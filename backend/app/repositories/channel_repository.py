"""Channel repository."""
from sqlalchemy.orm import Session
from typing import List, Optional
from app.models.models import Channel
from app.services.youtube.metadata_provider import ChannelMeta
from datetime import datetime


class ChannelRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_by_id(self, channel_id: str) -> Optional[Channel]:
        return self.db.query(Channel).filter(Channel.id == channel_id).first()

    def get_by_youtube_id(self, youtube_channel_id: str) -> Optional[Channel]:
        return self.db.query(Channel).filter(Channel.youtube_channel_id == youtube_channel_id).first()

    def get_all(self) -> List[Channel]:
        return self.db.query(Channel).filter(Channel.is_active == True).order_by(Channel.created_at.desc()).all()

    def upsert_channel(self, meta: ChannelMeta) -> Channel:
        existing = self.get_by_youtube_id(meta.youtube_channel_id)
        if existing:
            existing.name = meta.name
            existing.handle = meta.handle
            existing.description = meta.description
            existing.thumbnail_url = meta.thumbnail_url
            existing.subscriber_count = meta.subscriber_count
            existing.video_count = meta.video_count
            existing.channel_url = meta.channel_url
            existing.last_synced_at = datetime.utcnow()
            self.db.commit()
            self.db.refresh(existing)
            return existing
        channel = Channel(
            youtube_channel_id=meta.youtube_channel_id,
            name=meta.name,
            handle=meta.handle,
            description=meta.description,
            thumbnail_url=meta.thumbnail_url,
            subscriber_count=meta.subscriber_count,
            video_count=meta.video_count,
            channel_url=meta.channel_url,
            last_synced_at=datetime.utcnow(),
        )
        self.db.add(channel)
        self.db.commit()
        self.db.refresh(channel)
        return channel
