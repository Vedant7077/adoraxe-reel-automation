"""
Media Retrieval Service for Adoraxe.

Handles acquisition and local storage of authorized video media:
- Direct HTTP streaming chunked download for authorized remote media URLs (MP4, MOV, etc.).
- Robust validation: MIME type, file size limit (2GB), timeouts, bounded retries, and FFprobe stream verification.
- Enforces strict YouTube access compliance:
  * YouTube watch URLs are recognized as web pages, not direct media.
  * Rejects bypassing YouTube login, CAPTCHA, DRM, anti-bot gates, or scraping arbitrary videos without authorization.
  * Reports MEDIA_NOT_AVAILABLE with clear instructions to provide direct media URL or use manual upload.
- Seamlessly integrates with FFprobe, VideoAsset lifecycle, and FFmpeg 9:16 Reel conversion.
"""
import os
import uuid
import asyncio
import logging
from typing import Optional, Tuple
from urllib.parse import urlparse, urljoin
import httpx
from sqlalchemy.orm import Session

from app.models.models import Video, VideoAsset, VideoStatus
from app.services.video.ffmpeg_service import probe_video, VideoProbeResult
from app.services.video.security import validate_remote_url, SecuritySSRFError

logger = logging.getLogger(__name__)


# ─── Custom Exceptions ────────────────────────────────────────────────────────

class MediaRetrievalError(Exception):
    """Base exception for media retrieval errors."""
    pass


class MediaNotAvailableError(MediaRetrievalError):
    """Raised when media cannot be retrieved from an authorized source or a YouTube watch URL is used directly."""
    pass


class MediaValidationError(MediaRetrievalError):
    """Raised when downloaded media fails format, codec, or video stream validation."""
    pass


class MediaSizeLimitExceededError(MediaRetrievalError):
    """Raised when remote media exceeds the maximum allowed file size."""
    pass


# ─── YouTube URL Detection ────────────────────────────────────────────────────

def is_youtube_watch_url(url: Optional[str]) -> bool:
    """
    Check if a URL is a YouTube watch page, shorts, channel, or web interface URL
    rather than a direct media file.
    """
    if not url:
        return False
    lower = url.lower().strip()
    watch_patterns = [
        "youtube.com/watch",
        "youtu.be/",
        "youtube.com/shorts/",
        "youtube.com/channel/",
        "youtube.com/@",
        "youtube.com/embed/",
        "youtube.com/v/",
        "m.youtube.com/watch",
        "m.youtube.com/shorts",
    ]
    return any(pattern in lower for pattern in watch_patterns)


# ─── Media Retrieval Service ──────────────────────────────────────────────────

class MediaRetrievalService:
    """
    Modular service to acquire and store authorized video media.
    """
    CHUNK_SIZE = 128 * 1024  # 128 KB chunks
    MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2 GB max
    MAX_REDIRECTS = 3
    MAX_DOWNLOAD_DURATION_SECONDS = 120.0
    CONNECT_TIMEOUT_SECONDS = 10.0
    READ_TIMEOUT_SECONDS = 60.0

    ALLOWED_CONTENT_TYPES = {
        "video/mp4",
        "video/quicktime",
        "video/x-matroska",
        "video/webm",
        "video/x-msvideo",
        "application/octet-stream",
        "application/mp4",
        "binary/octet-stream",
    }
    ALLOWED_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi"}

    def __init__(self, temp_dir: Optional[str] = None):
        self.temp_dir = temp_dir or os.path.realpath(
            os.path.join(os.path.dirname(__file__), "..", "..", "..", "temp")
        )
        os.makedirs(self.temp_dir, exist_ok=True)

    def check_existing_asset(self, video: Video) -> Optional[VideoAsset]:
        """
        Check if a video record already has an existing valid original video file on disk.
        """
        if not video or not video.assets:
            return None
        # Sort newest first
        sorted_assets = sorted(
            video.assets,
            key=lambda a: getattr(a, "created_at", None) or 0,
            reverse=True,
        )
        for asset in sorted_assets:
            if asset.file_path and os.path.exists(asset.file_path) and os.path.isfile(asset.file_path):
                probe = probe_video(asset.file_path)
                if probe.is_valid:
                    return asset
        return None

    async def retrieve_from_url(
        self,
        url: str,
        asset_id: str,
        filename_hint: Optional[str] = None,
        max_retries: int = 2,
    ) -> str:
        """
        Stream download remote media in chunks with bounded retries, timeouts,
        and SSRF defense (validating host IPs and redirect destinations).
        """
        clean_url = url.strip()

        # Enforce YouTube policy
        if is_youtube_watch_url(clean_url):
            raise MediaNotAvailableError(
                "YouTube watch-page URLs (e.g. youtube.com/watch, youtu.be) are HTML web pages, "
                "not direct playable media streams. Directly downloading arbitrary YouTube videos without "
                "content-owner authorization or by bypassing access controls is prohibited. "
                "Please provide an authorized direct MP4/MOV media URL (e.g. from your media server, CDN, or S3/GCS bucket) "
                "or upload the video file directly using the manual upload option below."
            )

        # Enforce SSRF security validation on initial URL
        validate_remote_url(clean_url)

        # Derive safe filename and extension
        parsed = urlparse(clean_url)
        path_ext = os.path.splitext(parsed.path)[1].lower()
        safe_ext = path_ext if path_ext in self.ALLOWED_EXTENSIONS else ".mp4"

        base_name = filename_hint or f"{asset_id}{safe_ext}"
        if not any(base_name.lower().endswith(ext) for ext in self.ALLOWED_EXTENSIONS):
            base_name += safe_ext

        # Clean filename characters
        safe_base_name = "".join(c for c in base_name if c.isalnum() or c in "._- ")
        final_path = os.path.join(self.temp_dir, f"{asset_id}_{safe_base_name}")
        part_path = os.path.join(self.temp_dir, f"{asset_id}_download.part")

        last_error: Optional[Exception] = None
        loop = asyncio.get_event_loop()
        start_time = loop.time()

        for attempt in range(max_retries + 1):
            if os.path.exists(part_path):
                try:
                    os.remove(part_path)
                except Exception:
                    pass

            try:
                timeout = httpx.Timeout(
                    connect=self.CONNECT_TIMEOUT_SECONDS,
                    read=self.READ_TIMEOUT_SECONDS,
                    write=30.0,
                    pool=10.0,
                )
                headers = {
                    "User-Agent": "AdoraxeMediaRetrieval/2.0 (Video Processing Pipeline)",
                    "Accept": "*/*",
                }

                current_url = clean_url
                redirect_count = 0

                async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, headers=headers) as client:
                    while True:
                        # Validate before each request (including redirects)
                        validate_remote_url(current_url)

                        if loop.time() - start_time > self.MAX_DOWNLOAD_DURATION_SECONDS:
                            raise MediaRetrievalError(
                                f"Media download exceeded maximum allowed duration of {self.MAX_DOWNLOAD_DURATION_SECONDS}s."
                            )

                        async with client.stream("GET", current_url) as response:
                            # Handle redirect safely
                            if response.status_code in (301, 302, 303, 307, 308):
                                redirect_count += 1
                                if redirect_count > self.MAX_REDIRECTS:
                                    raise MediaRetrievalError(
                                        f"Too many redirects (exceeded maximum of {self.MAX_REDIRECTS})."
                                    )
                                location = response.headers.get("location")
                                if not location:
                                    raise MediaRetrievalError("Redirect response missing Location header.")

                                next_url = urljoin(current_url, location)
                                # Validate redirect destination against SSRF rules BEFORE following
                                validate_remote_url(next_url)
                                current_url = next_url
                                continue

                            if response.status_code == 403:
                                raise MediaRetrievalError(
                                    "Access forbidden (HTTP 403). The media URL may have expired or requires authentication."
                                )
                            if response.status_code == 404:
                                raise MediaRetrievalError(
                                    "Media not found (HTTP 404). Please verify that the media URL is correct and accessible."
                                )
                            if response.status_code == 429:
                                raise MediaRetrievalError(
                                    "Rate limit exceeded (HTTP 429). The remote media provider is throttling requests."
                                )
                            if response.status_code >= 400:
                                raise MediaRetrievalError(
                                    f"Remote media server returned HTTP error {response.status_code}."
                                )

                            # Content-Type check
                            ct_header = response.headers.get("content-type", "")
                            content_type = ct_header.split(";")[0].strip().lower()

                            if content_type:
                                if "text/html" in content_type or "application/json" in content_type:
                                    raise MediaValidationError(
                                        f"Remote URL returned non-video content type '{content_type}' (expected video/mp4, video/quicktime, etc.)."
                                    )
                                if not (content_type in self.ALLOWED_CONTENT_TYPES or content_type.startswith("video/")):
                                    logger.warning(f"Unusual content-type for media: {content_type}; will verify with FFprobe.")

                            # Content-Length check
                            cl_header = response.headers.get("content-length")
                            if cl_header:
                                try:
                                    cl_bytes = int(cl_header)
                                    if cl_bytes > self.MAX_FILE_SIZE:
                                        raise MediaSizeLimitExceededError(
                                            f"Remote file size ({cl_bytes / (1024*1024):.1f} MB) exceeds maximum allowed limit of 2 GB."
                                        )
                                except ValueError:
                                    pass

                            # Stream in chunks to part file
                            bytes_written = 0
                            with open(part_path, "wb") as f:
                                async for chunk in response.aiter_bytes(chunk_size=self.CHUNK_SIZE):
                                    if loop.time() - start_time > self.MAX_DOWNLOAD_DURATION_SECONDS:
                                        raise MediaRetrievalError(
                                            f"Media download exceeded maximum allowed duration of {self.MAX_DOWNLOAD_DURATION_SECONDS}s."
                                        )
                                    bytes_written += len(chunk)
                                    if bytes_written > self.MAX_FILE_SIZE:
                                        raise MediaSizeLimitExceededError(
                                            f"Download exceeded maximum allowed file size of 2 GB."
                                        )
                                    f.write(chunk)

                            # Stream finished successfully
                            break

                # Atomically replace final file
                if os.path.exists(final_path):
                    os.remove(final_path)
                os.rename(part_path, final_path)
                last_error = None
                break

            except (SecuritySSRFError, MediaNotAvailableError, MediaValidationError, MediaSizeLimitExceededError):
                if os.path.exists(part_path):
                    try:
                        os.remove(part_path)
                    except Exception:
                        pass
                raise

            except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError, httpx.RemoteProtocolError) as e:
                last_error = e
                logger.warning(f"Transient error on download attempt {attempt + 1}: {e}")
                if os.path.exists(part_path):
                    try:
                        os.remove(part_path)
                    except Exception:
                        pass
                if attempt < max_retries:
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue

            except Exception as e:
                last_error = e
                if os.path.exists(part_path):
                    try:
                        os.remove(part_path)
                    except Exception:
                        pass
                break

        if last_error:
            raise MediaRetrievalError(f"Failed to retrieve media from remote URL: {str(last_error)}")

        return final_path

    async def retrieve_media_for_video(
        self,
        video: Video,
        media_url: Optional[str] = None,
        db: Session = None,
        reuse_existing: bool = True,
        filename_hint: Optional[str] = None,
    ) -> Tuple[VideoAsset, VideoProbeResult, str]:
        """
        Acquire media for a Video record:
        1. If reuse_existing=True and a valid original asset exists on disk, returns it immediately.
        2. Determines authorized target URL: explicitly supplied media_url, or video.video_url if valid.
        3. Rejects YouTube watch pages with MediaNotAvailableError.
        4. Downloads media in chunks, probes with FFprobe, saves VideoAsset to DB.
        5. Returns (asset, probe_result, status_type).
        """
        # Step 1: Check existing asset on disk
        if reuse_existing and not media_url:
            existing_asset = self.check_existing_asset(video)
            if existing_asset:
                probe = probe_video(existing_asset.file_path)
                return existing_asset, probe, "REUSED"

        # Step 2: Determine URL to fetch
        target_url = (media_url or "").strip()
        if not target_url:
            candidate = (video.video_url or "").strip()
            if candidate and not is_youtube_watch_url(candidate) and (candidate.startswith("http://") or candidate.startswith("https://")):
                target_url = candidate
            else:
                # No direct media URL supplied and candidate is YouTube watch URL
                raise MediaNotAvailableError(
                    "YouTube watch-page URLs (youtube.com/watch, youtu.be, etc.) are HTML web pages, not direct playable media streams. "
                    "Directly downloading arbitrary YouTube videos without content-owner authorization or by bypassing access controls is prohibited. "
                    "Please provide an authorized direct MP4/MOV media URL or upload your video file directly using the manual upload option below."
                )

        # Step 3: Stream download
        asset_id = str(uuid.uuid4())
        hint = filename_hint or f"{video.youtube_video_id}.mp4"
        file_path = await self.retrieve_from_url(target_url, asset_id, filename_hint=hint)

        # Step 4: Validate with FFprobe
        probe = probe_video(file_path)
        if not probe.is_valid:
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except Exception:
                    pass
            raise MediaValidationError(
                f"Downloaded media file failed FFprobe validation: {probe.error or 'No valid video stream detected'}."
            )

        # Step 5: Create or update VideoAsset record in database
        asset = VideoAsset(
            id=asset_id,
            video_id=video.id,
            original_filename=os.path.basename(file_path),
            file_path=file_path,
            duration_seconds=int(probe.duration_seconds),
            width=probe.width,
            height=probe.height,
            codec=probe.codec,
            file_size_bytes=probe.file_size_bytes,
            is_processed=False,
            is_reel_compatible=False,
        )
        db.add(asset)

        # Keep video status as IMPORTED or READY for processing
        if video.status == VideoStatus.FAILED:
            video.status = VideoStatus.IMPORTED

        db.commit()
        db.refresh(asset)

        return asset, probe, "RETRIEVED"
