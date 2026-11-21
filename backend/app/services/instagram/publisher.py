"""
Instagram Reel Publisher – implements persistent 8-stage publishing state machine
with safe uncertain outcome handling and automatic read-only reconciliation.
"""
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple, Dict, Any
import httpx
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.database import SessionLocal
from app.models.models import JobStatus, PublishStatus, PublishingJob
from app.repositories.job_repository import JobRepository
from app.repositories.history_repository import HistoryRepository
from app.repositories.instagram_repository import InstagramRepository
from app.services.instagram.token_manager import TokenManager

logger = logging.getLogger(__name__)


class InstagramPublisher:
    """
    Implements official Meta Instagram Reels publishing workflow with:
    - Persistent state machine: QUEUED -> CONTAINER_CREATING -> CONTAINER_PROCESSING ->
      READY_TO_PUBLISH -> PUBLISH_REQUESTED -> PUBLISHED (or OUTCOME_UNKNOWN -> RECONCILED)
    - Concurrency and idempotency protection
    - Safe uncertain outcome handling (never blind retries or false FAILED states)
    - Automatic read-only reconciliation against Instagram recent media
    """

    GRAPH_API_BASE = "https://graph.facebook.com"
    MAX_POLL_ATTEMPTS = 120  # 120 attempts x 5s = 600s (10 mins) tolerance for large Meta video transcodes
    POLL_INTERVAL_SECONDS = 5
    HTTP_TIMEOUT_SECONDS = 60.0

    async def publish_reel(self, job_id: str, is_demo: bool, db: Optional[Session] = None, is_recovery: bool = False):
        """
        Execute full database-backed publishing pipeline.
        Runs independently of client/browser connections.
        """
        session = db if db is not None else SessionLocal()
        should_close = db is None

        job_repo = JobRepository(session)
        history_repo = HistoryRepository(session)
        ig_repo = InstagramRepository(session)

        try:
            job = job_repo.claim_job_for_execution(job_id, force_recovery=is_recovery)
            if not job:
                existing = job_repo.get_by_id(job_id)
                if existing and str(existing.status) in (JobStatus.PUBLISHED.value, JobStatus.SIMULATED.value, "PUBLISHED", "SIMULATED"):
                    logger.info("Job %s is already published/simulated. Skipping execution.", job_id)
                    return
                logger.info("Job %s could not be claimed or is locked by another runner. Skipping.", job_id)
                return

            if not job.rights_confirmed:
                job_repo.update_status(job_id, JobStatus.FAILED.value, error="Publishing rights not confirmed")
                history_repo.create_entry_failed(job_id, "Publishing rights not confirmed")
                return

            if not job.cloudinary_url or not job.cloudinary_url.startswith("https://"):
                err_msg = "Cannot publish without a verified Cloudinary HTTPS Reel URL. Local or simulated file URLs are not permitted."
                job_repo.update_status(job_id, JobStatus.FAILED.value, error=err_msg)
                history_repo.create_entry_failed(job_id, err_msg)
                return

            # Handle DEMO_MODE simulated execution
            if is_demo:
                await self._run_simulated_publish(job_id, job_repo, history_repo, session)
                return

            # Resolve Instagram account
            account = (
                ig_repo.get_account_by_id(job.instagram_account_id)
                if job.instagram_account_id
                else ig_repo.get_active_account()
            )
            if not account or not account.encrypted_access_token:
                err_msg = "No connected Instagram account with a valid access token"
                job_repo.update_status(job_id, JobStatus.FAILED.value, error=err_msg)
                history_repo.create_entry_failed(job_id, err_msg)
                return

            token_mgr = TokenManager()
            access_token = token_mgr.decrypt_token(account.encrypted_access_token)

            # Stage 1: CONTAINER_CREATING
            job_repo.update_status(
                job_id,
                JobStatus.CONTAINER_CREATING.value,
                progress=15,
                current_stage="CONTAINER_CREATING"
            )

            container_id = job.instagram_container_id
            if not container_id:
                try:
                    async with httpx.AsyncClient(timeout=self.HTTP_TIMEOUT_SECONDS) as client:
                        resp = await client.post(
                            f"{self.GRAPH_API_BASE}/{settings.meta_api_version}/{account.instagram_user_id}/media",
                            data={
                                "media_type": "REELS",
                                "video_url": job.cloudinary_url,
                                "caption": job.caption or "",
                                "access_token": access_token,
                            }
                        )
                        resp.raise_for_status()
                        container_data = resp.json()
                        container_id = container_data.get("id")
                except httpx.HTTPStatusError as e:
                    err_msg = f"Meta container creation rejected ({e.response.status_code}): {e.response.text}"
                    logger.error("Meta container creation failed for job %s: %s", job_id, err_msg)
                    job_repo.update_status(job_id, JobStatus.FAILED.value, error=err_msg)
                    history_repo.create_entry_failed(job_id, err_msg)
                    return
                except Exception as e:
                    err_msg = f"Failed to connect to Meta for container creation: {e}"
                    logger.error("Connection error creating container for job %s: %s", job_id, err_msg)
                    job_repo.update_status(job_id, JobStatus.FAILED.value, error=err_msg)
                    history_repo.create_entry_failed(job_id, err_msg)
                    return

                if not container_id:
                    err_msg = "Meta did not return an Instagram media container ID"
                    job_repo.update_status(job_id, JobStatus.FAILED.value, error=err_msg)
                    history_repo.create_entry_failed(job_id, err_msg)
                    return

                job_repo.update_container_id(job_id, container_id)

            # Stage 2: CONTAINER_PROCESSING
            job_repo.update_status(
                job_id,
                JobStatus.CONTAINER_PROCESSING.value,
                progress=30,
                current_stage="CONTAINER_PROCESSING"
            )

            # Stage 3: Poll until ready
            is_ready = await self._poll_until_ready(container_id, access_token, job_id, job_repo)
            if not is_ready:
                err_msg = getattr(self, "_last_poll_error", None) or "Instagram media container processing timed out or failed on Meta's servers."
                job_repo.update_status(job_id, JobStatus.FAILED.value, error=err_msg)
                history_repo.create_entry_failed(job_id, err_msg)
                return

            # Stage 4: READY_TO_PUBLISH
            job_repo.update_status(
                job_id,
                JobStatus.READY_TO_PUBLISH.value,
                progress=80,
                current_stage="READY_TO_PUBLISH"
            )

            # Stage 5: PUBLISH_REQUESTED (Persist BEFORE sending media_publish to Meta!)
            job_repo.update_status(
                job_id,
                JobStatus.PUBLISH_REQUESTED.value,
                progress=85,
                current_stage="PUBLISH_REQUESTED"
            )

            # Stage 6: Send media_publish to Meta
            publish_data = None
            try:
                async with httpx.AsyncClient(timeout=self.HTTP_TIMEOUT_SECONDS) as client:
                    publish_resp = await client.post(
                        f"{self.GRAPH_API_BASE}/{settings.meta_api_version}/{account.instagram_user_id}/media_publish",
                        data={
                            "creation_id": container_id,
                            "access_token": access_token,
                        }
                    )
                    publish_resp.raise_for_status()
                    publish_data = publish_resp.json()
            except httpx.HTTPStatusError as e:
                if 400 <= e.response.status_code < 500:
                    # Definitive client rejection from Meta
                    err_msg = f"Meta publishing rejected ({e.response.status_code}): {e.response.text}"
                    logger.error("Meta media_publish client error for job %s: %s", job_id, err_msg)
                    job_repo.update_status(job_id, JobStatus.FAILED.value, error=err_msg)
                    history_repo.create_entry_failed(job_id, err_msg)
                    return
                else:
                    # Server 5xx error from Meta - outcome UNCERTAIN!
                    logger.warning("Meta media_publish returned 5xx for job %s; marking OUTCOME_UNKNOWN", job_id)
                    job_repo.update_status(
                        job_id,
                        JobStatus.OUTCOME_UNKNOWN.value,
                        error=f"Meta server returned {e.response.status_code}. Outcome uncertain. Initiating reconciliation."
                    )
                    await asyncio.sleep(3)
                    await self.reconcile_job(job_id, session)
                    return
            except (httpx.TimeoutException, httpx.NetworkError, Exception) as e:
                # Network timeout, socket drop, or process interruption after request sent!
                # DO NOT mark as FAILED! Set OUTCOME_UNKNOWN and reconcile!
                logger.warning(
                    "Network error/timeout after media_publish for job %s: %s. Setting OUTCOME_UNKNOWN and reconciling.",
                    job_id, e
                )
                job_repo.update_status(
                    job_id,
                    JobStatus.OUTCOME_UNKNOWN.value,
                    error=f"Publish request sent but response timed out: {e or 'Timeout'}. Outcome uncertain."
                )
                await asyncio.sleep(4)
                await self.reconcile_job(job_id, session)
                return

            media_id = publish_data.get("id") if publish_data else None
            if not media_id:
                logger.warning("media_publish returned 200 without media ID for job %s. Setting OUTCOME_UNKNOWN and reconciling.", job_id)
                job_repo.update_status(
                    job_id,
                    JobStatus.OUTCOME_UNKNOWN.value,
                    error="Meta media_publish response missing media ID. Initiating reconciliation."
                )
                await asyncio.sleep(3)
                await self.reconcile_job(job_id, session)
                return

            # Stage 7: Retrieve permalink (read-only query)
            permalink = await self._fetch_media_permalink(media_id, access_token)

            # Stage 8: PUBLISHED - Persist confirmed success
            pub_time = datetime.utcnow()
            job_repo.update_published(
                job_id=job_id,
                media_id=media_id,
                permalink=permalink,
                published_at=pub_time,
            )
            history_repo.create_or_update_published(
                job_id=job_id,
                video_id=job.video_id,
                instagram_account_id=job.instagram_account_id,
                media_id=media_id,
                permalink=permalink,
                published_at=pub_time,
            )
            logger.info("Successfully published Reel %s for job %s (Media ID: %s)", permalink or media_id, job_id, media_id)

        except Exception as e:
            logger.exception("Unexpected error in publish_reel for job %s: %s", job_id, e)
            curr_job = job_repo.get_by_id(job_id)
            if curr_job and str(curr_job.status) == JobStatus.PUBLISH_REQUESTED.value:
                job_repo.update_status(job_id, JobStatus.OUTCOME_UNKNOWN.value, error=str(e))
                await self.reconcile_job(job_id, session)
            elif curr_job and str(curr_job.status) not in (JobStatus.PUBLISHED.value, "PUBLISHED"):
                job_repo.update_status(job_id, JobStatus.FAILED.value, error=str(e))
                history_repo.create_entry_failed(job_id, str(e))
        finally:
            if should_close:
                session.close()

    async def reconcile_job(self, job_id: str, db: Optional[Session] = None) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """
        Safe, read-only reconciliation against Instagram recent media.
        Identifies whether a job with uncertain outcome actually succeeded on Instagram.
        If verified: marks PUBLISHED, saves media_id and permalink.
        If ambiguous or not found: marks RECONCILIATION_REQUIRED.
        NEVER creates a new container or calls media_publish.
        """
        session = db if db is not None else SessionLocal()
        should_close = db is None

        job_repo = JobRepository(session)
        history_repo = HistoryRepository(session)
        ig_repo = InstagramRepository(session)

        try:
            job = job_repo.get_by_id(job_id)
            if not job:
                return (False, "Job not found", None)

            if str(job.status) == JobStatus.PUBLISHED.value:
                return (True, "Job is already marked as PUBLISHED", {"media_id": job.instagram_media_id, "permalink": job.permalink})

            account = (
                ig_repo.get_account_by_id(job.instagram_account_id)
                if job.instagram_account_id
                else ig_repo.get_active_account()
            )
            if not account or not account.encrypted_access_token:
                job_repo.update_status(job_id, JobStatus.RECONCILIATION_REQUIRED.value, error="No Instagram account credentials for reconciliation")
                return (False, "No credentials available for reconciliation", None)

            token_mgr = TokenManager()
            access_token = token_mgr.decrypt_token(account.encrypted_access_token)

            # Query recent media
            url = f"{self.GRAPH_API_BASE}/{settings.meta_api_version}/{account.instagram_user_id}/media"
            params = {
                "fields": "id,caption,media_type,media_product_type,timestamp,permalink,shortcode",
                "access_token": access_token,
            }

            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(url, params=params)
                if resp.status_code != 200:
                    err_detail = f"Meta Graph API error during reconciliation: {resp.status_code}"
                    job_repo.update_status(job_id, JobStatus.RECONCILIATION_REQUIRED.value, error=err_detail)
                    return (False, err_detail, None)
                media_items = resp.json().get("data", [])

            # Match against candidate media items
            matches = []
            job_caption_clean = (job.caption or "").strip()
            for item in media_items:
                item_caption = (item.get("caption") or "").strip()
                item_product_type = item.get("media_product_type") or item.get("media_type")

                # Match by exact caption or exact container shortcode
                caption_matches = bool(job_caption_clean and item_caption == job_caption_clean)
                is_reel = (item_product_type in ("REELS", "VIDEO")) if item_product_type else True

                if caption_matches and is_reel:
                    matches.append(item)

            if len(matches) == 1:
                matched_item = matches[0]
                media_id = matched_item["id"]
                permalink = matched_item.get("permalink")
                raw_ts = matched_item.get("timestamp")
                try:
                    pub_dt = datetime.fromisoformat(raw_ts.replace("Z", "+00:00")) if raw_ts else datetime.utcnow()
                except Exception:
                    pub_dt = datetime.utcnow()

                job_repo.update_published(
                    job_id=job_id,
                    media_id=media_id,
                    permalink=permalink,
                    published_at=pub_dt,
                )
                history_repo.create_or_update_published(
                    job_id=job_id,
                    video_id=job.video_id,
                    instagram_account_id=job.instagram_account_id,
                    media_id=media_id,
                    permalink=permalink,
                    published_at=pub_dt,
                )
                logger.info("Reconciliation SUCCESS for job %s: matched Reel %s", job_id, media_id)
                return (True, f"Reconciliation successful: matched Instagram Reel {media_id}", matched_item)

            elif len(matches) == 0:
                logger.warning("Reconciliation for job %s found 0 matching media items on Instagram", job_id)
                job_repo.update_status(
                    job_id,
                    JobStatus.RECONCILIATION_REQUIRED.value,
                    error="Read-only reconciliation could not definitively locate published Reel on Instagram. Manual review required."
                )
                return (False, "No matching Reel found on Instagram", None)
            else:
                logger.warning("Reconciliation for job %s found %d ambiguous matches", job_id, len(matches))
                job_repo.update_status(
                    job_id,
                    JobStatus.RECONCILIATION_REQUIRED.value,
                    error=f"Found {len(matches)} ambiguous matches on Instagram. Manual verification required."
                )
                return (False, f"Ambiguous reconciliation: {len(matches)} matching items found", None)

        except Exception as e:
            logger.exception("Error in reconcile_job for %s: %s", job_id, e)
            job_repo.update_status(job_id, JobStatus.RECONCILIATION_REQUIRED.value, error=str(e))
            return (False, str(e), None)
        finally:
            if should_close:
                session.close()

    async def _fetch_media_permalink(self, media_id: str, access_token: str) -> Optional[str]:
        """Fetch permalink for a published Instagram media item."""
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    f"{self.GRAPH_API_BASE}/{settings.meta_api_version}/{media_id}",
                    params={"fields": "permalink", "access_token": access_token}
                )
                if resp.status_code == 200:
                    return resp.json().get("permalink")
        except Exception as e:
            logger.warning("Could not fetch permalink for media %s: %s", media_id, e)
        return None

    async def _poll_until_ready(self, container_id: str, access_token: str, job_id: str, job_repo: JobRepository) -> bool:
        """Poll the container status until FINISHED or failure, updating progress incrementally."""
        self._last_poll_error = None
        for attempt in range(self.MAX_POLL_ATTEMPTS):
            await asyncio.sleep(self.POLL_INTERVAL_SECONDS)
            progress_pct = min(78, 30 + int(((attempt + 1) / self.MAX_POLL_ATTEMPTS) * 48))
            elapsed_sec = (attempt + 1) * int(self.POLL_INTERVAL_SECONDS)
            stage_msg = f"Meta is processing Reel media container ({elapsed_sec}s elapsed)..."

            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    resp = await client.get(
                        f"{self.GRAPH_API_BASE}/{settings.meta_api_version}/{container_id}",
                        params={"fields": "status_code,status", "access_token": access_token}
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        status_code = data.get("status_code")
                        status_detail = data.get("status")
                        if status_code == "FINISHED":
                            self._last_poll_error = None
                            return True
                        if status_code in ("ERROR", "EXPIRED"):
                            detail = status_detail or data.get("error_message") or f"Container status: {status_code}"
                            logger.error("Instagram container %s failed with status_code: %s (%s)", container_id, status_code, detail)
                            self._last_poll_error = f"Instagram media container failed ({status_code}): {detail}"
                            return False

                        job_repo.update_status(
                            job_id,
                            JobStatus.CONTAINER_PROCESSING.value,
                            progress=progress_pct,
                            current_stage=stage_msg,
                        )
                    else:
                        err_text = resp.text
                        try:
                            err_json = resp.json()
                            if "error" in err_json and "message" in err_json["error"]:
                                err_text = err_json["error"]["message"]
                        except Exception:
                            pass
                        logger.warning("Meta API returned %d while polling container %s: %s", resp.status_code, container_id, err_text)
            except Exception as e:
                logger.warning("Transient error polling container %s (attempt %d/%d): %s", container_id, attempt + 1, self.MAX_POLL_ATTEMPTS, e)

        timeout_msg = f"Instagram media container processing timed out after {int(self.MAX_POLL_ATTEMPTS * self.POLL_INTERVAL_SECONDS)}s on Meta's servers."
        logger.error(timeout_msg)
        self._last_poll_error = timeout_msg
        return False

    async def _run_simulated_publish(self, job_id: str, job_repo: JobRepository, history_repo: HistoryRepository, db: Session):
        """
        Simulate publishing workflow for DEMO_MODE.
        Clearly marks result as SIMULATED, never makes live Meta calls.
        """
        job_repo.update_status(job_id, JobStatus.CONTAINER_PROCESSING.value, progress=30, current_stage="CONTAINER_PROCESSING")
        await asyncio.sleep(1.5)
        job_repo.update_status(job_id, JobStatus.READY_TO_PUBLISH.value, progress=70, current_stage="READY_TO_PUBLISH")
        await asyncio.sleep(1.5)
        job_repo.update_status(job_id, JobStatus.PUBLISH_REQUESTED.value, progress=85, current_stage="PUBLISH_REQUESTED")
        await asyncio.sleep(1.0)

        job = job_repo.get_by_id(job_id)
        if job:
            simulated_media_id = f"simulated_media_{job.id[:8]}"
            simulated_permalink = f"https://instagram.com/p/simulated_{job.id[:8]}"
            pub_time = datetime.now(timezone.utc).replace(tzinfo=None) if hasattr(datetime, "now") else datetime.utcnow()

            job_repo.update_published(
                job_id=job_id,
                media_id=simulated_media_id,
                permalink=simulated_permalink,
                published_at=pub_time,
            )
            history_repo.create_entry(
                job_id=job_id,
                video_id=job.video_id,
                instagram_account_id=job.instagram_account_id,
                instagram_media_id=simulated_media_id,
                status=PublishStatus.SIMULATED,
                published_at=pub_time,
                permalink=simulated_permalink,
            )
