"""
yt-dlp Media Retrieval Provider for Adoraxe.

Provides optional, authorized YouTube media retrieval for selected videos:
- Uses the official yt-dlp Python embedding API (yt_dlp.YoutubeDL).
- Enforces strict authorization: media is only retrieved when content ownership
  or reuse rights are explicitly confirmed (rights_confirmed=True).
- Strictly complies with platform and assignment restrictions:
  * No browser cookies, exported cookies, or --cookies-from-browser.
  * No CAPTCHA or anti-bot challenge bypasses.
  * No private/login-gated video evasion or proxy rotation.
  * Stops immediately on 403, 429, bot challenges, or access restrictions.
- Format selection: 'bv*+ba/b' with automatic FFmpeg merging into MP4 container.
- Enforces 2 GB file-size limit.
- Verifies output with FFprobe before creating VideoAsset record.
- Integrates seamlessly with existing VideoAsset database lifecycle and FFmpeg 9:16 pipeline.
"""
import os
import uuid
import asyncio
import logging
import shutil
import subprocess
from typing import Optional, Tuple, Dict, Any
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.models import Video, VideoAsset, VideoStatus
from app.services.video.ffmpeg_service import probe_video, VideoProbeResult, FFMPEG_PATH, FFPROBE_PATH

logger = logging.getLogger(__name__)


# ─── Custom Exceptions & Error Categories ─────────────────────────────────────

class YtDlpRetrievalError(Exception):
    """Base exception for yt-dlp media retrieval."""
    def __init__(self, message: str, code: str = "RETRIEVAL_FAILED"):
        super().__init__(message)
        self.message = message
        self.code = code


class DownloadNotAuthorizedError(YtDlpRetrievalError):
    def __init__(self, message: str):
        super().__init__(message, code="DOWNLOAD_NOT_AUTHORIZED")


class YtDlpDisabledError(YtDlpRetrievalError):
    def __init__(self, message: str):
        super().__init__(message, code="YTDLP_DISABLED")


class VideoUnavailableError(YtDlpRetrievalError):
    def __init__(self, message: str):
        super().__init__(message, code="VIDEO_UNAVAILABLE")


class PrivateVideoError(YtDlpRetrievalError):
    def __init__(self, message: str):
        super().__init__(message, code="PRIVATE_VIDEO")


class AccessRestrictedError(YtDlpRetrievalError):
    def __init__(self, message: str):
        super().__init__(message, code="ACCESS_RESTRICTED")


class BotChallengeError(YtDlpRetrievalError):
    def __init__(self, message: str):
        super().__init__(message, code="BOT_CHALLENGE")


class RateLimitedError(YtDlpRetrievalError):
    def __init__(self, message: str):
        super().__init__(message, code="RATE_LIMITED")


class FormatUnavailableError(YtDlpRetrievalError):
    def __init__(self, message: str):
        super().__init__(message, code="FORMAT_UNAVAILABLE")


class NetworkTimeoutError(YtDlpRetrievalError):
    def __init__(self, message: str):
        super().__init__(message, code="NETWORK_TIMEOUT")


class FFmpegMissingError(YtDlpRetrievalError):
    def __init__(self, message: str):
        super().__init__(message, code="FFMPEG_MISSING")


class InvalidMediaError(YtDlpRetrievalError):
    def __init__(self, message: str):
        super().__init__(message, code="INVALID_MEDIA")


class FileSizeLimitExceededError(YtDlpRetrievalError):
    def __init__(self, message: str):
        super().__init__(message, code="FILE_SIZE_LIMIT_EXCEEDED")


class JsRuntimeUnsupportedError(YtDlpRetrievalError):
    def __init__(self, message: str):
        super().__init__(message, code="JS_RUNTIME_UNSUPPORTED")


# ─── Runtime Dependency Inspection ───────────────────────────────────────────

def get_js_runtime_details() -> Dict[str, Any]:
    """Inspect and return structured JavaScript runtime info as detected by yt-dlp."""
    try:
        from yt_dlp.utils._jsruntime import NodeJsRuntime, DenoJsRuntime, BunJsRuntime, QuickJsRuntime
        for runtime_cls in (NodeJsRuntime, DenoJsRuntime, BunJsRuntime, QuickJsRuntime):
            try:
                rt = runtime_cls()
                info = rt.info
                if info:
                    status_str = "supported" if info.supported else "unsupported"
                    return {
                        "name": info.name,
                        "version": info.version,
                        "supported": bool(info.supported),
                        "path": info.path,
                        "summary": f"{info.name} v{info.version} ({status_str})",
                    }
            except Exception:
                continue
    except Exception:
        pass

    node_path = shutil.which("node")
    if node_path:
        try:
            res = subprocess.run([node_path, "-v"], capture_output=True, text=True, timeout=5)
            ver = res.stdout.strip()
            major = 0
            if ver.startswith("v"):
                try:
                    major = int(ver[1:].split(".")[0])
                except Exception:
                    pass
            is_supported = major >= 22
            status_str = "supported" if is_supported else "unsupported"
            return {
                "name": "node",
                "version": ver.lstrip("v"),
                "supported": is_supported,
                "path": node_path,
                "summary": f"node {ver} ({status_str})",
            }
        except Exception:
            return {
                "name": "node",
                "version": "unknown",
                "supported": False,
                "path": node_path,
                "summary": "node (unknown)",
            }

    return {
        "name": None,
        "version": None,
        "supported": False,
        "path": None,
        "summary": None,
    }


def get_js_runtime_info() -> Optional[str]:
    """Inspect and return available JavaScript runtime as detected by yt-dlp (Node.js or Deno)."""
    details = get_js_runtime_details()
    return details.get("summary")


def get_configured_js_runtimes() -> Dict[str, Dict[str, Any]]:
    """Build dictionary of available JavaScript runtimes for yt-dlp's js_runtimes option."""
    runtimes: Dict[str, Dict[str, Any]] = {}
    if shutil.which("node"):
        runtimes["node"] = {}
    if shutil.which("deno"):
        runtimes["deno"] = {}
    if shutil.which("bun"):
        runtimes["bun"] = {}
    return runtimes or {"node": {}}


def check_runtime_dependencies() -> Dict[str, Any]:
    """Inspect and return system environment details for yt-dlp."""
    ytdlp_available = False
    ytdlp_version = None
    try:
        import yt_dlp
        import yt_dlp.version
        ytdlp_available = True
        ytdlp_version = yt_dlp.version.__version__
    except ImportError:
        pass

    ejs_available = False
    ejs_version = None
    try:
        from yt_dlp.dependencies import yt_dlp_ejs
        if yt_dlp_ejs:
            ejs_available = True
            try:
                import importlib.metadata
                ejs_version = importlib.metadata.version("yt-dlp-ejs")
            except Exception:
                ejs_version = getattr(yt_dlp_ejs, "__version__", "0.8.0")
    except ImportError:
        pass

    ffmpeg_bin = shutil.which("ffmpeg") or (FFMPEG_PATH if os.path.exists(FFMPEG_PATH) else None)
    ffprobe_bin = shutil.which("ffprobe") or (FFPROBE_PATH if os.path.exists(FFPROBE_PATH) else None)
    js_details = get_js_runtime_details()

    return {
        "enabled": getattr(settings, "ytdlp_enabled", False),
        "ytdlp_available": ytdlp_available,
        "ytdlp_version": ytdlp_version,
        "ytdlp_ejs_available": ejs_available,
        "ytdlp_ejs_version": ejs_version,
        "ffmpeg_available": bool(ffmpeg_bin),
        "ffmpeg_path": ffmpeg_bin,
        "ffprobe_available": bool(ffprobe_bin),
        "ffprobe_path": ffprobe_bin,
        "js_runtime": js_details.get("summary"),
        "js_runtime_name": js_details.get("name"),
        "js_runtime_version": js_details.get("version"),
        "js_runtime_supported": js_details.get("supported", False),
        "max_file_size_mb": getattr(settings, "ytdlp_max_file_size_mb", 2048),
    }


# ─── yt-dlp Media Retriever ───────────────────────────────────────────────────

class YtDlpMediaRetriever:
    """
    Dedicated media retriever using yt-dlp Python embedding API for authorized videos.
    """

    def __init__(self, temp_dir: Optional[str] = None):
        configured_dir = getattr(settings, "ytdlp_download_dir", None)
        if not configured_dir or not os.path.isabs(configured_dir):
            configured_dir = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "temp"))

        self.temp_dir = temp_dir or configured_dir
        os.makedirs(self.temp_dir, exist_ok=True)
        self.max_file_size_bytes = getattr(settings, "ytdlp_max_file_size_mb", 2048) * 1024 * 1024

    def check_existing_asset(self, video: Video) -> Optional[VideoAsset]:
        """
        Check if video already has an existing valid yt-dlp retrieved asset on disk.
        STRICT PROVENANCE CHECK:
        Only reuse an asset if it was specifically retrieved via yt-dlp for THIS exact YouTube video ID.
        Never reuse manual uploads (e.g. gemini_generated_video_*, manual_upload.mp4) or unrelated assets.
        """
        if not video or not video.assets:
            return None
        yt_id = (video.youtube_video_id or "").strip()
        if not yt_id:
            return None

        sorted_assets = sorted(
            video.assets,
            key=lambda a: getattr(a, "created_at", None) or 0,
            reverse=True,
        )
        for asset in sorted_assets:
            if not asset.file_path or not os.path.exists(asset.file_path) or not os.path.isfile(asset.file_path):
                continue

            filename = os.path.basename(asset.file_path)
            orig_name = asset.original_filename or ""

            # Strict exclusion: never reuse manual uploads or synthetic demo assets
            if (
                "manual_upload" in filename.lower()
                or "gemini_generated" in filename.lower()
                or "manual_upload" in orig_name.lower()
                or "gemini_generated" in orig_name.lower()
                or filename.startswith("manual_")
            ):
                continue

            # Provenance requirement: file path or original name must explicitly contain this exact youtube_video_id
            is_matching_yt_id = (
                f"_{yt_id}." in filename
                or f"_{yt_id}_" in filename
                or filename.endswith(f"_{yt_id}.mp4")
                or orig_name.startswith(yt_id)
                or orig_name == f"{yt_id}.mp4"
            )
            if not is_matching_yt_id:
                continue

            probe = probe_video(asset.file_path)
            if probe.is_valid:
                return asset

        return None

    def _classify_download_error(self, err_msg: str) -> YtDlpRetrievalError:
        """Map raw yt-dlp error string to specific error categories without bypass."""
        lower = err_msg.lower()

        if "unsupported javascript runtime" in lower or "unsupported js runtime" in lower or "javascript runtime is required" in lower:
            return JsRuntimeUnsupportedError(
                "yt-dlp requires a supported JavaScript runtime (Node.js >= 22.0.0 or Deno >= 2.3.0) to solve YouTube challenges. Please ensure Node.js 22+ is installed in the container environment."
            )

        if "private video" in lower or "this video is private" in lower:
            return PrivateVideoError("This video is private. Accessing private videos without owner authorization is restricted.")

        if "bot" in lower or "captcha" in lower or "unusual traffic" in lower or "automated queries" in lower:
            return BotChallengeError(
                "YouTube returned an anti-bot challenge (Sign in to confirm you're not a bot). In accordance with policy, automated CAPTCHA circumvention and cookie extraction are prohibited."
            )

        if (
            "format is not available" in lower
            or "no video formats found" in lower
            or "no compatible format" in lower
            or "format not available" in lower
            or "only storyboard formats" in lower
        ):
            return FormatUnavailableError(
                "Requested stream format is not available for this video without authentication or DRM."
            )

        if (
            "sign in" in lower
            or "login required" in lower
            or "members-only" in lower
            or "confirm your age" in lower
            or "age-restricted" in lower
            or "inappropriate for some users" in lower
            or "403" in lower
            or "forbidden" in lower
            or "geoblocked" in lower
            or "not available in your country" in lower
        ):
            return AccessRestrictedError(
                "Video is access-restricted (sign-in, age confirmation, or region restriction). Bypassing login or authentication is prohibited."
            )

        if "429" in lower or "too many requests" in lower or "rate limit" in lower:
            return RateLimitedError(
                "YouTube returned HTTP 429 (Rate Limited). Requests are throttled by the platform."
            )

        if "timed out" in lower or "connection refused" in lower or "network is unreachable" in lower:
            return NetworkTimeoutError(
                "Network connection timed out while contacting YouTube. Please try again later."
            )

        if (
            "video unavailable" in lower
            or "does not exist" in lower
            or "has been removed" in lower
            or "deleted" in lower
        ):
            return VideoUnavailableError("The requested YouTube video is unavailable or has been removed.")

        if "larger than max-filesize" in lower or "max-filesize" in lower:
            return FileSizeLimitExceededError(
                f"Video file exceeds maximum allowed size of {self.max_file_size_bytes // (1024*1024)} MB."
            )

        if "ffmpeg" in lower and ("not found" in lower or "installed" in lower):
            return FFmpegMissingError("FFmpeg was not found in PATH for stream merging.")

        return YtDlpRetrievalError(f"yt-dlp media acquisition failed: {err_msg}")

    def _run_ytdlp_download(self, url: str, ydl_opts: dict) -> Dict[str, Any]:
        """Synchronous worker running yt_dlp.YoutubeDL inside a thread pool."""
        try:
            import yt_dlp
        except ImportError:
            raise YtDlpDisabledError("The yt-dlp Python package is not installed.")

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                return info or {}
        except Exception as e:
            err_msg = str(e)
            raise self._classify_download_error(err_msg) from e

    async def retrieve_media_for_video(
        self,
        video: Video,
        db: Session,
        rights_confirmed: bool = False,
        reuse_existing: bool = True,
    ) -> Tuple[VideoAsset, VideoProbeResult, str]:
        """
        Acquire media for a selected video using yt-dlp Python API:
        1. Checks rights_confirmed: stops immediately if False.
        2. Checks YTDLP_ENABLED setting: stops if False.
        3. Verifies FFmpeg and FFprobe are available.
        4. Checks if an asset already exists on disk when reuse_existing=True.
        5. Downloads with format 'bv*+ba/b' and merges into MP4 via FFmpeg.
        6. Enforces 2GB limit.
        7. Validates with FFprobe.
        8. Creates VideoAsset record and commits to database.
        """
        # Step 1: Authorization check
        if not rights_confirmed:
            raise DownloadNotAuthorizedError(
                "Media retrieval requires explicit confirmation of content ownership or "
                "authorization to download and reuse this video. Please confirm authorization before proceeding."
            )

        # Step 2: Configuration check
        if not getattr(settings, "ytdlp_enabled", False):
            raise YtDlpDisabledError(
                "yt-dlp media retrieval is currently disabled in settings (YTDLP_ENABLED=false). "
                "To enable it, set YTDLP_ENABLED=true in .env, or use Method A (Direct URL) or Method B (Manual Upload)."
            )

        # Step 3: FFmpeg runtime check
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            raise FFmpegMissingError(
                "FFmpeg and FFprobe binaries are required for video/audio stream merging and validation."
            )

        # Step 4: Check existing asset
        if reuse_existing:
            existing_asset = self.check_existing_asset(video)
            if existing_asset:
                probe = probe_video(existing_asset.file_path)
                return existing_asset, probe, "REUSED"

        # Step 5: Canonical YouTube URL
        url = f"https://www.youtube.com/watch?v={video.youtube_video_id}"

        # Output template with UUID and YouTube video ID
        asset_id = str(uuid.uuid4())
        out_base = f"{asset_id}_{video.youtube_video_id}"
        outtmpl = os.path.join(self.temp_dir, f"{out_base}.%(ext)s")

        ydl_opts = {
            "format": "bv*+ba/b",
            "merge_output_format": "mp4",
            "outtmpl": outtmpl,
            "max_filesize": self.max_file_size_bytes,
            "quiet": True,
            "no_warnings": True,
            "nocheckcertificate": False,
            "noplaylist": True,
            "overwrites": False,  # Never overwrite unrelated existing files
            "extractor_args": {"youtube": {"player_client": ["android", "web"]}},
            # No cookies, no bypass flags, no proxy
        }
        configured_runtimes = get_configured_js_runtimes()
        if configured_runtimes:
            ydl_opts["js_runtimes"] = configured_runtimes

        # Step 6: Execute download in thread pool
        await asyncio.to_thread(self._run_ytdlp_download, url, ydl_opts)

        # Step 7: Locate output file
        expected_mp4 = os.path.join(self.temp_dir, f"{out_base}.mp4")
        target_path: Optional[str] = None

        if os.path.exists(expected_mp4) and os.path.getsize(expected_mp4) > 0:
            target_path = expected_mp4
        else:
            # Check for other merged extensions (e.g. .mkv, .mov)
            for fname in os.listdir(self.temp_dir):
                if fname.startswith(out_base) and not fname.endswith(".part"):
                    cand = os.path.join(self.temp_dir, fname)
                    if os.path.isfile(cand) and os.path.getsize(cand) > 0:
                        target_path = cand
                        break

        if not target_path or not os.path.exists(target_path):
            raise InvalidMediaError("yt-dlp download completed without producing a valid output file.")

        # Step 8: Enforce 2GB limit on final merged file
        file_size = os.path.getsize(target_path)
        if file_size > self.max_file_size_bytes:
            if os.path.exists(target_path):
                try:
                    os.remove(target_path)
                except Exception:
                    pass
            raise FileSizeLimitExceededError(
                f"Retrieved media file ({file_size / (1024*1024):.1f} MB) exceeds maximum allowed limit of 2 GB."
            )

        # Step 9: Validate with FFprobe
        probe = probe_video(target_path)
        if not probe.is_valid:
            if os.path.exists(target_path):
                try:
                    os.remove(target_path)
                except Exception:
                    pass
            raise InvalidMediaError(
                f"Retrieved media failed FFprobe validation: {probe.error or 'No valid video stream detected'}."
            )

        # Step 10: Create VideoAsset record in database
        asset = VideoAsset(
            id=asset_id,
            video_id=video.id,
            original_filename=os.path.basename(target_path),
            file_path=target_path,
            duration_seconds=int(probe.duration_seconds),
            width=probe.width,
            height=probe.height,
            codec=probe.codec,
            file_size_bytes=probe.file_size_bytes,
            is_processed=False,
            is_reel_compatible=False,
        )
        db.add(asset)

        if video.status == VideoStatus.FAILED:
            video.status = VideoStatus.IMPORTED

        db.commit()
        db.refresh(asset)

        return asset, probe, "DOWNLOADED"
