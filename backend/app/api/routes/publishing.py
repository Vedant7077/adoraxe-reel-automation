"""
Publishing routes – create and track Reel publishing jobs with duplicate prevention.
"""
from typing import List, Optional
import hashlib
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Query, status
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.schemas import PublishRequest, PublishResponse, JobStatusResponse, QueueVideoItem, ActiveJobResponse
from app.repositories.job_repository import JobRepository
from app.repositories.instagram_repository import InstagramRepository
from app.repositories.video_repository import VideoRepository
from app.services.instagram.publisher import InstagramPublisher
from app.core.config import settings
from app.models.models import InstagramAccountStatus, JobStatus, Video, VideoStatus, PublishingJob, PublishingHistory

router = APIRouter()


@router.get("/queue-videos", response_model=List[QueueVideoItem])
async def list_queue_videos(
    account_id: Optional[str] = Query(None, description="Optional account ID to evaluate duplicate status against"),
    db: Session = Depends(get_db),
):
    """
    List all processed videos for the Publishing Queue, annotated with their
    account-specific publishability status and duplicate prevention flags.
    """
    is_demo = settings.demo_mode
    ig_repo = InstagramRepository(db)
    account = None
    if account_id:
        account = ig_repo.get_account_by_id(account_id)
    if not account:
        account = ig_repo.get_effective_account(is_demo=is_demo)
    effective_account_id = account.id if account else None

    # Get processed videos
    videos = db.query(Video).filter(Video.status == VideoStatus.PROCESSED).order_by(Video.created_at.desc()).all()
    job_repo = JobRepository(db)
    video_repo = VideoRepository(db)

    results: List[QueueVideoItem] = []
    for v in videos:
        is_dup = False
        reason = None

        # Check Cloudinary requirement and duration for publishing
        proc_asset = video_repo.get_processed_asset(v.id)
        has_cloudinary_url = bool(
            proc_asset and proc_asset.cloudinary_url and proc_asset.cloudinary_url.startswith("https://")
        )
        reel_cld_url = proc_asset.cloudinary_url if (proc_asset and proc_asset.cloudinary_url and proc_asset.cloudinary_url.startswith("https://")) else None
        crop_mode = proc_asset.crop_mode if proc_asset else None
        width = proc_asset.width if proc_asset else None
        height = proc_asset.height if proc_asset else None
        reel_duration = proc_asset.duration_seconds if (proc_asset and proc_asset.duration_seconds) else v.duration_seconds

        if not has_cloudinary_url:
            for a in v.assets:
                if a.cloudinary_url and a.cloudinary_url.startswith("https://") and a.is_processed:
                    has_cloudinary_url = True
                    reel_cld_url = a.cloudinary_url
                    crop_mode = a.crop_mode or crop_mode
                    width = a.width or width
                    height = a.height or height
                    if a.duration_seconds:
                        reel_duration = a.duration_seconds
                    break

        if not has_cloudinary_url:
            reason = "Video must have a processed Cloudinary HTTPS URL before publishing."
        elif reel_duration and reel_duration > 900:
            reason = f"Reel duration ({reel_duration}s) exceeds Meta's 15-minute (900s) maximum limit. Please re-process the video in the Video Library to automatically trim to 15 minutes."

        if effective_account_id:
            dup_found, dup_reason = job_repo.check_duplicate_publish(
                video_id=v.id,
                account_id=effective_account_id,
                is_demo=is_demo,
            )
            if dup_found:
                is_dup = True
                reason = dup_reason

        is_in_progress = is_dup and ("in progress" in (reason or "").lower())
        is_published = is_dup and not is_in_progress

        permalink = None
        media_id = None
        published_caption = None
        published_at = None

        if is_published:
            hist = db.query(PublishingHistory).filter(
                PublishingHistory.video_id == v.id,
            ).order_by(PublishingHistory.created_at.desc()).first()
            if hist:
                permalink = hist.permalink
                media_id = hist.instagram_media_id
                published_at = hist.published_at or hist.created_at

            job = db.query(PublishingJob).filter(
                PublishingJob.video_id == v.id,
                PublishingJob.status.in_([JobStatus.PUBLISHED.value, JobStatus.SIMULATED.value, "PUBLISHED", "SIMULATED"]),
            ).order_by(PublishingJob.created_at.desc()).first()
            if job:
                if not permalink:
                    permalink = job.permalink
                if not media_id:
                    media_id = job.instagram_media_id
                published_caption = job.caption
                if not published_at:
                    published_at = job.published_at or job.updated_at

        can_pub = (not is_dup) and has_cloudinary_url and not (reel_duration and reel_duration > 900)
        ch_name = v.channel_name or (v.channel.name if v.channel else None)

        results.append(
            QueueVideoItem(
                id=v.id,
                title=v.title,
                thumbnail_url=v.thumbnail_url,
                channel_name=ch_name,
                duration_seconds=reel_duration,
                status=v.status,
                is_already_published=is_published,
                is_publishing_in_progress=is_in_progress,
                can_publish=can_pub,
                disabled_reason=reason if (is_dup or not has_cloudinary_url or (reel_duration and reel_duration > 900)) else None,
                cloudinary_url=reel_cld_url,
                crop_mode=crop_mode,
                width=width,
                height=height,
                permalink=permalink,
                instagram_media_id=media_id,
                published_caption=published_caption,
                published_at=published_at,
            )
        )

    return results


@router.post("/queue/{video_id}/remove")
@router.delete("/queue/{video_id}")
async def remove_from_publishing_queue(video_id: str, db: Session = Depends(get_db)):
    """
    Remove a video from the publishing queue by changing status to READY.
    Preserves all processed video assets, Cloudinary URLs, and publishing history.
    """
    video = db.query(Video).filter(Video.id == video_id).first()
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    video.status = VideoStatus.READY
    db.commit()
    db.refresh(video)
    return {"message": "Video removed from publishing queue", "video_id": video_id}


@router.post("/queue/{video_id}/add")
async def add_to_publishing_queue(video_id: str, db: Session = Depends(get_db)):
    """
    Add or re-add a processed Reel back into the publishing queue.
    """
    video = db.query(Video).filter(Video.id == video_id).first()
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    repo = VideoRepository(db)
    orig_asset = repo.get_original_asset(video_id)
    proc_asset = repo.get_processed_asset(video_id)

    has_uploaded = bool(
        orig_asset
        and (
            (orig_asset.cloudinary_url and (orig_asset.cloudinary_url.startswith("http://") or orig_asset.cloudinary_url.startswith("https://")))
            or (orig_asset.file_path and os.path.exists(orig_asset.file_path))
        )
    )
    has_processed = bool(
        proc_asset
        and (
            (proc_asset.cloudinary_url and (proc_asset.cloudinary_url.startswith("http://") or proc_asset.cloudinary_url.startswith("https://")))
            or (proc_asset.file_path and os.path.exists(proc_asset.file_path))
            or (proc_asset.is_processed)
        )
    )

    if not has_uploaded and not has_processed:
        raise HTTPException(
            status_code=400,
            detail="Cannot add to publishing queue: Source video media must be retrieved through yt-dlp or uploaded first."
        )

    if not has_processed and video.status != VideoStatus.PROCESSED:
        raise HTTPException(
            status_code=400,
            detail="Cannot add to publishing queue: Video has not been converted into a 9:16 Reel yet. Please process the video as a Reel first."
        )

    video.status = VideoStatus.PROCESSED
    db.commit()
    db.refresh(video)
    return {"message": "Video added to publishing queue", "video_id": video_id}


@router.post("", response_model=PublishResponse)
async def publish_reel(
    request: PublishRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """
    Create a publishing job for a Reel with database-level concurrency protection
    and duplicate-publishing prevention.
    """
    if not request.rights_confirmed:
        raise HTTPException(status_code=400, detail="Publishing rights must be confirmed.")

    is_demo = settings.demo_mode
    ig_repo = InstagramRepository(db)
    account = None
    if request.instagram_account_id:
        acc = ig_repo.get_account_by_id(request.instagram_account_id)
        if acc and (is_demo or acc.status == InstagramAccountStatus.CONNECTED):
            account = acc
    if not account:
        account = ig_repo.get_effective_account(is_demo=is_demo)

    if not is_demo and (not account or account.status != InstagramAccountStatus.CONNECTED):
        raise HTTPException(
            status_code=403,
            detail="No connected Instagram account. Please connect your Instagram account first."
        )

    if not account:
        raise HTTPException(
            status_code=400,
            detail="No Instagram account available for publishing."
        )

    # 1. Row-level transaction lock on the video record to serialize concurrent requests
    locked_video = db.query(Video).filter(Video.id == request.video_id).with_for_update().first()
    if not locked_video:
        raise HTTPException(status_code=404, detail="Video not found")

    # Verify that the video has a processed Cloudinary HTTPS URL
    video_repo = VideoRepository(db)
    proc_asset = video_repo.get_processed_asset(request.video_id)
    reel_cloudinary_url = proc_asset.cloudinary_url if proc_asset else None
    if not reel_cloudinary_url or not reel_cloudinary_url.startswith("https://"):
        for a in locked_video.assets:
            if a.cloudinary_url and a.cloudinary_url.startswith("https://") and a.is_processed:
                reel_cloudinary_url = a.cloudinary_url
                break

    if not reel_cloudinary_url or not reel_cloudinary_url.startswith("https://"):
        raise HTTPException(
            status_code=400,
            detail="Cannot publish: Video does not have a processed Cloudinary HTTPS URL. Please process the video with Cloudinary storage enabled."
        )

    # Verify duration does not exceed Meta's 15-minute (900-second) Reels limit
    reel_duration = proc_asset.duration_seconds if (proc_asset and proc_asset.duration_seconds) else locked_video.duration_seconds
    if reel_duration and reel_duration > 900:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot publish: Reel duration ({reel_duration}s) exceeds Meta's 15-minute (900 seconds) maximum limit. Please re-process the video in the Video Library to automatically trim to 15 minutes."
        )

    # Verify the Cloudinary URL is publicly retrievable before creating job
    from app.services.cloudinary.cloudinary_service import CloudinaryService
    cdn = CloudinaryService()
    try:
        is_accessible = await cdn.verify_url(reel_cloudinary_url)
    except Exception as verify_err:
        logger.warning("Cloudinary verification check non-fatal exception: %s", verify_err)
        is_accessible = reel_cloudinary_url.startswith("https://")

    if not is_accessible and not reel_cloudinary_url.startswith("https://res.cloudinary.com/"):
        raise HTTPException(
            status_code=422,
            detail="Cannot publish: Processed Reel Cloudinary URL is not publicly retrievable. Please verify Cloudinary CDN availability."
        )

    # If PostgreSQL, also acquire transaction-scoped advisory lock for (video_id, account_id)
    if db.bind and hasattr(db.bind, "dialect") and db.bind.dialect.name == "postgresql":
        from sqlalchemy import text
        db.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:k))"),
            {"k": f"publish_{request.video_id}_{account.id}"}
        )

    # 2. Duplicate protection check
    job_repo = JobRepository(db)
    is_duplicate, reason = job_repo.check_duplicate_publish(
        video_id=request.video_id,
        account_id=account.id,
        is_demo=is_demo,
    )
    if is_duplicate:
        raise HTTPException(
            status_code=409,
            detail=reason,
        )

    # 3. Create persistent publish job with caption, idempotency, and verified Cloudinary HTTPS URL
    caption_hash = hashlib.sha256(request.caption.encode("utf-8")).hexdigest()[:16]
    idempotency_key = f"pub_{account.id}_{request.video_id}_{caption_hash}"

    job = job_repo.create_publish_job(
        video_id=request.video_id,
        instagram_account_id=account.id,
        caption=request.caption,
        is_demo=is_demo,
        cloudinary_url=reel_cloudinary_url,
        cloudinary_public_id=proc_asset.cloudinary_public_id if proc_asset else None,
        idempotency_key=idempotency_key,
    )

    publisher = InstagramPublisher()
    background_tasks.add_task(
        publisher.publish_reel,
        job_id=job.id,
        is_demo=is_demo,
        db=None,
    )

    status_msg = f"SIMULATED publish queued for @{account.username} (DEMO_MODE)" if is_demo else f"Publishing queued for @{account.username}"
    return PublishResponse(
        job_id=job.id,
        status=JobStatus.PENDING,
        current_stage="QUEUED",
        message=status_msg,
    )


@router.get("/active", response_model=ActiveJobResponse)
async def get_active_publish_job(
    account_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """
    Retrieve the active in-flight or latest publishing job for this session.
    Allows frontend pages to restore publishing progress and caption seamlessly.
    """
    repo = JobRepository(db)
    job = repo.get_latest_active_job(account_id=account_id)
    if not job:
        return ActiveJobResponse(has_active_job=False, job=None)

    status_val = job.status.value if hasattr(job.status, "value") else str(job.status)
    try:
        status_enum = JobStatus(status_val)
    except Exception:
        status_enum = JobStatus.PROCESSING

    return ActiveJobResponse(
        has_active_job=True,
        job=JobStatusResponse(
            id=job.id,
            status=status_enum,
            current_stage=job.current_stage or status_val,
            progress=job.processing_progress or 0,
            video_id=job.video_id,
            instagram_account_id=job.instagram_account_id,
            caption=job.caption,
            instagram_container_id=job.instagram_container_id,
            instagram_media_id=job.instagram_media_id,
            permalink=job.permalink,
            error_message=job.error_message,
            cloudinary_url=job.cloudinary_url,
            created_at=job.created_at,
            updated_at=job.updated_at,
            published_at=job.published_at,
        ),
    )


@router.post("/jobs/{job_id}/reconcile")
async def reconcile_publishing_job(
    job_id: str,
    db: Session = Depends(get_db),
):
    """
    Trigger on-demand safe read-only reconciliation for a publishing job.
    Never creates a new container or calls media_publish.
    """
    repo = JobRepository(db)
    job = repo.get_by_id(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Publishing job not found")

    publisher = InstagramPublisher()
    success, message, media_item = await publisher.reconcile_job(job_id, db)
    db.refresh(job)
    return {
        "success": success,
        "message": message,
        "status": str(job.status),
        "current_stage": job.current_stage,
        "media_id": job.instagram_media_id,
        "permalink": job.permalink,
    }


@router.get("/{job_id}", response_model=JobStatusResponse)
async def get_publish_job(job_id: str, db: Session = Depends(get_db)):
    """Get full publish job status."""
    repo = JobRepository(db)
    job = repo.get_by_id(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Publishing job not found")

    status_val = job.status.value if hasattr(job.status, "value") else str(job.status)
    try:
        status_enum = JobStatus(status_val)
    except Exception:
        status_enum = JobStatus.PROCESSING

    return JobStatusResponse(
        id=job.id,
        status=status_enum,
        current_stage=job.current_stage or status_val,
        progress=job.processing_progress or 0,
        video_id=job.video_id,
        instagram_account_id=job.instagram_account_id,
        caption=job.caption,
        instagram_container_id=job.instagram_container_id,
        instagram_media_id=job.instagram_media_id,
        permalink=job.permalink,
        error_message=job.error_message,
        cloudinary_url=job.cloudinary_url,
        created_at=job.created_at,
        updated_at=job.updated_at,
        published_at=job.published_at,
    )
