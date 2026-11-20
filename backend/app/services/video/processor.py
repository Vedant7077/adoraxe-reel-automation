"""
Video Processor – orchestrates FFmpeg processing + Cloudinary persistent storage.
Local disk is used only for temporary staging and conversion.
"""
import os
import asyncio
import logging
from typing import Optional
from sqlalchemy.orm import Session

from app.core.config import DEFAULT_TEMP_DIR, settings
from app.db.database import SessionLocal
from app.services.video.ffmpeg_service import probe_video, validate_for_reel, convert_to_reel
from app.services.cloudinary.cloudinary_service import CloudinaryService
from app.repositories.video_repository import VideoRepository
from app.repositories.job_repository import JobRepository
from app.models.models import VideoStatus, JobStatus

logger = logging.getLogger(__name__)

# Concurrency limiter: ensure at most 1 FFmpeg conversion runs at a time on limited-resource hosting
_PROCESSING_SEMAPHORE = asyncio.Semaphore(1)


class VideoProcessor:
    """Orchestrates the video processing and Cloudinary persistent storage pipeline."""

    async def _stage_remote_file(self, url: str, target_path: str) -> bool:
        """Download remote video URL (e.g. Cloudinary) to a local staging file for processing."""
        import httpx
        temp_part = target_path + ".part"
        try:
            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
                async with client.stream("GET", url) as resp:
                    if resp.status_code not in (200, 206):
                        logger.error(f"Failed to stream remote video from {url}: status {resp.status_code}")
                        return False
                    with open(temp_part, "wb") as f:
                        async for chunk in resp.aiter_bytes(chunk_size=1024 * 1024):
                            f.write(chunk)
            if os.path.exists(temp_part) and os.path.getsize(temp_part) > 0:
                if os.path.exists(target_path):
                    os.remove(target_path)
                os.rename(temp_part, target_path)
                return True
            return False
        except Exception as e:
            logger.error(f"Error staging remote video from {url}: {e}")
            if os.path.exists(temp_part):
                try:
                    os.remove(temp_part)
                except Exception:
                    pass
            return False

    async def process_video(self, job_id: str, video_id: str, options, db: Optional[Session] = None):
        """
        Full media lifecycle:
        1. Locate original staged file (or stage from Cloudinary persistent storage if local copy was cleaned).
        2. FFprobe validates original.
        3. FFmpeg converts original into Instagram-compatible 9:16 MP4 Reel.
        4. FFprobe validates converted Reel.
        5. Upload original MP4 to Cloudinary (adoraxe/originals/{video_id}) if not already uploaded.
        6. Upload processed Reel MP4 to Cloudinary (adoraxe/reels/{video_id}).
        7. Verify Cloudinary HTTPS delivery URLs.
        8. Save both Cloudinary secure URLs, public IDs, dimensions, file sizes, and metadata in PostgreSQL VideoAsset records.
        9. Update video processing status to PROCESSED.
        10. Safely delete temporary local original, processed, and partial files.
        * Failure handling: Never delete local files if upload, verification, or DB persistence fails.
        """
        session = db if db is not None else SessionLocal()
        should_close = db is None

        job_repo = JobRepository(session)
        video_repo = VideoRepository(session)

        # Track temporary files for safe cleanup upon success
        temp_files_to_clean = []

        try:
            job_repo.update_status(job_id, JobStatus.PROCESSING, progress=5)

            video = video_repo.get_by_id(video_id)
            if not video or not video.assets:
                job_repo.update_status(job_id, JobStatus.FAILED, error="No video asset found")
                return

            original_asset = video_repo.get_original_asset(video_id)
            if not original_asset:
                job_repo.update_status(job_id, JobStatus.FAILED, error="No original video asset found")
                return

            input_path = original_asset.file_path
            staged_from_remote = False
            remote_url = None

            if not input_path or not os.path.exists(input_path):
                # Locate persistent Cloudinary URL from original_asset or any non-processed asset of this video
                remote_url = original_asset.cloudinary_url
                if not remote_url or not (remote_url.startswith("http://") or remote_url.startswith("https://")):
                    for a in video.assets:
                        if a.cloudinary_url and not a.is_processed and (a.cloudinary_url.startswith("http://") or a.cloudinary_url.startswith("https://")):
                            remote_url = a.cloudinary_url
                            break

                if remote_url and (remote_url.startswith("http://") or remote_url.startswith("https://")):
                    job_repo.update_status(job_id, JobStatus.PROCESSING, progress=8)
                    staging_filename = f"staged_orig_{video_id}.mp4"
                    staged_path = os.path.join(DEFAULT_TEMP_DIR, staging_filename)
                    logger.info(f"Staging original video from Cloudinary for video {video_id}: {remote_url} -> {staged_path}")
                    staged_ok = await self._stage_remote_file(remote_url, staged_path)
                    if staged_ok and os.path.exists(staged_path) and os.path.getsize(staged_path) > 0:
                        input_path = staged_path
                        staged_from_remote = True
                        temp_files_to_clean.append(staged_path)
                    else:
                        job_repo.update_status(job_id, JobStatus.FAILED, error="Failed to stage original video from Cloudinary persistent storage.")
                        return
                elif video.youtube_video_id and getattr(settings, "ytdlp_enabled", False):
                    # Automatic retrieval fallback via yt-dlp if media was not yet uploaded to Cloudinary
                    job_repo.update_status(job_id, JobStatus.PROCESSING, progress=8)
                    from app.services.youtube.ytdlp_media_retriever import YtDlpMediaRetriever
                    ytdlp_retriever = YtDlpMediaRetriever()
                    try:
                        retrieved = await ytdlp_retriever.retrieve_media_for_video(video)
                        if retrieved and retrieved.file_path and os.path.exists(retrieved.file_path):
                            input_path = retrieved.file_path
                            temp_files_to_clean.append(input_path)
                    except Exception as ytdlp_err:
                        logger.warning(f"yt-dlp re-retrieval attempt failed: {ytdlp_err}")

                if not input_path or not os.path.exists(input_path):
                    job_repo.update_status(
                        job_id,
                        JobStatus.FAILED,
                        error="Original video file not found locally or on Cloudinary. Please acquire or upload video media first.",
                    )
                    return
            else:
                temp_files_to_clean.append(input_path)

            # Step 1: Probe original video
            job_repo.update_status(job_id, JobStatus.PROCESSING, progress=10)
            probe = probe_video(input_path)
            if not probe.is_valid:
                job_repo.update_status(job_id, JobStatus.FAILED, error=f"Original media validation error: {probe.error}")
                return

            # Step 2: Validate original for reel conversion
            job_repo.update_status(job_id, JobStatus.PROCESSING, progress=20)
            valid, reason = validate_for_reel(probe)
            if not valid:
                job_repo.update_status(job_id, JobStatus.FAILED, error=reason)
                return

            # Update original asset metadata with probed parameters
            video_repo.update_asset_metadata(
                asset_id=original_asset.id,
                duration=int(probe.duration_seconds),
                width=probe.width,
                height=probe.height,
                codec=probe.codec,
                file_size=probe.file_size_bytes,
            )

            # Step 3: Convert to 9:16 Reel format via FFmpeg
            job_repo.update_status(job_id, JobStatus.PROCESSING, progress=30)
            output_path = input_path.replace(".mp4", "_reel.mp4").replace(".mov", "_reel.mp4")
            if not output_path.endswith("_reel.mp4"):
                output_path = input_path + "_reel.mp4"

            temp_files_to_clean.append(output_path)

            crop_mode_val = (options.crop_mode if options and options.crop_mode else "blur").lower().strip()
            # Concurrency limit: Ensure at most 1 FFmpeg process executes at a time
            async with _PROCESSING_SEMAPHORE:
                success, message = convert_to_reel(
                    input_path=input_path,
                    output_path=output_path,
                    crop_mode=crop_mode_val,
                )
            if not success:
                job_repo.update_status(job_id, JobStatus.FAILED, error=f"FFmpeg conversion failed: {message}")
                return

            # Step 4: Probe converted Reel
            job_repo.update_status(job_id, JobStatus.PROCESSING, progress=60)
            reel_probe = probe_video(output_path)
            if not reel_probe.is_valid:
                job_repo.update_status(job_id, JobStatus.FAILED, error=f"Converted reel probe failed: {reel_probe.error}")
                return

            cdn = CloudinaryService()

            orig_cloudinary_url = None
            orig_public_id = None
            reel_cloudinary_url = None
            reel_public_id = None

            if cdn.is_configured():
                if (staged_from_remote or original_asset.cloudinary_url) and (original_asset.cloudinary_url or remote_url):
                    # Original is already persistently stored on Cloudinary; reuse verified URL and public ID
                    orig_cloudinary_url = original_asset.cloudinary_url or remote_url
                    orig_public_id = original_asset.cloudinary_public_id or cdn.get_public_id(video_id, "original")
                    job_repo.update_status(job_id, JobStatus.PROCESSING, progress=70)
                else:
                    # Step 5: Upload original MP4 to Cloudinary
                    job_repo.update_status(job_id, JobStatus.PROCESSING, progress=65)
                    orig_public_id = cdn.get_public_id(video_id, "original")
                    orig_result = await cdn.upload_video(
                        file_path=input_path,
                        public_id=orig_public_id,
                        folder="adoraxe/originals",
                        overwrite=True,
                    )
                    if not orig_result or not orig_result.get("secure_url"):
                        job_repo.update_status(job_id, JobStatus.FAILED, error="Cloudinary upload failed for original video.")
                        return
                    orig_cloudinary_url = orig_result.get("secure_url")

                    # Step 6: Verify original Cloudinary URL serves video content
                    orig_verified = await cdn.verify_url(orig_cloudinary_url)
                    if not orig_verified:
                        job_repo.update_status(job_id, JobStatus.FAILED, error="Cloudinary delivery verification failed for original video.")
                        return

                # Step 7: Upload processed Reel MP4 to Cloudinary
                job_repo.update_status(job_id, JobStatus.PROCESSING, progress=75)
                reel_public_id = cdn.get_public_id(video_id, "reel")
                reel_result = await cdn.upload_video(
                    file_path=output_path,
                    public_id=reel_public_id,
                    folder="adoraxe/reels",
                    overwrite=True,
                )
                if not reel_result or not reel_result.get("secure_url"):
                    job_repo.update_status(job_id, JobStatus.FAILED, error="Cloudinary upload failed for processed Reel.")
                    return
                reel_cloudinary_url = reel_result.get("secure_url")

                # Step 8: Verify processed Reel Cloudinary URL serves video content
                job_repo.update_status(job_id, JobStatus.PROCESSING, progress=85)
                reel_verified = await cdn.verify_url(reel_cloudinary_url)
                if not reel_verified:
                    job_repo.update_status(job_id, JobStatus.FAILED, error="Cloudinary delivery verification failed for processed Reel.")
                    return
            else:
                # Cloudinary not configured (e.g. local unconfigured demo mode)
                orig_cloudinary_url = original_asset.cloudinary_url or f"file://{input_path}"
                reel_cloudinary_url = f"file://{output_path}"

            # Step 9: Save both Cloudinary secure URLs, public IDs, and metadata in PostgreSQL VideoAsset records
            job_repo.update_status(job_id, JobStatus.PROCESSING, progress=90)

            # Update original asset record
            video_repo.update_asset_cloudinary(
                asset_id=original_asset.id,
                cloudinary_url=orig_cloudinary_url,
                cloudinary_public_id=orig_public_id,
            )

            # Create or update separate processed Reel asset record
            clean_base = (original_asset.original_filename or video_id).replace("_reel.mp4", "").replace(".mp4", "").replace(".mov", "")
            reel_filename = f"{clean_base}_reel.mp4"
            reel_asset = video_repo.create_or_update_reel_asset(
                video_id=video_id,
                cloudinary_url=reel_cloudinary_url,
                cloudinary_public_id=reel_public_id,
                original_filename=reel_filename,
                duration=int(reel_probe.duration_seconds or probe.duration_seconds),
                width=reel_probe.width or 1080,
                height=reel_probe.height or 1920,
                codec=reel_probe.codec or "h264",
                file_size=reel_probe.file_size_bytes,
                file_path=None if cdn.is_configured() else output_path,
                crop_mode=crop_mode_val,
            )

            # Step 10: Update video processing status only after successful upload and DB persistence
            video_repo.update_status(video_id, VideoStatus.PROCESSED)
            job_repo.update_status(job_id, JobStatus.FINISHED, progress=100, cloudinary_url=reel_cloudinary_url)

            # Step 11: Delete temporary local files safely ONLY after confirmed Cloudinary upload and DB commit
            if cdn.is_configured():
                for fpath in temp_files_to_clean:
                    if fpath and os.path.exists(fpath):
                        try:
                            os.remove(fpath)
                            logger.info(f"Cleaned up temporary staging file: {fpath}")
                        except Exception as cleanup_err:
                            logger.warning(f"Failed to remove temporary file {fpath}: {cleanup_err}")

                # Clear local file path on the original asset now that Cloudinary is the verified persistent source
                video_repo.clear_asset_local_path(original_asset.id)

        except Exception as e:
            # On any failure: local files are NOT deleted
            job_repo.update_status(job_id, JobStatus.FAILED, error=str(e))
        finally:
            if should_close:
                session.close()
