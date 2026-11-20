"""
Tests for YouTube metadata provider abstraction and demo provider.
"""
import pytest
from app.services.youtube.metadata_provider import (
    DemoYouTubeProvider,
    BaseYouTubeProvider,
    get_youtube_provider,
    extract_youtube_video_id,
)


def test_extract_youtube_video_id():
    assert extract_youtube_video_id("https://youtu.be/Vmo4SakRHnI") == "Vmo4SakRHnI"
    assert extract_youtube_video_id("https://www.youtube.com/watch?v=Vmo4SakRHnI") == "Vmo4SakRHnI"
    assert extract_youtube_video_id("https://www.youtube.com/shorts/Vmo4SakRHnI") == "Vmo4SakRHnI"
    assert extract_youtube_video_id("https://www.youtube.com/@mkbhd") is None


@pytest.mark.asyncio
async def test_demo_provider_channel_info():
    provider = DemoYouTubeProvider()
    assert isinstance(provider, BaseYouTubeProvider)

    # Test with handle
    channel = await provider.get_channel_info("https://www.youtube.com/@mkbhd")
    assert "[DEMO]" in channel.name
    assert channel.handle == "@mkbhd"
    assert channel.subscriber_count is not None
    assert channel.video_count is not None
    assert channel.youtube_channel_id != ""

    # Test default fallback
    unknown = await provider.get_channel_info("https://www.youtube.com/@unknown_channel_xyz")
    assert "[DEMO]" in unknown.name
    assert unknown.handle == "@demo_channel"


@pytest.mark.asyncio
async def test_demo_provider_direct_video_url():
    provider = DemoYouTubeProvider()
    # Direct video URL
    channel = await provider.get_channel_info("https://youtu.be/Vmo4SakRHnI")
    assert "[DEMO]" in channel.name
    assert "Vmo4SakRHnI" in channel.youtube_channel_id

    # Videos for this channel should include the direct video first
    videos = await provider.get_channel_videos(channel.youtube_channel_id, page=1, page_size=5)
    assert len(videos) > 0
    assert videos[0].youtube_video_id == "Vmo4SakRHnI"
    assert "Vmo4SakRHnI" in videos[0].title


@pytest.mark.asyncio
async def test_demo_provider_channel_videos():
    provider = DemoYouTubeProvider()
    videos = await provider.get_channel_videos("DEMO_CHANNEL_001", page=1, page_size=6)
    assert len(videos) == 6
    for v in videos:
        assert v.youtube_video_id.startswith("DEMO_VID_")
        assert "[DEMO]" in v.title
        assert v.video_url.startswith("https://")


@pytest.mark.asyncio
async def test_demo_provider_single_video():
    provider = DemoYouTubeProvider()
    video = await provider.get_video_info("DEMO_VID_001")
    assert video.youtube_video_id == "DEMO_VID_001"
    assert "[DEMO]" in video.title


def test_provider_factory():
    provider = get_youtube_provider()
    assert isinstance(provider, BaseYouTubeProvider)
