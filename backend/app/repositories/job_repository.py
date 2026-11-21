"""Job repository – publishing jobs with persistent state machine and idempotency."""
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import or_

from app.models.models import PublishingJob, JobStatus, PublishStatus

logger = logging.getLogger(__name__)

# Allowed state machine transitions (enforcing monotonic progression and protecting PUBLISHED)
ALLOWED_TRANSITIONS = {
    JobStatus.QUEUED.value: {
        JobStatus.CONTAINER_CREATING.value,
        JobStatus.CONTAINER_PROCESSING.value,
        JobStatus.PROCESSING.value,
        JobStatus.FAILED.value,
        JobStatus.SIMULATED.value,
        JobStatus.PUBLISHED.value,
    },
    JobStatus.CONTAINER_CREATING.value: {
        JobStatus.CONTAINER_PROCESSING.value,
        JobStatus.FAILED.value,
    },
    JobStatus.CONTAINER_PROCESSING.value: {
        JobStatus.READY_TO_PUBLISH.value,
        JobStatus.FAILED.value,
        JobStatus.PUBLISHED.value,
    },
    JobStatus.READY_TO_PUBLISH.value: {
        JobStatus.PUBLISH_REQUESTED.value,
        JobStatus.FAILED.value,
    },
    JobStatus.PUBLISH_REQUESTED.value: {
        JobStatus.PUBLISHED.value,
        JobStatus.OUTCOME_UNKNOWN.value,
        JobStatus.FINISHED.value,
    },
    JobStatus.OUTCOME_UNKNOWN.value: {
        JobStatus.PUBLISHED.value,
        JobStatus.RECONCILIATION_REQUIRED.value,
        JobStatus.FAILED.value,
    },
    JobStatus.RECONCILIATION_REQUIRED.value: {
        JobStatus.PUBLISHED.value,
        JobStatus.FAILED.value,
    },
    JobStatus.PENDING.value: {
        JobStatus.QUEUED.value,
        JobStatus.PROCESSING.value,
        JobStatus.CONTAINER_CREATING.value,
        JobStatus.CONTAINER_PROCESSING.value,
        JobStatus.FINISHED.value,
        JobStatus.FAILED.value,
        JobStatus.SIMULATED.value,
        JobStatus.PUBLISHED.value,
    },
    JobStatus.PROCESSING.value: {
        JobStatus.PROCESSING.value,
        JobStatus.CONTAINER_CREATING.value,
        JobStatus.CONTAINER_PROCESSING.value,
        JobStatus.READY_TO_PUBLISH.value,
        JobStatus.PUBLISH_REQUESTED.value,
        JobStatus.FINISHED.value,
        JobStatus.FAILED.value,
        JobStatus.SIMULATED.value,
        JobStatus.PUBLISHED.value,
        JobStatus.OUTCOME_UNKNOWN.value,
    },
    JobStatus.PUBLISHED.value: set(),  # Terminal state: NEVER allows transition to FAILED
    JobStatus.FINISHED.value: set(),   # Terminal state
    JobStatus.FAILED.value: set(),     # Terminal state
    JobStatus.SIMULATED.value: set(),  # Terminal state
}

ACTIVE_STATUS_VALUES = [
    JobStatus.QUEUED.value,
    JobStatus.CONTAINER_CREATING.value,
    JobStatus.CONTAINER_PROCESSING.value,
    JobStatus.READY_TO_PUBLISH.value,
    JobStatus.PUBLISH_REQUESTED.value,
    JobStatus.OUTCOME_UNKNOWN.value,
    JobStatus.RECONCILIATION_REQUIRED.value,
    JobStatus.PENDING.value,
    JobStatus.PROCESSING.value,
]


class JobRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_by_id(self, job_id: str) -> Optional[PublishingJob]:
        return self.db.query(PublishingJob).filter(PublishingJob.id == job_id).first()

    def get_by_idempotency_key(self, idempotency_key: str) -> Optional[PublishingJob]:
        if not idempotency_key:
            return None
        return self.db.query(PublishingJob).filter(PublishingJob.idempotency_key == idempotency_key).first()

    def get_active_job_for_video(self, video_id: str, account_id: Optional[str] = None) -> Optional[PublishingJob]:
        from app.repositories.instagram_repository import DEMO_ACCOUNT_ID

        q = self.db.query(PublishingJob).filter(
            PublishingJob.video_id == video_id,
            PublishingJob.status.in_(ACTIVE_STATUS_VALUES),
        )
        if account_id:
            if account_id == DEMO_ACCOUNT_ID:
                q = q.filter(or_(PublishingJob.instagram_account_id == DEMO_ACCOUNT_ID, PublishingJob.instagram_account_id.is_(None)))
            else:
                q = q.filter(PublishingJob.instagram_account_id == account_id)
        return q.first()

    def get_latest_active_job(self, account_id: Optional[str] = None) -> Optional[PublishingJob]:
        """
        Retrieve the latest in-flight or recently completed publishing job.
        Used by the frontend to restore publishing state across page navigation and reloads.
        """
        from app.repositories.instagram_repository import DEMO_ACCOUNT_ID

        q = self.db.query(PublishingJob)
        if account_id:
            if account_id == DEMO_ACCOUNT_ID:
                q = q.filter(or_(PublishingJob.instagram_account_id == DEMO_ACCOUNT_ID, PublishingJob.instagram_account_id.is_(None)))
            else:
                q = q.filter(PublishingJob.instagram_account_id == account_id)

        # 1. First look for truly active in-flight jobs
        active = q.filter(
            PublishingJob.rights_confirmed.is_(True),
            PublishingJob.status.in_(ACTIVE_STATUS_VALUES),
        ).order_by(PublishingJob.created_at.desc()).first()

        if active:
            return active

        # 2. Look for jobs completed in the last 15 minutes to show final completion state
        fifteen_mins_ago = datetime.utcnow() - timedelta(minutes=15)
        recent = q.filter(
            PublishingJob.rights_confirmed.is_(True),
            PublishingJob.updated_at >= fifteen_mins_ago,
        ).order_by(PublishingJob.updated_at.desc()).first()

        return recent

    def get_jobs_for_startup_recovery(self) -> List[PublishingJob]:
        """Find pending/interrupted publishing jobs on application boot."""
        return self.db.query(PublishingJob).filter(
            PublishingJob.rights_confirmed.is_(True),
            PublishingJob.status.in_([
                JobStatus.QUEUED.value,
                JobStatus.PENDING.value,
                JobStatus.CONTAINER_CREATING.value,
                JobStatus.CONTAINER_PROCESSING.value,
                JobStatus.READY_TO_PUBLISH.value,
                JobStatus.PUBLISH_REQUESTED.value,
                JobStatus.OUTCOME_UNKNOWN.value,
                JobStatus.RECONCILIATION_REQUIRED.value,
                "QUEUED",
                "PENDING",
                "CONTAINER_CREATING",
                "CONTAINER_PROCESSING",
                "READY_TO_PUBLISH",
                "PUBLISH_REQUESTED",
                "OUTCOME_UNKNOWN",
                "RECONCILIATION_REQUIRED",
            ]),
        ).all()

    CLAIM_LEASE_SECONDS = 300  # 5 minutes active worker lease

    def claim_job_for_execution(self, job_id: str, force_recovery: bool = False) -> Optional[PublishingJob]:
        """
        Atomically lock and claim a publishing job so that concurrent workers
        cannot execute the same job simultaneously.

        - Uses PostgreSQL row-level locking (FOR UPDATE SKIP LOCKED) to guarantee atomicity.
        - Checks terminal states (PUBLISHED, FAILED, SIMULATED) -> returns None.
        - Checks active lease: if another worker has an active lease (< 5 mins since last update)
          and force_recovery is False -> returns None (second worker cannot steal active job).
        - Transitions status to PROCESSING atomically if in QUEUED/PENDING.
        - Sets started_at and increments attempt_count atomically before releasing row lock.
        - If previous process crashed, startup recovery passes force_recovery=True to reclaim,
          or after 5 minutes of inactivity the lease expires naturally.
        """
        try:
            query = self.db.query(PublishingJob).filter(PublishingJob.id == job_id)
            if self.db.bind and self.db.bind.dialect.name == "postgresql":
                query = query.with_for_update(skip_locked=True)
            job = query.first()
        except Exception:
            job = self.get_by_id(job_id)

        if not job:
            return None

        current = str(job.status)
        # Cannot claim terminal states
        if current in (JobStatus.PUBLISHED.value, JobStatus.FAILED.value, JobStatus.SIMULATED.value, "PUBLISHED", "FAILED", "SIMULATED"):
            return None

        # Check if already actively claimed by another running worker
        if not force_recovery and job.started_at is not None:
            now = datetime.utcnow()
            ref_time = job.updated_at or job.started_at
            if ref_time and (now - ref_time).total_seconds() < self.CLAIM_LEASE_SECONDS:
                # Active worker holds the lease and updated recently
                return None

        now = datetime.utcnow()
        job.started_at = now
        job.updated_at = now
        job.attempt_count = (job.attempt_count or 0) + 1
        if current in (JobStatus.QUEUED.value, JobStatus.PENDING.value, "QUEUED", "PENDING"):
            job.status = JobStatus.PROCESSING.value
            job.current_stage = "PROCESSING"

        self.db.commit()
        self.db.refresh(job)
        return job

    def check_duplicate_publish(
        self,
        video_id: str,
        account_id: str,
        is_demo: bool,
    ) -> Tuple[bool, str]:
        """
        Check whether this video can be published to the given Instagram account.
        Returns (is_duplicate: bool, error_message: str).
        """
        from app.models.models import PublishingHistory, PublishStatus
        from app.repositories.instagram_repository import DEMO_ACCOUNT_ID

        def account_filter(col):
            if account_id == DEMO_ACCOUNT_ID:
                return or_(col == DEMO_ACCOUNT_ID, col.is_(None))
            return col == account_id

        # 1. Check for in-flight active publishing jobs
        active_job = self.db.query(PublishingJob).filter(
            PublishingJob.video_id == video_id,
            account_filter(PublishingJob.instagram_account_id),
            PublishingJob.rights_confirmed.is_(True),
            PublishingJob.status.in_(ACTIVE_STATUS_VALUES),
        ).first()

        if active_job:
            status_str = str(active_job.status)
            if status_str in (JobStatus.OUTCOME_UNKNOWN.value, JobStatus.RECONCILIATION_REQUIRED.value):
                return (
                    True,
                    f"Publishing outcome for this video is currently uncertain (Status: {status_str}). "
                    f"Read-only outcome reconciliation is required before any retry is permitted to prevent duplicate Reels."
                )
            stage_str = active_job.current_stage or status_str
            return (
                True,
                f"A publishing job is already in progress for this video and account (Job ID: {active_job.id}, Stage: {stage_str})."
            )

        # 2. Check for completed publish / simulated history
        if is_demo:
            existing_history = self.db.query(PublishingHistory).filter(
                PublishingHistory.video_id == video_id,
                account_filter(PublishingHistory.instagram_account_id),
                PublishingHistory.status.in_([PublishStatus.SIMULATED.value, PublishStatus.PUBLISHED.value, "SIMULATED", "PUBLISHED"])
            ).order_by(PublishingHistory.created_at.desc()).first()

            if existing_history:
                return (
                    True,
                    f"This video has already been published to this Instagram account (simulated on {existing_history.published_at or existing_history.created_at}). Duplicate publishing is prevented."
                )

            finished_job = self.db.query(PublishingJob).filter(
                PublishingJob.video_id == video_id,
                account_filter(PublishingJob.instagram_account_id),
                PublishingJob.rights_confirmed.is_(True),
                PublishingJob.status.in_([JobStatus.FINISHED.value, JobStatus.PUBLISHED.value, JobStatus.SIMULATED.value, "FINISHED", "PUBLISHED", "SIMULATED"])
            ).first()

            if finished_job:
                return (
                    True,
                    f"This video has already been published to this Instagram account (Job ID: {finished_job.id}). Duplicate publishing is prevented."
                )
        else:
            existing_history = self.db.query(PublishingHistory).filter(
                PublishingHistory.video_id == video_id,
                account_filter(PublishingHistory.instagram_account_id),
                PublishingHistory.status.in_([PublishStatus.PUBLISHED.value, "PUBLISHED"])
            ).order_by(PublishingHistory.created_at.desc()).first()

            if existing_history:
                return (
                    True,
                    f"This video has already been published to this Instagram account on {existing_history.published_at or existing_history.created_at} (Media ID: {existing_history.instagram_media_id or 'verified'}). Duplicate publishing is prevented."
                )

            finished_job = self.db.query(PublishingJob).filter(
                PublishingJob.video_id == video_id,
                account_filter(PublishingJob.instagram_account_id),
                PublishingJob.rights_confirmed.is_(True),
                PublishingJob.status.in_([JobStatus.PUBLISHED.value, JobStatus.FINISHED.value, "PUBLISHED", "FINISHED"])
            ).first()

            if finished_job:
                return (
                    True,
                    f"This video has already been published to this Instagram account (Job ID: {finished_job.id}, Media ID: {finished_job.instagram_media_id or 'verified'}). Duplicate publishing is prevented."
                )

        return (False, "")

    PROCESSING_LEASE_SECONDS = 600  # 10 minutes lease for video processing jobs

    def get_active_processing_job(self, video_id: str) -> Optional[PublishingJob]:
        """Retrieve active in-flight video conversion/processing job for a video."""
        return (
            self.db.query(PublishingJob)
            .filter(
                PublishingJob.video_id == video_id,
                PublishingJob.instagram_account_id.is_(None),
                PublishingJob.status.in_([
                    JobStatus.PENDING.value,
                    JobStatus.PROCESSING.value,
                    "PENDING",
                    "PROCESSING",
                ]),
            )
            .order_by(PublishingJob.updated_at.desc(), PublishingJob.created_at.desc())
            .first()
        )

    def is_processing_job_stale(self, job: PublishingJob, lease_seconds: Optional[int] = None) -> bool:
        """Check if a processing job was abandoned (e.g. worker crashed or container restarted)."""
        lease = lease_seconds or self.PROCESSING_LEASE_SECONDS
        ref_time = job.updated_at or job.started_at or job.created_at
        if not ref_time:
            return False
        from datetime import timezone
        if ref_time.tzinfo is not None:
            now = datetime.now(timezone.utc)
        else:
            now = datetime.utcnow()
        return (now - ref_time) > timedelta(seconds=lease)

    def create_processing_job(self, video_id: str) -> PublishingJob:
        """Create a video conversion/processing job."""
        job = PublishingJob(
            video_id=video_id,
            status=JobStatus.PENDING.value,
            current_stage="PROCESSING",
        )
        self.db.add(job)
        self.db.commit()
        self.db.refresh(job)
        return job

    def create_publish_job(
        self,
        video_id: str,
        instagram_account_id: Optional[str],
        caption: str,
        is_demo: bool,
        cloudinary_url: Optional[str] = None,
        cloudinary_public_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> PublishingJob:
        """Create and persist a publishing job before any outbound requests."""
        if idempotency_key:
            existing = self.get_by_idempotency_key(idempotency_key)
            if existing:
                if str(existing.status) in ACTIVE_STATUS_VALUES:
                    return existing
                if str(existing.status) in (JobStatus.PUBLISHED.value, JobStatus.SIMULATED.value, "PUBLISHED", "SIMULATED"):
                    return existing
                if str(existing.status) in (JobStatus.FAILED.value, "FAILED"):
                    # Retry of previously failed job: reset and re-queue existing record
                    existing.status = JobStatus.QUEUED.value
                    existing.current_stage = "QUEUED"
                    existing.processing_progress = 0
                    existing.error_message = None
                    existing.attempt_count = (existing.attempt_count or 1) + 1
                    existing.instagram_container_id = None
                    existing.instagram_media_id = None
                    existing.permalink = None
                    existing.started_at = None
                    existing.completed_at = None
                    existing.updated_at = datetime.utcnow()
                    if cloudinary_url:
                        existing.cloudinary_url = cloudinary_url
                    if cloudinary_public_id:
                        existing.cloudinary_public_id = cloudinary_public_id
                    self.db.commit()
                    self.db.refresh(existing)
                    return existing

        initial_status = JobStatus.QUEUED.value
        job = PublishingJob(
            video_id=video_id,
            instagram_account_id=instagram_account_id,
            caption=caption,
            is_demo=is_demo,
            rights_confirmed=True,
            cloudinary_url=cloudinary_url,
            cloudinary_public_id=cloudinary_public_id,
            idempotency_key=idempotency_key,
            status=initial_status,
            current_stage="QUEUED",
            attempt_count=1,
            processing_progress=0,
        )
        try:
            self.db.add(job)
            self.db.commit()
            self.db.refresh(job)
            return job
        except Exception as e:
            self.db.rollback()
            if idempotency_key:
                existing = self.get_by_idempotency_key(idempotency_key)
                if existing:
                    return existing
            raise e

    def update_status(
        self,
        job_id: str,
        status: JobStatus | str,
        progress: int = None,
        error: str = None,
        cloudinary_url: str = None,
        current_stage: str = None,
    ):
        """Update job status with transition validation and protection of terminal states."""
        job = self.get_by_id(job_id)
        if not job:
            return

        target_status = status.value if hasattr(status, "value") else str(status)
        current_status = job.status.value if hasattr(job.status, "value") else str(job.status)

        # Protect PUBLISHED state: NEVER transition backwards to FAILED
        if current_status in (JobStatus.PUBLISHED.value, "PUBLISHED") and target_status in (JobStatus.FAILED.value, "FAILED"):
            logger.warning(
                "Blocked illegal transition: Attempted to mark PUBLISHED job %s as FAILED. Keeping PUBLISHED status.",
                job_id
            )
            return

        # Check allowed transitions if defined (progress/stage updates in the same state are not state transitions)
        if current_status != target_status:
            allowed = ALLOWED_TRANSITIONS.get(current_status)
            if allowed is not None and len(allowed) > 0 and target_status not in allowed:
                logger.warning(
                    "Unusual state transition for job %s: %s -> %s",
                    job_id, current_status, target_status
                )

        job.status = target_status
        if current_stage:
            job.current_stage = current_stage
        elif not job.current_stage or job.current_stage != target_status:
            job.current_stage = target_status

        if progress is not None:
            job.processing_progress = progress
        if error is not None:
            job.error_message = error
        if cloudinary_url:
            job.cloudinary_url = cloudinary_url

        if target_status in (JobStatus.CONTAINER_CREATING.value, JobStatus.PROCESSING.value) and not job.started_at:
            job.started_at = datetime.utcnow()

        if target_status in (JobStatus.PUBLISHED.value, JobStatus.FINISHED.value, JobStatus.FAILED.value, JobStatus.SIMULATED.value):
            job.completed_at = datetime.utcnow()

        self.db.commit()

    def update_container_id(self, job_id: str, container_id: str):
        job = self.get_by_id(job_id)
        if job:
            job.instagram_container_id = container_id
            self.db.commit()

    def update_published(
        self,
        job_id: str,
        media_id: str,
        permalink: Optional[str] = None,
        published_at: Optional[datetime] = None,
    ):
        """Persist confirmed Instagram publication."""
        job = self.get_by_id(job_id)
        if not job:
            return
        pub_dt = published_at or datetime.utcnow()
        job.status = JobStatus.PUBLISHED.value
        job.current_stage = "PUBLISHED"
        job.processing_progress = 100
        job.instagram_media_id = media_id
        if permalink:
            job.permalink = permalink
        job.published_at = pub_dt
        job.completed_at = pub_dt
        job.error_message = None
        self.db.commit()
