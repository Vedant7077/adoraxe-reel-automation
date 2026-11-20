"""
Channels API routes.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List

from app.db.database import get_db
from app.schemas.schemas import ChannelImportRequest, ChannelResponse
from app.repositories.channel_repository import ChannelRepository
from app.repositories.video_repository import VideoRepository
from app.services.youtube.metadata_provider import get_youtube_provider
from app.core.config import settings

router = APIRouter()


@router.post("/import", response_model=ChannelResponse, status_code=status.HTTP_201_CREATED)
async def import_channel(
    request: ChannelImportRequest,
    db: Session = Depends(get_db),
):
    """Import a YouTube channel by channel URL, @handle, or direct video URL."""
    provider = get_youtube_provider()
    try:
        channel_meta = await provider.get_channel_info(request.url)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Could not fetch channel information: {str(e)}"
        )

    channel_repo = ChannelRepository(db)
    channel = channel_repo.upsert_channel(channel_meta)

    # Ingest available videos for this channel into the database
    video_repo = VideoRepository(db)
    try:
        videos_meta = await provider.get_channel_videos(channel.youtube_channel_id, page=1, page_size=24)
        for v_meta in videos_meta:
            video_repo.upsert_video(v_meta, channel_id=channel.id)
    except Exception:
        pass  # Channel was created successfully; video ingestion failure should not abort channel creation

    return channel


@router.get("", response_model=List[ChannelResponse])
async def list_channels(db: Session = Depends(get_db)):
    """List all imported channels."""
    repo = ChannelRepository(db)
    return repo.get_all()


@router.get("/{channel_id}/videos")
async def get_channel_videos(
    channel_id: str,
    page: int = 1,
    page_size: int = 24,
    db: Session = Depends(get_db),
):
    """Get videos for a specific channel."""
    channel_repo = ChannelRepository(db)
    channel = channel_repo.get_by_id(channel_id) or channel_repo.get_by_youtube_id(channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")

    video_repo = VideoRepository(db)
    items, total = video_repo.get_by_channel(channel.id, page=page, page_size=page_size)

    # If no videos in DB yet, fetch from provider and store
    if total == 0:
        provider = get_youtube_provider()
        try:
            videos_meta = await provider.get_channel_videos(channel.youtube_channel_id, page=page, page_size=page_size)
            for v_meta in videos_meta:
                video_repo.upsert_video(v_meta, channel_id=channel.id)
            items, total = video_repo.get_by_channel(channel.id, page=page, page_size=page_size)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Failed to fetch videos from YouTube: {str(e)}")

    return {"channel": channel, "videos": items, "page": page, "page_size": page_size, "total": total}
