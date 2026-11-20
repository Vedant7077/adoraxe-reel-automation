"""
Videos API routes.
"""
import os
import uuid
from datetime import datetime
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, BackgroundTasks, status, Response
from starlette.responses import FileResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.models import Video, VideoAsset, VideoStatus, JobStatus
from app.schemas.schemas import (
    VideoResponse,
    VideoListResponse,
    VideoUploadResponse,
    VideoAssetResponse,
    ProcessVideoRequest,
    JobStatusResponse,
    MediaRetrieveRequest,
    MediaRetrieveResponse,
    YtDlpStatusResponse,
)
from app.repositories.video_repository import VideoRepository
from app.repositories.job_repository import JobRepository
from app.services.video.processor import VideoProcessor
from app.services.video.ffmpeg_service import probe_video
from app.services.cloudinary.cloudinary_service import CloudinaryService
from app.services.video.media_retrieval_service import (
    MediaRetrievalService,
    MediaRetrievalError,
    MediaNotAvailableError,
    MediaValidationError,
    MediaSizeLimitExceededError,
)
from app.services.youtube.ytdlp_media_retriever import (
    YtDlpMediaRetriever,
    check_runtime_dependencies,
    YtDlpRetrievalError,
    DownloadNotAuthorizedError,
    YtDlpDisabledError,
    VideoUnavailableError,
    PrivateVideoError,
    AccessRestrictedError,
    BotChallengeError,
    FormatUnavailableError,
    RateLimitedError,
    NetworkTimeoutError,
    FFmpegMissingError,
    InvalidMediaError,
    FileSizeLimitExceededError,
    JsRuntimeUnsupportedError,
)
from app.services.video.security import SecuritySSRFError
from app.core.config import settings

router = APIRouter()

TEMP_DIR = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "temp"))
os.makedirs(TEMP_DIR, exist_ok=True)

ALLOWED_VIDEO_TYPES = {"video/mp4", "video/quicktime", "video/x-msvideo", "video/webm", "video/x-matroska"}
MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2GB


def _resolve_processed_path(asset: VideoAsset) -> Optional[str]:
    """Helper to find the local processed reel file path if present."""
    if asset.cloudinary_url:
        if asset.cloudinary_url.startswith("file://"):
            candidate = asset.cloudinary_url[7:]
            if os.path.exists(candidate) and os.path.isfile(candidate):
                return os.path.realpath(candidate)
    if asset.file_path:
        candidate = asset.file_path.replace(".mp4", "_reel.mp4").replace(".mov", "_reel.mp4")
        if not candidate.endswith("_reel.mp4"):
            candidate = asset.file_path + "_reel.mp4"
        if os.path.exists(candidate) and os.path.isfile(candidate):
            return os.path.realpath(candidate)
    return None


def _build_video_response(video: Video) -> VideoResponse:
    """Build rich VideoResponse containing asset statuses and stream URLs."""
    asset_responses: List[VideoAssetResponse] = []
    has_uploaded = False
    has_processed = False
    orig_url: Optional[str] = None
    proc_url: Optional[str] = None
    proc_crop_mode: Optional[str] = None
    active_asset_resp: Optional[VideoAssetResponse] = None

    sorted_assets = sorted(video.assets, key=lambda a: a.created_at or datetime.min, reverse=True)

    # Distinctly identify latest original and processed assets
    orig_asset = next((a for a in sorted_assets if not a.is_processed), None)
    proc_asset = next((a for a in sorted_assets if a.is_processed), None)

    # Fallback for legacy single-asset records where an asset with local file_path was marked processed
    if not orig_asset:
        orig_fallback = next(
            (a for a in sorted_assets if a.file_path and os.path.exists(a.file_path) and os.path.isfile(a.file_path)),
            None,
        )
        if orig_fallback:
            orig_asset = orig_fallback

    if not proc_asset:
        proc_fallback = next((a for a in sorted_assets if _resolve_processed_path(a) is not None), None)
        if proc_fallback:
            proc_asset = proc_fallback

    orig_has_cld = bool(
        orig_asset
        and not orig_asset.is_processed
        and orig_asset.cloudinary_url
        and (orig_asset.cloudinary_url.startswith("http://") or orig_asset.cloudinary_url.startswith("https://"))
    )
    orig_file_exists = bool(
        orig_asset
        and orig_asset.file_path
        and os.path.exists(orig_asset.file_path)
        and os.path.isfile(orig_asset.file_path)
    )
    if orig_has_cld or orig_file_exists:
        has_uploaded = True
        # Prefer direct verified Cloudinary HTTPS URL for HTML5 playback without 401; fallback to local API stream
        orig_url = orig_asset.cloudinary_url if orig_has_cld else f"/api/v1/videos/{video.id}/stream/original"

    proc_has_cld = bool(
        proc_asset
        and proc_asset.cloudinary_url
        and (proc_asset.cloudinary_url.startswith("http://") or proc_asset.cloudinary_url.startswith("https://"))
    )
    proc_file_exists = bool(proc_asset and _resolve_processed_path(proc_asset))
    if proc_has_cld or proc_file_exists:
        has_processed = True
        # Prefer direct verified Cloudinary HTTPS URL for HTML5 playback without 401; fallback to local API stream
        proc_url = proc_asset.cloudinary_url if proc_has_cld else f"/api/v1/videos/{video.id}/stream/processed"
        if proc_asset and proc_asset.crop_mode:
            proc_crop_mode = proc_asset.crop_mode

    for asset in sorted_assets:
        is_orig = not asset.is_processed
        asset_has_cld = bool(
            asset.cloudinary_url
            and (asset.cloudinary_url.startswith("http://") or asset.cloudinary_url.startswith("https://"))
        )
        orig_exists = bool(
            (asset.file_path and os.path.exists(asset.file_path) and os.path.isfile(asset.file_path))
            or (asset_has_cld and is_orig)
        )
        proc_exists = bool(
            (asset_has_cld and asset.is_processed)
            or (_resolve_processed_path(asset) is not None)
        )

        orig_stream = (
            asset.cloudinary_url if (asset_has_cld and is_orig)
            else (f"/api/v1/videos/{video.id}/stream/original" if orig_exists else None)
        )
        proc_stream = (
            asset.cloudinary_url if (asset_has_cld and asset.is_processed)
            else (f"/api/v1/videos/{video.id}/stream/processed" if proc_exists else None)
        )

        resp_asset = VideoAssetResponse(
            id=asset.id,
            video_id=asset.video_id,
            original_filename=asset.original_filename,
            duration_seconds=asset.duration_seconds,
            width=asset.width,
            height=asset.height,
            codec=asset.codec,
            file_size_bytes=asset.file_size_bytes,
            is_processed=asset.is_processed,
            is_reel_compatible=asset.is_reel_compatible,
            crop_mode=asset.crop_mode,
            processing_error=asset.processing_error,
            has_original_file=orig_exists,
            has_processed_file=proc_exists,
            original_stream_url=orig_stream,
            processed_stream_url=proc_stream,
            cloudinary_url=asset.cloudinary_url,
            created_at=asset.created_at,
            updated_at=asset.updated_at,
        )
        asset_responses.append(resp_asset)
        if not active_asset_resp:
            active_asset_resp = resp_asset

    # If any asset is processed, video has processed reel
    if any(a.is_processed for a in sorted_assets) or proc_url:
        has_processed = True
        if not proc_url:
            proc_url = f"/api/v1/videos/{video.id}/stream/processed"
    if any(not a.is_processed for a in sorted_assets) or orig_url:
        has_uploaded = True
        if not orig_url:
            orig_url = f"/api/v1/videos/{video.id}/stream/original"

    # Identify any active in-flight processing job for this video
    active_job_id: Optional[str] = None
    is_proc_job = False
    if hasattr(video, "publishing_jobs") and video.publishing_jobs:
        active_proc_jobs = [
            j for j in video.publishing_jobs
            if j.instagram_account_id is None
            and str(j.status) in (JobStatus.PENDING.value, JobStatus.PROCESSING.value, "PENDING", "PROCESSING")
        ]
        if active_proc_jobs:
            latest_proc = sorted(active_proc_jobs, key=lambda j: j.updated_at or datetime.min, reverse=True)[0]
            active_job_id = latest_proc.id
            is_proc_job = True

    return VideoResponse(
        id=video.id,
        youtube_video_id=video.youtube_video_id,
        title=video.title,
        description=video.description,
        thumbnail_url=video.thumbnail_url,
        duration_seconds=video.duration_seconds,
        view_count=video.view_count,
        video_url=video.video_url,
        channel_name=video.channel_name,
        status=video.status,
        assets=asset_responses,
        has_uploaded_video=has_uploaded,
        has_processed_reel=has_processed,
        crop_mode=proc_crop_mode,
        original_video_url=orig_url,
        processed_video_url=proc_url,
        active_asset=active_asset_resp,
        active_job_id=active_job_id,
        is_processing=is_proc_job,
        uploaded_at=video.uploaded_at,
        created_at=video.created_at,
    )


@router.get("", response_model=VideoListResponse)
async def list_videos(
    page: int = 1,
    page_size: int = 24,
    status: str = None,
    search: str = None,
    db: Session = Depends(get_db),
):
    """List all videos with optional filtering."""
    repo = VideoRepository(db)
    items, total = repo.get_all(page=page, page_size=page_size, status=status, search=search)
    response_items = [_build_video_response(v) for v in items]
    total_pages = (total + page_size - 1) // page_size if total > 0 else 0
    return VideoListResponse(items=response_items, total=total, page=page, page_size=page_size, total_pages=total_pages)


@router.get("/ytdlp/status", response_model=YtDlpStatusResponse)
async def get_ytdlp_status(response: Response):
    """Get runtime status, dependency health, and capabilities for yt-dlp media retrieval."""
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    deps = check_runtime_dependencies()
    return YtDlpStatusResponse(
        enabled=deps["enabled"],
        ytdlp_available=deps["ytdlp_available"],
        ytdlp_version=deps["ytdlp_version"],
        ytdlp_ejs_available=deps["ytdlp_ejs_available"],
        ytdlp_ejs_version=deps["ytdlp_ejs_version"],
        ffmpeg_available=deps["ffmpeg_available"],
        ffmpeg_path=deps["ffmpeg_path"],
        ffprobe_available=deps["ffprobe_available"],
        ffprobe_path=deps["ffprobe_path"],
        js_runtime=deps["js_runtime"],
        js_runtime_supported=deps.get("js_runtime_supported", False),
        js_runtime_name=deps.get("js_runtime_name"),
        js_runtime_version=deps.get("js_runtime_version"),
        max_file_size_mb=deps["max_file_size_mb"],
    )


@router.get("/{video_id}", response_model=VideoResponse)
async def get_video(video_id: str, db: Session = Depends(get_db)):
    """Get a specific video by ID with full preview metadata."""
    repo = VideoRepository(db)
    video = repo.get_by_id(video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")
    return _build_video_response(video)


@router.api_route("/{video_id}/stream/{variant}", methods=["GET", "HEAD"])
async def stream_video_by_variant(
    video_id: str,
    variant: str,
    db: Session = Depends(get_db),
):
    """
    Stream video file (original or processed reel) with Range request seeking support.
    variant: 'original' or 'processed' (or 'reel')
    """
    variant_norm = variant.lower().strip()
    if variant_norm not in ("original", "processed", "reel"):
        raise HTTPException(status_code=400, detail="Invalid variant. Must be 'original' or 'processed'.")

    repo = VideoRepository(db)
    video = repo.get_by_id(video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    if not video.assets:
        raise HTTPException(status_code=404, detail="No video file has been uploaded for this video yet.")

    asset = sorted(video.assets, key=lambda a: a.created_at or datetime.min, reverse=True)[0]

    NO_CACHE_HEADERS = {
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
        "Expires": "0",
    }

    if variant_norm == "original":
        orig_asset = repo.get_original_asset(video_id) or asset
        if orig_asset.cloudinary_url and (orig_asset.cloudinary_url.startswith("http://") or orig_asset.cloudinary_url.startswith("https://")):
            return RedirectResponse(
                url=orig_asset.cloudinary_url,
                status_code=status.HTTP_307_TEMPORARY_REDIRECT,
                headers=NO_CACHE_HEADERS,
            )

        if not orig_asset.file_path or not os.path.exists(orig_asset.file_path):
            raise HTTPException(status_code=404, detail="Original video file not found on server disk or Cloudinary. Please re-upload.")
        real_path = os.path.realpath(orig_asset.file_path)
        if not os.path.isfile(real_path):
            raise HTTPException(status_code=404, detail="Original video path is not a file.")
        return FileResponse(
            path=real_path,
            media_type="video/mp4",
            filename=orig_asset.original_filename or f"{video_id}_original.mp4",
            content_disposition_type="inline",
            headers=NO_CACHE_HEADERS,
        )

    # variant is processed or reel
    proc_asset = repo.get_processed_asset(video_id) or asset
    if proc_asset.cloudinary_url:
        if proc_asset.cloudinary_url.startswith("http://") or proc_asset.cloudinary_url.startswith("https://"):
            return RedirectResponse(
                url=proc_asset.cloudinary_url,
                status_code=status.HTTP_307_TEMPORARY_REDIRECT,
                headers=NO_CACHE_HEADERS,
            )
        elif proc_asset.cloudinary_url.startswith("file://"):
            candidate = os.path.realpath(proc_asset.cloudinary_url[7:])
            if os.path.exists(candidate) and os.path.isfile(candidate):
                return FileResponse(
                    path=candidate,
                    media_type="video/mp4",
                    filename=f"{video_id}_reel_1080x1920.mp4",
                    content_disposition_type="inline",
                    headers=NO_CACHE_HEADERS,
                )

    proc_path = _resolve_processed_path(proc_asset)
    if proc_path and os.path.exists(proc_path) and os.path.isfile(proc_path):
        return FileResponse(
            path=proc_path,
            media_type="video/mp4",
            filename=f"{video_id}_reel_1080x1920.mp4",
            content_disposition_type="inline",
            headers=NO_CACHE_HEADERS,
        )

    raise HTTPException(
        status_code=404,
        detail="Processed Reel video not found. Please click 'Process as Reel' to generate the 9:16 vertical video."
    )


@router.api_route("/{video_id}/assets/{asset_id}/stream", methods=["GET", "HEAD"])
async def stream_asset(
    video_id: str,
    asset_id: str,
    variant: str = "original",
    db: Session = Depends(get_db),
):
    """Stream video file for a specific asset ID with Range request seeking support."""
    repo = VideoRepository(db)
    video = repo.get_by_id(video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    matching = [a for a in video.assets if a.id == asset_id]
    if not matching:
        raise HTTPException(status_code=404, detail="Asset not found for this video")
    asset = matching[0]

    NO_CACHE_HEADERS = {
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
        "Expires": "0",
    }

    variant_norm = variant.lower().strip()
    if variant_norm == "original":
        if asset.cloudinary_url and (asset.cloudinary_url.startswith("http://") or asset.cloudinary_url.startswith("https://")) and not asset.is_processed:
            return RedirectResponse(
                url=asset.cloudinary_url,
                status_code=status.HTTP_307_TEMPORARY_REDIRECT,
                headers=NO_CACHE_HEADERS,
            )
        if not asset.file_path or not os.path.exists(asset.file_path):
            raise HTTPException(status_code=404, detail="Original video file not found on server disk or Cloudinary.")
        real_path = os.path.realpath(asset.file_path)
        return FileResponse(
            path=real_path,
            media_type="video/mp4",
            filename=asset.original_filename or f"{asset_id}_original.mp4",
            content_disposition_type="inline",
            headers=NO_CACHE_HEADERS,
        )

    # Processed
    if asset.cloudinary_url:
        if asset.cloudinary_url.startswith("http://") or asset.cloudinary_url.startswith("https://"):
            return RedirectResponse(
                url=asset.cloudinary_url,
                status_code=status.HTTP_307_TEMPORARY_REDIRECT,
                headers=NO_CACHE_HEADERS,
            )
        elif asset.cloudinary_url.startswith("file://"):
            candidate = os.path.realpath(asset.cloudinary_url[7:])
            if os.path.exists(candidate) and os.path.isfile(candidate):
                return FileResponse(
                    path=candidate,
                    media_type="video/mp4",
                    filename=f"{asset_id}_reel_1080x1920.mp4",
                    content_disposition_type="inline",
                    headers=NO_CACHE_HEADERS,
                )

    proc_path = _resolve_processed_path(asset)
    if proc_path and os.path.exists(proc_path) and os.path.isfile(proc_path):
        return FileResponse(
            path=proc_path,
            media_type="video/mp4",
            filename=f"{asset_id}_reel_1080x1920.mp4",
            content_disposition_type="inline",
            headers=NO_CACHE_HEADERS,
        )

    raise HTTPException(status_code=404, detail="Processed Reel video not found for this asset.")


@router.post("/upload", response_model=VideoUploadResponse)
async def upload_video(
    file: UploadFile = File(...),
    video_id: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    """Upload an authorized video file for processing."""
    if file.content_type not in ALLOWED_VIDEO_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {file.content_type}. Allowed: mp4, mov, avi, webm, mkv"
        )

    asset_id = str(uuid.uuid4())
    file_path = os.path.join(TEMP_DIR, f"{asset_id}_{file.filename}")

    try:
        content = await file.read()
        if len(content) > MAX_FILE_SIZE:
            raise HTTPException(status_code=413, detail="File too large. Maximum 2GB allowed.")

        with open(file_path, "wb") as f:
            f.write(content)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")

    # Step 3: Run FFprobe and validate that a playable video stream exists
    probe = probe_video(file_path)
    if not probe.is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid video file: {probe.error or 'No playable video stream detected'}. Please upload a valid MP4, MOV, AVI, or WebM video file.",
        )

    repo = VideoRepository(db)

    # If no video_id provided, create a parent video record
    if not video_id:
        new_video = Video(
            youtube_video_id=f"UPLOAD_{uuid.uuid4().hex[:8]}",
            title=file.filename or "Uploaded Video",
            video_url=f"file://{file_path}",
            status=VideoStatus.IMPORTED,
        )
        db.add(new_video)
        db.commit()
        db.refresh(new_video)
        video_id = new_video.id

    # Step 4: Upload original video to Cloudinary using existing CloudinaryService
    cdn = CloudinaryService()
    cld_url: Optional[str] = None
    pub_id: Optional[str] = None

    if cdn.is_configured():
        # Check if an original asset for this video already has a verified Cloudinary URL
        existing_orig = repo.get_original_asset(video_id)
        if (
            existing_orig
            and existing_orig.cloudinary_url
            and (existing_orig.cloudinary_url.startswith("http://") or existing_orig.cloudinary_url.startswith("https://"))
        ):
            cld_url = existing_orig.cloudinary_url
            pub_id = existing_orig.cloudinary_public_id
        else:
            # Deterministic naming convention: adoraxe/originals/{video_id}
            pub_id = cdn.get_public_id(video_id, asset_type="original")
            try:
                upload_result = await cdn.upload_video(
                    file_path=file_path,
                    public_id=pub_id,
                    folder="adoraxe/originals",
                    overwrite=True,
                )
            except Exception as e:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"Cloudinary upload failed: Unable to store original video asset in persistent cloud storage ({type(e).__name__}).",
                )

            if not upload_result or not upload_result.get("secure_url"):
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="Cloudinary upload failed: Cloud storage did not return a valid secure URL for original video.",
                )
            cld_url = upload_result.get("secure_url")

            # Step 5: Verify the returned HTTPS URL, content type, and byte-range availability
            is_verified = await cdn.verify_url(cld_url)
            if not is_verified:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="Cloudinary delivery verification failed: Uploaded media URL is not accessible or failed byte-range delivery verification.",
                )

    # Step 6: Persist the Cloudinary secure URL and public ID on the original VideoAsset record
    asset = repo.create_asset(video_id=video_id, file_path=file_path, original_filename=file.filename)
    asset.duration_seconds = int(probe.duration_seconds)
    asset.width = probe.width
    asset.height = probe.height
    asset.codec = probe.codec
    asset.file_size_bytes = probe.file_size_bytes
    asset.cloudinary_url = cld_url
    asset.cloudinary_public_id = pub_id
    asset.is_processed = False

    video = repo.get_by_id(video_id)
    if video and video.status == VideoStatus.FAILED:
        video.status = VideoStatus.IMPORTED

    db.commit()
    db.refresh(asset)

    # Step 7: Only return upload success after Cloudinary verification and database persistence succeed
    # Local temporary copy is retained until video processing has completed successfully
    return VideoUploadResponse(
        id=asset.id,
        video_id=video_id,
        message="Upload successful and persisted to permanent storage. Ready to process.",
    )


@router.post("/{video_id}/retrieve-media", response_model=MediaRetrieveResponse)
async def retrieve_media(
    video_id: str,
    request: MediaRetrieveRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """
    Acquire authorized video media from a direct media URL, authorized yt-dlp retrieval,
    or verify an existing asset.
    If auto_process=True, automatically kicks off the FFmpeg 9:16 Reel conversion job.
    """
    repo = VideoRepository(db)
    video = repo.get_by_id(video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    provider_used = "direct"
    source_url_used = request.media_url or video.video_url

    if request.use_ytdlp:
        provider_used = "yt-dlp"
        source_url_used = f"https://www.youtube.com/watch?v={video.youtube_video_id}"
        ytdlp_retriever = YtDlpMediaRetriever()
        try:
            asset, probe, status_str = await ytdlp_retriever.retrieve_media_for_video(
                video=video,
                db=db,
                rights_confirmed=request.rights_confirmed,
                reuse_existing=request.reuse_existing,
            )
        except DownloadNotAuthorizedError as e:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": e.code,
                    "message": str(e),
                    "action": "Please confirm content ownership or authorization to download and repurpose this video.",
                }
            )
        except YtDlpDisabledError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": e.code,
                    "message": str(e),
                    "action": "Enable yt-dlp by setting YTDLP_ENABLED=true in your environment configuration.",
                }
            )
        except JsRuntimeUnsupportedError as e:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": e.code,
                    "message": str(e),
                    "action": "Ensure the container environment runs a supported Node.js version (v22 or newer) for yt-dlp challenge solving.",
                }
            )
        except VideoUnavailableError as e:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": e.code,
                    "message": str(e),
                    "action": "The video is not available or has been deleted on YouTube.",
                }
            )
        except PrivateVideoError as e:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": e.code,
                    "message": str(e),
                    "action": "This video is marked private by its creator on YouTube. Accessing private videos is restricted. Please select a public video or upload the media file directly.",
                }
            )
        except BotChallengeError as e:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": e.code,
                    "message": str(e),
                    "action": "YouTube returned an anti-bot challenge (Sign in to confirm you're not a bot). In accordance with platform policy, automated CAPTCHA circumvention and cookie extraction are prohibited. Please use the manual video upload fallback below.",
                }
            )
        except AccessRestrictedError as e:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": e.code,
                    "message": str(e),
                    "action": "The video is access-restricted on YouTube (age confirmation, members-only, or region lock). Please upload the media file directly.",
                }
            )
        except FormatUnavailableError as e:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": e.code,
                    "message": str(e),
                    "action": "Requested video stream formats could not be extracted without authentication or DRM. Please use the manual video upload fallback below.",
                }
            )
        except RateLimitedError as e:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "code": e.code,
                    "message": str(e),
                    "action": "YouTube has rate-limited requests from this IP. Please wait before retrying.",
                }
            )
        except NetworkTimeoutError as e:
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail={
                    "code": e.code,
                    "message": str(e),
                    "action": "Connection to YouTube timed out. Please check network connectivity.",
                }
            )
        except FFmpegMissingError as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "code": e.code,
                    "message": str(e),
                    "action": "FFmpeg and FFprobe are required to merge and validate streams.",
                }
            )
        except FileSizeLimitExceededError as e:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail={
                    "code": e.code,
                    "message": str(e),
                    "action": "The video exceeds the 2 GB file size limit.",
                }
            )
        except InvalidMediaError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": e.code,
                    "message": str(e),
                    "action": "The retrieved media could not be validated or contains no valid video streams.",
                }
            )
        except YtDlpRetrievalError as e:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={
                    "code": e.code,
                    "message": str(e),
                }
            )
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"code": "RETRIEVAL_FAILED", "message": f"Unexpected yt-dlp error: {str(e)}"}
            )
    else:
        retrieval_service = MediaRetrievalService()
        try:
            asset, probe, status_str = await retrieval_service.retrieve_media_for_video(
                video=video,
                media_url=request.media_url,
                db=db,
                reuse_existing=request.reuse_existing,
            )
        except SecuritySSRFError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "SSRF_SECURITY_VIOLATION",
                    "message": str(e),
                    "action": "Only public, accessible HTTP/HTTPS media URLs are permitted. Internal, private, loopback, and cloud metadata addresses are blocked.",
                }
            )
        except MediaNotAvailableError as e:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "MEDIA_NOT_AVAILABLE",
                    "message": str(e),
                    "action": "Please provide an authorized direct media URL (e.g. MP4/MOV) or use the manual video upload fallback below.",
                }
            )
        except MediaSizeLimitExceededError as e:
            raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(e))
        except MediaValidationError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
        except MediaRetrievalError as e:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Unexpected retrieval failure: {str(e)}")

    job_id = None
    if request.auto_process:
        job_repo = JobRepository(db)
        existing_job = job_repo.get_active_processing_job(video_id)
        if existing_job and not job_repo.is_processing_job_stale(existing_job):
            job_id = existing_job.id
        else:
            if existing_job and job_repo.is_processing_job_stale(existing_job):
                job_repo.update_status(
                    existing_job.id,
                    JobStatus.FAILED,
                    error="Previous processing attempt timed out. Stale job expired.",
                )
            job = job_repo.create_processing_job(video_id=video_id)
            job_id = job.id
            processor = VideoProcessor()
            process_opts = ProcessVideoRequest(crop_mode=request.crop_mode)
            background_tasks.add_task(processor.process_video, job_id=job.id, video_id=video_id, options=process_opts)

    msg = "Media retrieved successfully and verified with FFprobe. Original video is now playable."
    if status_str == "REUSED":
        msg = "Existing verified media file on disk is active and playable."
    elif status_str == "DOWNLOADED" and provider_used == "yt-dlp":
        msg = "Authorized YouTube video stream retrieved via yt-dlp and verified with FFprobe."

    orig_stream = f"/api/v1/videos/{video.id}/stream/original"

    return MediaRetrieveResponse(
        asset_id=asset.id,
        video_id=video.id,
        status=status_str,
        provider=provider_used,
        message=msg,
        source_url=source_url_used,
        original_filename=asset.original_filename,
        duration_seconds=asset.duration_seconds,
        width=asset.width,
        height=asset.height,
        codec=asset.codec,
        file_size_bytes=asset.file_size_bytes,
        original_stream_url=orig_stream,
        job_id=job_id,
    )


@router.post("/{video_id}/process")
async def process_video(
    video_id: str,
    request: ProcessVideoRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """
    Start FFmpeg processing for a video.
    Enforces idempotency and concurrent processing limits to prevent duplicate background execution.
    """
    repo = VideoRepository(db)
    video = repo.get_by_id(video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    job_repo = JobRepository(db)

    # Idempotency guard: prevent duplicate concurrent processing jobs for the same video
    existing_job = job_repo.get_active_processing_job(video_id)
    if existing_job:
        if job_repo.is_processing_job_stale(existing_job):
            # Previous container session died or timed out; expire stale job
            job_repo.update_status(
                existing_job.id,
                JobStatus.FAILED,
                error="Previous processing attempt timed out or worker restarted. Stale job expired.",
            )
        else:
            # Active in-flight processing job already exists; return existing job to avoid duplicate FFmpeg runs
            return {
                "job_id": existing_job.id,
                "message": "Processing is already in progress for this video.",
                "status": existing_job.status,
            }

    job = job_repo.create_processing_job(video_id=video_id)

    processor = VideoProcessor()
    background_tasks.add_task(processor.process_video, job_id=job.id, video_id=video_id, options=request)

    return {"job_id": job.id, "message": "Processing started", "status": "PROCESSING"}


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job_status(job_id: str, db: Session = Depends(get_db)):
    """Get processing job status."""
    repo = JobRepository(db)
    job = repo.get_by_id(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job
