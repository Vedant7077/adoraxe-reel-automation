"""
YouTube Metadata Provider – Abstract base + Demo + yt-dlp implementations.
Supports channel URLs, @handles, and direct video URLs.
"""
from __future__ import annotations
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, List

from app.core.config import settings


# ─── Data Classes ─────────────────────────────────────────────────────────────

@dataclass
class ChannelMeta:
    youtube_channel_id: str
    name: str
    handle: Optional[str]
    description: Optional[str]
    thumbnail_url: Optional[str]
    subscriber_count: Optional[int]
    video_count: Optional[int]
    channel_url: str


@dataclass
class VideoMeta:
    youtube_video_id: str
    title: str
    description: Optional[str]
    thumbnail_url: Optional[str]
    duration_seconds: Optional[int]
    view_count: Optional[int]
    video_url: str
    channel_name: Optional[str]
    channel_url: Optional[str]
    uploaded_at: Optional[datetime]


def extract_youtube_video_id(url: str) -> Optional[str]:
    """Extract YouTube video ID from various URL formats."""
    patterns = [
        r"youtu\.be/([a-zA-Z0-9_-]{6,15})",
        r"[?&]v=([a-zA-Z0-9_-]{6,15})",
        r"youtube\.com/shorts/([a-zA-Z0-9_-]{6,15})",
        r"youtube\.com/embed/([a-zA-Z0-9_-]{6,15})",
    ]
    for p in patterns:
        m = re.search(p, url)
        if m:
            return m.group(1)
    return None


# ─── Abstract Base ────────────────────────────────────────────────────────────

class BaseYouTubeProvider(ABC):
    @abstractmethod
    async def get_channel_info(self, url: str) -> ChannelMeta:
        """Fetch channel metadata by channel URL, @handle, or video URL."""
        ...

    @abstractmethod
    async def get_channel_videos(self, channel_id: str, page: int = 1, page_size: int = 24) -> List[VideoMeta]:
        """Fetch videos for a channel."""
        ...

    @abstractmethod
    async def get_video_info(self, video_id: str) -> VideoMeta:
        """Fetch a single video's metadata."""
        ...


# ─── Demo Provider ────────────────────────────────────────────────────────────

DEMO_CHANNELS = {
    "@mkbhd": ChannelMeta(
        youtube_channel_id="UCBJycsmduvYEL83R_U4JriQ",
        name="Marques Brownlee",
        handle="@mkbhd",
        description="Quality tech videos. YouTuber. Geek. Consumer electronics reviewer.",
        thumbnail_url="https://yt3.googleusercontent.com/ytc/AAUvwnjNoBbN1lBIA46343QVkKMR5TEL3cRQIuEaSlXN=s176-c-k-c0x00ffffff-no-rj",
        subscriber_count=18_400_000,
        video_count=1600,
        channel_url="https://www.youtube.com/@mkbhd",
    ),
    "default": ChannelMeta(
        youtube_channel_id="DEMO_CHANNEL_001",
        name="Adoraxe Creators (Demo Channel)",
        handle="@demo_channel",
        description="[DEMO DATA] Sample channel data. Configure DEMO_MODE=false with valid credentials to fetch live content.",
        thumbnail_url="https://picsum.photos/seed/adoraxe_channel/200/200",
        subscriber_count=1_250_000,
        video_count=48,
        channel_url="https://www.youtube.com/@adoraxe_creators",
    ),
}

DEMO_VIDEOS_TITLES = [
    ("M3 Max MacBook Pro Deep Dive: Worth The Hype?", 2535, 1240000),
    ("Top 5 AI Coding Tools Changing Software in 2025", 890, 840000),
    ("10 Productivity Hacks for Remote Startup Founders", 1120, 620000),
    ("How NVIDIA Became the Undisputed King of AI Hardware", 1450, 950000),
    ("Deep Dive into High-Performance Edge Computing", 780, 430000),
    ("Building the Ultimate Minimalist Studio Setup 2025", 610, 520000),
    ("The Harsh Reality of Autonomous Coding Agents", 1340, 780000),
    ("Optimal Sleep & Focus Protocols for High Performance", 3200, 2100000),
    ("How to Build a Micro-SaaS in 30 Days as a Solo Dev", 1050, 690000),
    ("Why Vector Databases Matter for Large Language Models", 940, 310000),
    ("The Next Generation of Smartphone Cameras Tested", 1180, 890000),
    ("Future of Tech: What to Expect from Quantum Computing", 1520, 710000),
]

DEMO_VIDEOS = [
    VideoMeta(
        youtube_video_id=f"DEMO_VID_{i+1:03d}",
        title=f"[DEMO] {title}",
        description=f"[DEMO DATA] Simulated video metadata for Reel generation. High retention timestamps detected at 00:45, 02:15, and 05:30.",
        thumbnail_url=f"https://picsum.photos/seed/adoraxe_vid_{i+1}/640/360",
        duration_seconds=duration,
        view_count=views,
        video_url=f"https://www.youtube.com/watch?v=DEMO_VID_{i+1:03d}",
        channel_name="[DEMO] Marques Brownlee",
        channel_url="https://www.youtube.com/@mkbhd",
        uploaded_at=None,
    )
    for i, (title, duration, views) in enumerate(DEMO_VIDEOS_TITLES)
]


class DemoYouTubeProvider(BaseYouTubeProvider):
    """
    Returns clearly-labelled demo/sample data.
    Used when DEMO_MODE=true or no extraction method is configured.
    Supports channel URLs, @handles, and individual video URLs.
    NEVER presents demo data as real scraped content.
    """

    async def get_channel_info(self, url: str) -> ChannelMeta:
        # Check if URL is an individual video URL
        video_id = extract_youtube_video_id(url)
        if video_id:
            return ChannelMeta(
                youtube_channel_id=f"CH_{video_id}",
                name=f"[DEMO] YouTube Video Ingest ({video_id})",
                handle="@youtube_creator",
                description=f"[DEMO DATA] Channel metadata generated from direct video URL: {url}",
                thumbnail_url=f"https://picsum.photos/seed/{video_id}/200/200",
                subscriber_count=1_500_000,
                video_count=24,
                channel_url=url,
            )

        handle = self._extract_handle(url)
        channel = DEMO_CHANNELS.get(handle, DEMO_CHANNELS["default"])
        return ChannelMeta(
            youtube_channel_id=channel.youtube_channel_id,
            name=f"[DEMO] {channel.name.replace('[DEMO] ', '')}",
            handle=channel.handle,
            description=f"[DEMO DATA] {channel.description}",
            thumbnail_url=channel.thumbnail_url,
            subscriber_count=channel.subscriber_count,
            video_count=channel.video_count,
            channel_url=url or channel.channel_url,
        )

    async def get_channel_videos(self, channel_id: str, page: int = 1, page_size: int = 24) -> List[VideoMeta]:
        # If imported from a direct video URL
        if channel_id.startswith("CH_"):
            video_id = channel_id[3:]
            direct_video = VideoMeta(
                youtube_video_id=video_id,
                title=f"[DEMO] Video {video_id} – High-Performance Reel Candidate",
                description=f"[DEMO DATA] Video imported directly from https://youtu.be/{video_id}. Ready for 9:16 Reel processing.",
                thumbnail_url=f"https://picsum.photos/seed/{video_id}/640/360",
                duration_seconds=720,
                view_count=850000,
                video_url=f"https://youtu.be/{video_id}",
                channel_name=f"[DEMO] YouTube Video Ingest ({video_id})",
                channel_url=f"https://youtu.be/{video_id}",
                uploaded_at=None,
            )
            # Put direct video first, followed by demo catalog
            all_vids = [direct_video] + [v for v in DEMO_VIDEOS if v.youtube_video_id != video_id]
            start = (page - 1) * page_size
            return all_vids[start: start + page_size]

        start = (page - 1) * page_size
        return DEMO_VIDEOS[start: start + page_size]

    async def get_video_info(self, video_id: str) -> VideoMeta:
        # Check in demo videos
        for v in DEMO_VIDEOS:
            if v.youtube_video_id == video_id:
                return v

        # Dynamic fallback for any video_id
        return VideoMeta(
            youtube_video_id=video_id,
            title=f"[DEMO] Video {video_id} – High Engagement Clip",
            description=f"[DEMO DATA] Imported from YouTube video ID {video_id}.",
            thumbnail_url=f"https://picsum.photos/seed/{video_id}/640/360",
            duration_seconds=640,
            view_count=450000,
            video_url=f"https://youtu.be/{video_id}",
            channel_name="[DEMO] Adoraxe Creator",
            channel_url=f"https://www.youtube.com/@adoraxe_creators",
            uploaded_at=None,
        )

    def _extract_handle(self, url: str) -> str:
        match = re.search(r"@([\w.]+)", url)
        return f"@{match.group(1)}" if match else "default"


# ─── yt-dlp Provider ──────────────────────────────────────────────────────────

class YtDlpYouTubeProvider(BaseYouTubeProvider):
    """
    Fetches public YouTube metadata using yt-dlp.
    Only retrieves publicly available metadata (titles, descriptions, thumbnails).
    Does NOT download video files.
    """

    async def get_channel_info(self, url: str) -> ChannelMeta:
        try:
            import yt_dlp  # type: ignore
        except ImportError:
            raise ImportError("yt-dlp not installed. Run: pip install yt-dlp")

        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": "in_playlist",
            "playlist_items": "1",
            "skip_download": True,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        video_id = extract_youtube_video_id(url)
        channel_id = info.get("channel_id") or info.get("id", video_id or "unknown")
        name = info.get("channel") or info.get("uploader") or info.get("title", "Unknown")
        return ChannelMeta(
            youtube_channel_id=channel_id,
            name=name,
            handle=info.get("uploader_id"),
            description=info.get("description"),
            thumbnail_url=info.get("thumbnail"),
            subscriber_count=info.get("channel_follower_count"),
            video_count=info.get("playlist_count") or (1 if video_id else None),
            channel_url=info.get("channel_url") or url,
        )

    async def get_channel_videos(self, channel_id: str, page: int = 1, page_size: int = 24) -> List[VideoMeta]:
        try:
            import yt_dlp  # type: ignore
        except ImportError:
            raise ImportError("yt-dlp not installed.")

        start = (page - 1) * page_size + 1
        end = start + page_size - 1

        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": True,
            "playlist_items": f"{start}-{end}",
            "skip_download": True,
        }

        channel_url = f"https://www.youtube.com/channel/{channel_id}/videos"
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(channel_url, download=False)

        entries = info.get("entries", []) or []
        return [self._entry_to_meta(e) for e in entries if e]

    async def get_video_info(self, video_id: str) -> VideoMeta:
        try:
            import yt_dlp  # type: ignore
        except ImportError:
            raise ImportError("yt-dlp not installed.")

        url = f"https://www.youtube.com/watch?v={video_id}"
        ydl_opts = {"quiet": True, "no_warnings": True, "skip_download": True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
        return self._entry_to_meta(info)

    def _entry_to_meta(self, info: dict) -> VideoMeta:
        thumbnails = info.get("thumbnails") or []
        thumb_url = None
        if thumbnails:
            thumb_url = thumbnails[-1].get("url")
        elif info.get("thumbnail"):
            thumb_url = info["thumbnail"]

        upload_date = None
        if info.get("upload_date"):
            try:
                upload_date = datetime.strptime(info["upload_date"], "%Y%m%d")
            except ValueError:
                pass

        return VideoMeta(
            youtube_video_id=info.get("id", ""),
            title=info.get("title", "Untitled"),
            description=info.get("description"),
            thumbnail_url=thumb_url,
            duration_seconds=info.get("duration"),
            view_count=info.get("view_count"),
            video_url=info.get("webpage_url") or f"https://www.youtube.com/watch?v={info.get('id', '')}",
            channel_name=info.get("channel") or info.get("uploader"),
            channel_url=info.get("channel_url"),
            uploaded_at=upload_date,
        )


# ─── Factory ──────────────────────────────────────────────────────────────────

def get_youtube_provider(force_provider: Optional[str] = None) -> BaseYouTubeProvider:
    """
    Factory: returns appropriate YouTube metadata provider based on configuration.
    1. If force_provider or settings.youtube_provider is 'http': return HttpYouTubeScraper.
    2. If force_provider or settings.youtube_provider is 'yt_dlp': return YtDlpYouTubeProvider.
    3. If DEMO_MODE=true and no specific provider requested: return DemoYouTubeProvider.
    4. Otherwise, default to HttpYouTubeScraper for real scraping.
    """
    target = (force_provider or getattr(settings, "youtube_provider", "demo")).lower().strip()
    if target == "http":
        from app.services.youtube.http_scraper import HttpYouTubeScraper
        return HttpYouTubeScraper()

    if target == "yt_dlp":
        try:
            import yt_dlp  # type: ignore
            return YtDlpYouTubeProvider()
        except ImportError:
            pass

    if settings.demo_mode or target == "demo":
        return DemoYouTubeProvider()

    # Default to real native HTTP scraper if DEMO_MODE is false
    from app.services.youtube.http_scraper import HttpYouTubeScraper
    return HttpYouTubeScraper()
