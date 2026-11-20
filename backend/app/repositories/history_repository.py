"""Publishing history repository."""
from datetime import datetime
from typing import Optional, List, Tuple
from sqlalchemy.orm import Session
from app.models.models import PublishingHistory, PublishingJob, PublishStatus


class HistoryRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_all(self, page: int = 1, page_size: int = 20, status: str = None) -> Tuple[List[PublishingHistory], int]:
        q = self.db.query(PublishingHistory)
        if status:
            q = q.filter(PublishingHistory.status == status)
        total = q.count()
        items = q.order_by(PublishingHistory.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
        return items, total

    def create_or_update_published(
        self,
        job_id: str,
        video_id: str,
        instagram_account_id: Optional[str],
        media_id: Optional[str],
        permalink: Optional[str] = None,
        published_at: Optional[datetime] = None,
    ) -> PublishingHistory:
        """Create or update a PUBLISHED history entry. Guaranteed not to duplicate."""
        existing = self.db.query(PublishingHistory).filter(PublishingHistory.publishing_job_id == job_id).first()
        if existing:
            existing.status = PublishStatus.PUBLISHED.value
            existing.instagram_media_id = media_id
            if permalink:
                existing.permalink = permalink
            if published_at:
                existing.published_at = published_at
            existing.error_message = None
            self.db.commit()
            return existing

        from app.models.models import Video, InstagramAccount
        video = self.db.query(Video).filter(Video.id == video_id).first()
        video_title = video.title if video else "Unknown"

        ig_username = None
        if instagram_account_id:
            ig_account = self.db.query(InstagramAccount).filter(InstagramAccount.id == instagram_account_id).first()
            ig_username = ig_account.username if ig_account else None

        entry = PublishingHistory(
            publishing_job_id=job_id,
            video_id=video_id,
            instagram_account_id=instagram_account_id,
            video_title=video_title,
            instagram_username=ig_username,
            instagram_media_id=media_id,
            permalink=permalink,
            status=PublishStatus.PUBLISHED.value,
            published_at=published_at or datetime.utcnow(),
        )
        self.db.add(entry)
        self.db.commit()
        return entry

    def create_entry(
        self,
        job_id: str,
        video_id: str,
        instagram_account_id: Optional[str],
        instagram_media_id: Optional[str],
        status: PublishStatus,
        published_at: Optional[datetime],
        permalink: Optional[str] = None,
    ):
        status_val = status.value if hasattr(status, "value") else str(status)
        if status_val == PublishStatus.PUBLISHED.value:
            return self.create_or_update_published(
                job_id=job_id,
                video_id=video_id,
                instagram_account_id=instagram_account_id,
                media_id=instagram_media_id,
                permalink=permalink,
                published_at=published_at,
            )

        from app.models.models import Video, InstagramAccount
        video = self.db.query(Video).filter(Video.id == video_id).first()
        video_title = video.title if video else "Unknown"

        ig_username = None
        if instagram_account_id:
            ig_account = self.db.query(InstagramAccount).filter(InstagramAccount.id == instagram_account_id).first()
            ig_username = ig_account.username if ig_account else None

        entry = PublishingHistory(
            publishing_job_id=job_id,
            video_id=video_id,
            instagram_account_id=instagram_account_id,
            video_title=video_title,
            instagram_username=ig_username,
            instagram_media_id=instagram_media_id,
            permalink=permalink,
            status=status_val,
            published_at=published_at,
        )
        self.db.add(entry)
        self.db.commit()
        return entry

    def create_entry_failed(self, job_id: str, error: str):
        """
        Record a failure in publishing history.
        CRITICAL: Never overwrite a previously PUBLISHED status with FAILED.
        """
        job = self.db.query(PublishingJob).filter(PublishingJob.id == job_id).first()
        if not job:
            return

        existing = self.db.query(PublishingHistory).filter(PublishingHistory.publishing_job_id == job_id).first()

        # If job or history is already PUBLISHED, never overwrite with FAILED
        if (existing and str(existing.status) in (PublishStatus.PUBLISHED.value, "PUBLISHED")) or (str(job.status) in (PublishStatus.PUBLISHED.value, "PUBLISHED")):
            return existing

        if existing:
            existing.status = PublishStatus.FAILED.value
            existing.error_message = error or "Publishing failed"
            self.db.commit()
            return existing

        from app.models.models import Video, InstagramAccount
        video = self.db.query(Video).filter(Video.id == job.video_id).first()
        ig_username = None
        if job.instagram_account_id:
            ig_acc = self.db.query(InstagramAccount).filter(InstagramAccount.id == job.instagram_account_id).first()
            ig_username = ig_acc.username if ig_acc else None

        entry = PublishingHistory(
            publishing_job_id=job_id,
            video_id=job.video_id,
            instagram_account_id=job.instagram_account_id,
            video_title=video.title if video else "Unknown",
            instagram_username=ig_username,
            status=PublishStatus.FAILED.value,
            error_message=error or "Publishing failed",
        )
        self.db.add(entry)
        self.db.commit()
        return entry
