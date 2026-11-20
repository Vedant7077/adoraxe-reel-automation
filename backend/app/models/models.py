"""
SQLAlchemy Models – All database tables for Adoraxe.
Uses UUID primary keys and proper relationships.
"""
import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import (
    String, Text, Integer, BigInteger, Boolean, DateTime,
    ForeignKey, Enum as SAEnum, Index
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID as PGUUID
import enum

from app.db.database import Base


def new_uuid() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.utcnow()


# ─── Enums ────────────────────────────────────────────────────────────────────

class VideoStatus(str, enum.Enum):
    IMPORTED = "IMPORTED"
    PROCESSING = "PROCESSING"
    PROCESSED = "PROCESSED"
    FAILED = "FAILED"
    READY = "READY"


class JobStatus(str, enum.Enum):
    QUEUED = "QUEUED"
    CONTAINER_CREATING = "CONTAINER_CREATING"
    CONTAINER_PROCESSING = "CONTAINER_PROCESSING"
    READY_TO_PUBLISH = "READY_TO_PUBLISH"
    PUBLISH_REQUESTED = "PUBLISH_REQUESTED"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    # Legacy / Compatibility aliases
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    FINISHED = "FINISHED"
    SIMULATED = "SIMULATED"


class PublishStatus(str, enum.Enum):
    QUEUED = "QUEUED"
    CONTAINER_CREATING = "CONTAINER_CREATING"
    CONTAINER_PROCESSING = "CONTAINER_PROCESSING"
    READY_TO_PUBLISH = "READY_TO_PUBLISH"
    PUBLISH_REQUESTED = "PUBLISH_REQUESTED"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    # Legacy / Compatibility aliases
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    SIMULATED = "SIMULATED"


class InstagramAccountStatus(str, enum.Enum):
    NOT_CONNECTED = "NOT_CONNECTED"
    CONNECTED = "CONNECTED"
    TOKEN_EXPIRED = "TOKEN_EXPIRED"
    ERROR = "ERROR"


# ─── Models ───────────────────────────────────────────────────────────────────

class Channel(Base):
    __tablename__ = "channels"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    youtube_channel_id: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(500))
    handle: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    thumbnail_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    banner_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    subscriber_count: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    video_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    channel_url: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    videos: Mapped[list["Video"]] = relationship("Video", back_populates="channel", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_channels_youtube_id", "youtube_channel_id"),
    )


class Video(Base):
    __tablename__ = "videos"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    channel_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("channels.id"), nullable=True)
    youtube_video_id: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(1000))
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    thumbnail_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    duration_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    view_count: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    video_url: Mapped[str] = mapped_column(Text)
    channel_name: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    channel_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    uploaded_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    status: Mapped[VideoStatus] = mapped_column(SAEnum(VideoStatus), default=VideoStatus.IMPORTED)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    channel: Mapped[Optional["Channel"]] = relationship("Channel", back_populates="videos")
    assets: Mapped[list["VideoAsset"]] = relationship("VideoAsset", back_populates="video", cascade="all, delete-orphan")
    publishing_jobs: Mapped[list["PublishingJob"]] = relationship("PublishingJob", back_populates="video")

    __table_args__ = (
        Index("ix_videos_youtube_id", "youtube_video_id"),
        Index("ix_videos_status", "status"),
    )


class VideoAsset(Base):
    __tablename__ = "video_assets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    video_id: Mapped[str] = mapped_column(String(36), ForeignKey("videos.id"), nullable=False)
    original_filename: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    file_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # local temp path
    cloudinary_public_id: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    cloudinary_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    duration_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    width: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    height: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    codec: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    file_size_bytes: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    is_processed: Mapped[bool] = mapped_column(Boolean, default=False)
    is_reel_compatible: Mapped[bool] = mapped_column(Boolean, default=False)
    crop_mode: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    processing_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    video: Mapped["Video"] = relationship("Video", back_populates="assets")


class InstagramAccount(Base):
    __tablename__ = "instagram_accounts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    instagram_user_id: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    username: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    profile_picture_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    account_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # BUSINESS or CREATOR
    followers_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    following_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Encrypted token storage – NEVER store plaintext tokens
    encrypted_access_token: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    token_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    status: Mapped[InstagramAccountStatus] = mapped_column(
        SAEnum(InstagramAccountStatus), default=InstagramAccountStatus.NOT_CONNECTED
    )
    connected_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    publishing_jobs: Mapped[list["PublishingJob"]] = relationship("PublishingJob", back_populates="instagram_account")
    publishing_history: Mapped[list["PublishingHistory"]] = relationship("PublishingHistory", back_populates="instagram_account")


class PublishingJob(Base):
    __tablename__ = "publishing_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    video_id: Mapped[str] = mapped_column(String(36), ForeignKey("videos.id"), nullable=False)
    instagram_account_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("instagram_accounts.id"), nullable=True)
    video_asset_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("video_assets.id"), nullable=True)
    caption: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(50), default=JobStatus.QUEUED.value)
    current_stage: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, default="QUEUED")
    attempt_count: Mapped[int] = mapped_column(Integer, default=1)
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, unique=True)
    # Instagram container and media IDs
    instagram_container_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    instagram_media_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    cloudinary_public_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    cloudinary_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    permalink: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    processing_progress: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    diagnostic_metadata: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    rights_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    scheduled_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    @property
    def job_id(self) -> str:
        return self.id

    @property
    def job_status(self) -> str:
        return self.status

    @property
    def progress(self) -> int:
        return self.processing_progress or 0

    @property
    def cloudinary_secure_url(self) -> Optional[str]:
        return self.cloudinary_url

    @property
    def last_error(self) -> Optional[str]:
        return self.error_message

    video: Mapped["Video"] = relationship("Video", back_populates="publishing_jobs")
    instagram_account: Mapped[Optional["InstagramAccount"]] = relationship("InstagramAccount", back_populates="publishing_jobs")

    __table_args__ = (
        Index("ix_publishing_jobs_status", "status"),
        Index("ix_publishing_jobs_video_id", "video_id"),
    )


class PublishingHistory(Base):
    __tablename__ = "publishing_history"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    publishing_job_id: Mapped[str] = mapped_column(String(36), ForeignKey("publishing_jobs.id"), nullable=False)
    video_id: Mapped[str] = mapped_column(String(36), ForeignKey("videos.id"), nullable=False)
    instagram_account_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("instagram_accounts.id"), nullable=True)
    video_title: Mapped[str] = mapped_column(String(1000))
    instagram_username: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    instagram_media_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(50), default=PublishStatus.QUEUED.value)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    permalink: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    instagram_account: Mapped[Optional["InstagramAccount"]] = relationship("InstagramAccount", back_populates="publishing_history")

    __table_args__ = (
        Index("ix_publishing_history_status", "status"),
    )
