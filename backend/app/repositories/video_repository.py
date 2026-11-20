"""Video repository."""
from datetime import datetime
from sqlalchemy.orm import Session
from typing import List, Optional, Tuple
from app.models.models import Video, VideoAsset, VideoStatus
from app.services.youtube.metadata_provider import VideoMeta


class VideoRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_by_id(self, video_id: str) -> Optional[Video]:
        return self.db.query(Video).filter(Video.id == video_id).first()

    def get_by_youtube_id(self, youtube_video_id: str) -> Optional[Video]:
        return self.db.query(Video).filter(Video.youtube_video_id == youtube_video_id).first()

    def get_all(self, page: int = 1, page_size: int = 24, status: str = None, search: str = None) -> Tuple[List[Video], int]:
        q = self.db.query(Video)
        if status:
            q = q.filter(Video.status == status)
        if search:
            q = q.filter(Video.title.ilike(f"%{search}%"))
        total = q.count()
        items = q.order_by(Video.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
        return items, total

    def get_by_channel(self, channel_id: str, page: int = 1, page_size: int = 24) -> Tuple[List[Video], int]:
        q = self.db.query(Video).filter(Video.channel_id == channel_id)
        total = q.count()
        items = q.order_by(Video.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
        return items, total

    def upsert_video(self, meta: VideoMeta, channel_id: Optional[str] = None) -> Video:
        existing = self.get_by_youtube_id(meta.youtube_video_id)
        if existing:
            existing.title = meta.title
            existing.description = meta.description
            existing.thumbnail_url = meta.thumbnail_url
            existing.duration_seconds = meta.duration_seconds
            existing.view_count = meta.view_count
            existing.video_url = meta.video_url
            if channel_id:
                existing.channel_id = channel_id
            if meta.channel_name:
                existing.channel_name = meta.channel_name
            self.db.commit()
            self.db.refresh(existing)
            return existing

        video = Video(
            channel_id=channel_id,
            youtube_video_id=meta.youtube_video_id,
            title=meta.title,
            description=meta.description,
            thumbnail_url=meta.thumbnail_url,
            duration_seconds=meta.duration_seconds,
            view_count=meta.view_count,
            video_url=meta.video_url,
            channel_name=meta.channel_name,
            channel_url=meta.channel_url,
            uploaded_at=meta.uploaded_at,
            status=VideoStatus.IMPORTED,
        )
        self.db.add(video)
        self.db.commit()
        self.db.refresh(video)
        return video

    def get_original_asset(self, video_id: str) -> Optional[VideoAsset]:
        """Get original video asset (is_processed=False), or fallback to oldest asset for historical records."""
        orig = (
            self.db.query(VideoAsset)
            .filter(VideoAsset.video_id == video_id, VideoAsset.is_processed == False)
            .order_by(VideoAsset.created_at.desc())
            .first()
        )
        if orig:
            return orig
        # Historical compatibility: single asset marked processed but containing original file_path
        return (
            self.db.query(VideoAsset)
            .filter(VideoAsset.video_id == video_id)
            .order_by(VideoAsset.created_at.asc())
            .first()
        )

    def get_processed_asset(self, video_id: str) -> Optional[VideoAsset]:
        """Get processed Reel asset (is_processed=True)."""
        return (
            self.db.query(VideoAsset)
            .filter(VideoAsset.video_id == video_id, VideoAsset.is_processed == True)
            .order_by(VideoAsset.updated_at.desc(), VideoAsset.created_at.desc())
            .first()
        )

    def create_asset(self, video_id: Optional[str], file_path: str, original_filename: str) -> VideoAsset:
        asset = VideoAsset(
            video_id=video_id,
            file_path=file_path,
            original_filename=original_filename,
            is_processed=False,
            is_reel_compatible=False,
        )
        self.db.add(asset)
        self.db.commit()
        self.db.refresh(asset)
        return asset

    def create_or_update_reel_asset(
        self,
        video_id: str,
        cloudinary_url: str,
        cloudinary_public_id: Optional[str],
        original_filename: str,
        duration: int,
        width: int,
        height: int,
        codec: str,
        file_size: int,
        file_path: Optional[str] = None,
        crop_mode: Optional[str] = None,
    ) -> VideoAsset:
        """Create or update separate processed Reel asset in PostgreSQL."""
        existing = (
            self.db.query(VideoAsset)
            .filter(VideoAsset.video_id == video_id, VideoAsset.is_processed == True)
            .first()
        )
        if existing:
            existing.cloudinary_url = cloudinary_url
            existing.cloudinary_public_id = cloudinary_public_id
            existing.original_filename = original_filename
            existing.duration_seconds = duration
            existing.width = width
            existing.height = height
            existing.codec = codec
            existing.file_size_bytes = file_size
            existing.is_processed = True
            existing.is_reel_compatible = True
            existing.updated_at = datetime.utcnow()
            if crop_mode is not None:
                existing.crop_mode = crop_mode
            if file_path is not None:
                existing.file_path = file_path
            self.db.commit()
            self.db.refresh(existing)
            return existing

        reel_asset = VideoAsset(
            video_id=video_id,
            file_path=file_path,
            original_filename=original_filename,
            cloudinary_url=cloudinary_url,
            cloudinary_public_id=cloudinary_public_id,
            duration_seconds=duration,
            width=width,
            height=height,
            codec=codec,
            file_size_bytes=file_size,
            is_processed=True,
            is_reel_compatible=True,
            crop_mode=crop_mode,
        )
        self.db.add(reel_asset)
        self.db.commit()
        self.db.refresh(reel_asset)
        return reel_asset

    def update_asset_metadata(self, asset_id: str, duration: int, width: int, height: int, codec: str, file_size: int):
        asset = self.db.query(VideoAsset).filter(VideoAsset.id == asset_id).first()
        if asset:
            asset.duration_seconds = duration
            asset.width = width
            asset.height = height
            asset.codec = codec
            asset.file_size_bytes = file_size
            self.db.commit()

    def update_asset_cloudinary(
        self,
        asset_id: str,
        cloudinary_url: str,
        cloudinary_public_id: Optional[str],
    ):
        """Update Cloudinary URL and public ID on an existing asset."""
        asset = self.db.query(VideoAsset).filter(VideoAsset.id == asset_id).first()
        if asset:
            asset.cloudinary_url = cloudinary_url
            asset.cloudinary_public_id = cloudinary_public_id
            self.db.commit()

    def clear_asset_local_path(self, asset_id: str):
        """Clear local file path after confirmed Cloudinary upload and verification."""
        asset = self.db.query(VideoAsset).filter(VideoAsset.id == asset_id).first()
        if asset:
            asset.file_path = None
            self.db.commit()

    def mark_asset_processed(self, asset_id: str, cloudinary_url: str, cloudinary_public_id: Optional[str]):
        asset = self.db.query(VideoAsset).filter(VideoAsset.id == asset_id).first()
        if asset:
            asset.cloudinary_url = cloudinary_url
            asset.cloudinary_public_id = cloudinary_public_id
            asset.is_processed = True
            asset.is_reel_compatible = True
            self.db.commit()

    def update_status(self, video_id: str, status: VideoStatus):
        video = self.get_by_id(video_id)
        if video:
            video.status = status
            self.db.commit()
