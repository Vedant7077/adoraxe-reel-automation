"""
Pydantic Schemas for request/response validation.
Ensures timezone-aware ISO 8601 UTC timestamps with 'Z' suffix.
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Optional, List
from pydantic import BaseModel, HttpUrl, field_validator, field_serializer
from app.models.models import VideoStatus, JobStatus, PublishStatus, InstagramAccountStatus


class AdoraxeBaseModel(BaseModel):
    """Base schema ensuring timezone-aware ISO 8601 UTC timestamps (with Z) in JSON output."""
    model_config = {"from_attributes": True}

    @field_serializer(
        "created_at",
        "updated_at",
        "published_at",
        "last_synced_at",
        "uploaded_at",
        "started_at",
        "completed_at",
        "token_expires_at",
        "connected_at",
        check_fields=False,
    )
    def serialize_utc_datetime(self, dt: Optional[datetime]) -> Optional[str]:
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# ─── Channel Schemas ──────────────────────────────────────────────────────────

class ChannelImportRequest(BaseModel):
    url: str

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("URL cannot be empty")
        return v


class ChannelResponse(AdoraxeBaseModel):
    id: str
    youtube_channel_id: str
    name: str
    handle: Optional[str] = None
    description: Optional[str] = None
    thumbnail_url: Optional[str] = None
    subscriber_count: Optional[int] = None
    video_count: Optional[int] = None
    channel_url: str
    is_active: bool
    last_synced_at: Optional[datetime] = None
    created_at: datetime


# ─── Video Schemas ────────────────────────────────────────────────────────────

class VideoAssetResponse(AdoraxeBaseModel):
    id: str
    video_id: str
    original_filename: Optional[str] = None
    duration_seconds: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None
    codec: Optional[str] = None
    file_size_bytes: Optional[int] = None
    is_processed: bool = False
    is_reel_compatible: bool = False
    crop_mode: Optional[str] = None
    processing_error: Optional[str] = None
    has_original_file: bool = False
    has_processed_file: bool = False
    original_stream_url: Optional[str] = None
    processed_stream_url: Optional[str] = None
    cloudinary_url: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class VideoResponse(AdoraxeBaseModel):
    id: str
    youtube_video_id: str
    title: str
    description: Optional[str] = None
    thumbnail_url: Optional[str] = None
    duration_seconds: Optional[int] = None
    view_count: Optional[int] = None
    video_url: str
    channel_name: Optional[str] = None
    status: VideoStatus
    assets: List[VideoAssetResponse] = []
    has_uploaded_video: bool = False
    has_processed_reel: bool = False
    crop_mode: Optional[str] = None
    original_video_url: Optional[str] = None
    processed_video_url: Optional[str] = None
    active_asset: Optional[VideoAssetResponse] = None
    active_job_id: Optional[str] = None
    is_processing: bool = False
    uploaded_at: Optional[datetime] = None
    created_at: datetime


class VideoListResponse(BaseModel):
    items: List[VideoResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


# ─── Video Processing ─────────────────────────────────────────────────────────

class MediaRetrieveRequest(BaseModel):
    media_url: Optional[str] = None
    auto_process: bool = False
    crop_mode: str = "blur"  # "blur", "pad", or "crop"
    reuse_existing: bool = True
    use_ytdlp: bool = False
    rights_confirmed: bool = False


class MediaRetrieveResponse(BaseModel):
    asset_id: str
    video_id: str
    status: str  # "RETRIEVED", "REUSED", "MEDIA_NOT_AVAILABLE", "YTDLP_RETRIEVED"
    message: str
    provider: Optional[str] = "direct"  # "direct", "ytdlp", "manual"
    source_url: Optional[str] = None
    original_filename: Optional[str] = None
    duration_seconds: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None
    codec: Optional[str] = None
    file_size_bytes: Optional[int] = None
    original_stream_url: str
    job_id: Optional[str] = None


class YtDlpStatusResponse(BaseModel):
    enabled: bool
    ytdlp_available: bool
    ytdlp_version: Optional[str] = None
    ytdlp_ejs_available: bool
    ytdlp_ejs_version: Optional[str] = None
    ffmpeg_available: bool
    ffmpeg_path: Optional[str] = None
    ffprobe_available: bool
    ffprobe_path: Optional[str] = None
    js_runtime: Optional[str] = None
    js_runtime_supported: bool = False
    js_runtime_name: Optional[str] = None
    js_runtime_version: Optional[str] = None
    max_file_size_mb: int = 2048


class VideoUploadResponse(BaseModel):
    id: str
    video_id: str
    message: str


class ProcessVideoRequest(BaseModel):
    crop_mode: str = "blur"  # "blur", "pad", or "crop"
    target_width: int = 1080
    target_height: int = 1920


class JobStatusResponse(AdoraxeBaseModel):
    id: str
    status: JobStatus
    current_stage: Optional[str] = None
    progress: int = 0
    video_id: Optional[str] = None
    instagram_account_id: Optional[str] = None
    caption: Optional[str] = None
    instagram_container_id: Optional[str] = None
    instagram_media_id: Optional[str] = None
    permalink: Optional[str] = None
    error_message: Optional[str] = None
    cloudinary_url: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    published_at: Optional[datetime] = None


class ActiveJobResponse(BaseModel):
    has_active_job: bool
    job: Optional[JobStatusResponse] = None


# ─── Instagram Schemas ────────────────────────────────────────────────────────

class InstagramAccountItem(AdoraxeBaseModel):
    id: str
    instagram_user_id: str
    username: str
    full_name: Optional[str] = None
    profile_picture_url: Optional[str] = None
    account_type: Optional[str] = None
    status: InstagramAccountStatus
    is_active: bool = False
    is_demo: bool = False
    connected_at: Optional[datetime] = None


class InstagramStatusResponse(AdoraxeBaseModel):
    status: InstagramAccountStatus
    account_id: Optional[str] = None
    instagram_user_id: Optional[str] = None
    username: Optional[str] = None
    full_name: Optional[str] = None
    profile_picture_url: Optional[str] = None
    account_type: Optional[str] = None
    followers_count: Optional[int] = None
    token_expires_at: Optional[datetime] = None
    connected_at: Optional[datetime] = None
    instagram_configured: bool
    demo_mode: bool
    is_demo_placeholder: bool = False

    # Meta App & Unified Login Details
    meta_app_name: Optional[str] = None
    meta_app_id: Optional[str] = None
    meta_config_id: Optional[str] = None
    meta_api_version: Optional[str] = None

    # Token Health & Security Metadata
    token_validity: Optional[str] = None  # "VALID", "INVALID", "UNKNOWN", "UNVERIFIED"
    token_type: Optional[str] = None       # e.g. "SYSTEM_USER"
    expires_at_raw: Optional[int] = None
    data_access_expires_at: Optional[int] = None
    token_status_message: Optional[str] = None
    last_verified_at: Optional[datetime] = None

    # Granted Scopes & Verification
    granted_scopes: List[str] = []
    required_scopes: List[str] = ["instagram_basic", "instagram_content_publish"]
    additional_scopes: List[str] = []
    missing_scopes: List[str] = []

    # Available accounts
    connected_accounts: List[InstagramAccountItem] = []


class InstagramConnectResponse(BaseModel):
    auth_url: str


# ─── Publishing Schemas ───────────────────────────────────────────────────────

class PublishRequest(BaseModel):
    video_id: str
    caption: str
    rights_confirmed: bool
    instagram_account_id: Optional[str] = None

    @field_validator("caption")
    @classmethod
    def validate_caption(cls, v: str) -> str:
        if len(v) > 2200:
            raise ValueError("Caption cannot exceed 2200 characters")
        return v

    @field_validator("rights_confirmed")
    @classmethod
    def must_confirm_rights(cls, v: bool) -> bool:
        if not v:
            raise ValueError("You must confirm publishing rights before publishing")
        return v


class PublishResponse(BaseModel):
    job_id: str
    status: JobStatus
    current_stage: Optional[str] = "QUEUED"
    message: str


class QueueVideoItem(AdoraxeBaseModel):
    id: str
    title: str
    thumbnail_url: Optional[str] = None
    channel_name: Optional[str] = None
    duration_seconds: Optional[int] = None
    status: VideoStatus
    is_already_published: bool = False
    is_publishing_in_progress: bool = False
    can_publish: bool = True
    disabled_reason: Optional[str] = None
    cloudinary_url: Optional[str] = None
    crop_mode: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None


# ─── History Schemas ──────────────────────────────────────────────────────────

class PublishingHistoryResponse(AdoraxeBaseModel):
    id: str
    publishing_job_id: Optional[str] = None
    video_id: Optional[str] = None
    video_title: str
    instagram_username: Optional[str] = None
    instagram_media_id: Optional[str] = None
    permalink: Optional[str] = None
    status: PublishStatus
    error_message: Optional[str] = None
    published_at: Optional[datetime] = None
    created_at: datetime


class HistoryListResponse(BaseModel):
    items: List[PublishingHistoryResponse]
    total: int


# ─── Health Schema ────────────────────────────────────────────────────────────

class HealthResponse(BaseModel):
    status: str
    version: str
    demo_mode: bool
    database: bool
    ffmpeg: bool
    cloudinary_configured: bool
    instagram_configured: bool
