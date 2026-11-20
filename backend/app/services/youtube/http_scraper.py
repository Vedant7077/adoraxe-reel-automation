"""
Native HTTP-based YouTube Metadata Scraper.
Retrieves public YouTube channel and video metadata using standard HTTP requests
against public web endpoints, strictly without YouTube Data API v3, Selenium,
Playwright, or browser automation frameworks.
"""
from __future__ import annotations
import asyncio
import json
import logging
import random
import re
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Tuple
import httpx

from app.services.youtube.metadata_provider import (
    BaseYouTubeProvider,
    ChannelMeta,
    VideoMeta,
    extract_youtube_video_id,
)

logger = logging.getLogger(__name__)


# ─── Custom Exceptions ────────────────────────────────────────────────────────

class YouTubeScrapingError(Exception):
    """Base exception for YouTube scraping failures."""
    pass


class YouTubeNotFoundError(YouTubeScrapingError):
    """Channel or video not found (HTTP 404)."""
    pass


class YouTubeRateLimitError(YouTubeScrapingError):
    """Rate limit encountered (HTTP 429)."""
    pass


class YouTubeAccessForbiddenError(YouTubeScrapingError):
    """Access forbidden or bot challenge encountered (HTTP 403)."""
    pass


class YouTubeContentUnavailableError(YouTubeScrapingError):
    """Video or channel is private, deleted, age-restricted, or region-blocked."""
    pass


class YouTubeParseError(YouTubeScrapingError):
    """Unexpected HTML or JSON structure from YouTube."""
    pass


# ─── Parser Utility Functions ─────────────────────────────────────────────────

def parse_duration_to_seconds(text: Optional[str]) -> Optional[int]:
    """Parse 'MM:SS' or 'HH:MM:SS' into integer seconds."""
    if not text:
        return None
    parts = text.strip().split(":")
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        elif len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        elif len(parts) == 1 and parts[0].isdigit():
            return int(parts[0])
    except (ValueError, TypeError):
        pass
    return None


def parse_count_number(text: Optional[str]) -> Optional[int]:
    """Parse human counts like '18.4M', '1.2K', '34 thousand', '2.69 million', '1,234,567 views' into integers."""
    if not text:
        return None
    cleaned = text.strip()
    # Remove words like 'subscribers', 'subscriber', 'views', 'view', 'videos', 'video', 'watching'
    cleaned = re.sub(r"(?i)\s*(subscribers|subscriber|views|view|videos|video|watching).*$", "", cleaned).strip()
    cleaned = cleaned.replace(",", "")

    # Multipliers
    multipliers = {
        "K": 1_000,
        "THOUSAND": 1_000,
        "M": 1_000_000,
        "MILLION": 1_000_000,
        "B": 1_000_000_000,
        "BILLION": 1_000_000_000,
    }
    match = re.match(r"^([\d.]+)\s*(k|m|b|thousand|million|billion)?$", cleaned, re.IGNORECASE)
    if match:
        val_str, unit = match.group(1), match.group(2)
        try:
            val = float(val_str)
            if unit:
                val *= multipliers.get(unit.upper(), 1)
            return int(val)
        except (ValueError, TypeError):
            pass
    return None


def extract_json_blob(html: str, variable_name: str) -> Optional[Dict[str, Any]]:
    """
    Safely extract a JSON object assigned to a JS variable (e.g. ytInitialData).
    Uses regex anchor and brace-balancing to extract the exact JSON payload.
    """
    pattern = rf"(?:var\s+|window\[['\"])?{re.escape(variable_name)}(?:['\"])?\s*=\s*"
    match = re.search(pattern, html)
    if not match:
        return None

    start_idx = match.end()
    # Find opening brace
    while start_idx < len(html) and html[start_idx] != "{":
        start_idx += 1
    if start_idx >= len(html):
        return None

    brace_count = 0
    in_string = False
    escape = False
    end_idx = start_idx

    for i in range(start_idx, len(html)):
        char = html[i]
        if escape:
            escape = False
            continue
        if char == "\\":
            escape = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if not in_string:
            if char == "{":
                brace_count += 1
            elif char == "}":
                brace_count -= 1
                if brace_count == 0:
                    end_idx = i + 1
                    break

    if brace_count != 0:
        return None

    json_str = html[start_idx:end_idx]
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        return None


# ─── Native HTTP Scraper Provider ─────────────────────────────────────────────

class HttpYouTubeScraper(BaseYouTubeProvider):
    """
    Production-grade HTTP scraper for publicly accessible YouTube channels and videos.
    Uses native HTTP GET/POST with httpx, strictly without YouTube Data API v3,
    Selenium, Playwright, or browser automation.
    """

    USER_AGENT = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0.0.0 Safari/537.36"
    )
    DEFAULT_HEADERS = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Sec-Fetch-Mode": "navigate",
    }
    INNERTUBE_URL = "https://www.youtube.com/youtubei/v1/browse?prettyPrint=false"
    MAX_RETRIES = 3
    INITIAL_BACKOFF = 1.0

    def __init__(self, timeout: float = 12.0, max_connections: int = 10):
        self.timeout = timeout
        self.limits = httpx.Limits(max_connections=max_connections, max_keepalive_connections=5)
        # In-memory continuation token cache: channel_id -> next_token
        self._continuation_cache: Dict[str, str] = {}

    async def _send_request_with_retry(
        self,
        url: str,
        method: str = "GET",
        data: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        client: Optional[httpx.AsyncClient] = None,
    ) -> httpx.Response:
        """
        Execute an HTTP request with bounded exponential backoff and jitter for transient errors.
        Permanent errors (404, 403) are never retried. Respects Retry-After on 429.
        """
        req_headers = {**self.DEFAULT_HEADERS, **(headers or {})}
        owns_client = client is None
        cli = client or httpx.AsyncClient(timeout=self.timeout, limits=self.limits, follow_redirects=True)

        try:
            for attempt in range(1, self.MAX_RETRIES + 1):
                try:
                    if method.upper() == "POST":
                        resp = await cli.post(url, json=data, headers=req_headers)
                    else:
                        resp = await cli.get(url, headers=req_headers)

                    # Explicit HTTP status handling
                    if resp.status_code == 200:
                        return resp
                    elif resp.status_code == 404:
                        raise YouTubeNotFoundError(f"YouTube resource not found (HTTP 404): {url}")
                    elif resp.status_code == 403:
                        raise YouTubeAccessForbiddenError(f"Access to YouTube resource forbidden (HTTP 403): {url}")
                    elif resp.status_code == 429:
                        if attempt == self.MAX_RETRIES:
                            raise YouTubeRateLimitError("YouTube rate limit exceeded (HTTP 429). Please slow down requests.")
                        retry_after = resp.headers.get("Retry-After")
                        sleep_time = float(retry_after) if retry_after and retry_after.isdigit() else (
                            self.INITIAL_BACKOFF * (2 ** (attempt - 1)) + random.uniform(0.1, 0.5)
                        )
                        logger.warning(f"Rate limited (429). Retrying after {sleep_time:.2f}s...")
                        await asyncio.sleep(sleep_time)
                        continue
                    elif resp.status_code in (500, 502, 503, 504):
                        if attempt == self.MAX_RETRIES:
                            raise YouTubeScrapingError(f"YouTube server error (HTTP {resp.status_code}) after {self.MAX_RETRIES} retries.")
                        sleep_time = self.INITIAL_BACKOFF * (2 ** (attempt - 1)) + random.uniform(0.1, 0.5)
                        await asyncio.sleep(sleep_time)
                        continue
                    else:
                        raise YouTubeScrapingError(f"Unexpected HTTP status {resp.status_code} from YouTube: {url}")

                except (httpx.TimeoutException, httpx.NetworkError) as e:
                    if attempt == self.MAX_RETRIES:
                        raise YouTubeScrapingError(f"Network error communicating with YouTube after {self.MAX_RETRIES} attempts: {str(e)}")
                    sleep_time = self.INITIAL_BACKOFF * (2 ** (attempt - 1)) + random.uniform(0.1, 0.5)
                    await asyncio.sleep(sleep_time)

            raise YouTubeScrapingError(f"Failed to fetch {url} after {self.MAX_RETRIES} retries.")
        finally:
            if owns_client:
                await cli.aclose()

    def _normalize_channel_url(self, raw_input: str) -> Tuple[str, str]:
        """
        Normalize raw user input (handle, full URL, or legacy username) into (channel_url, lookup_key).
        """
        s = raw_input.strip()
        # Direct video URL
        video_id = extract_youtube_video_id(s)
        if video_id:
            return f"https://www.youtube.com/watch?v={video_id}", video_id

        # Plain @handle
        if s.startswith("@"):
            return f"https://www.youtube.com/{s}/videos", s

        # Full URLs
        if s.startswith("http://") or s.startswith("https://"):
            # Ensure it targets /videos tab for listing
            clean = s.rstrip("/")
            if "/videos" not in clean and not any(clean.endswith(x) for x in ["/featured", "/shorts", "/community"]):
                return f"{clean}/videos", clean
            return clean, clean

        # Plain string -> treat as @handle
        return f"https://www.youtube.com/@{s}/videos", f"@{s}"

    async def get_channel_info(self, url: str) -> ChannelMeta:
        """
        Fetch public YouTube channel metadata by channel URL, @handle, or video URL.
        """
        # 1. If an individual video URL was provided, resolve its channel
        video_id = extract_youtube_video_id(url)
        if video_id:
            vid_meta = await self.get_video_info(video_id)
            if vid_meta.channel_url:
                try:
                    return await self.get_channel_info(vid_meta.channel_url)
                except Exception:
                    pass
            # Fallback channel representation from video info
            return ChannelMeta(
                youtube_channel_id=f"CH_{video_id}",
                name=vid_meta.channel_name or f"YouTube Video ({video_id})",
                handle="@youtube_creator",
                description=vid_meta.description,
                thumbnail_url=vid_meta.thumbnail_url,
                subscriber_count=None,
                video_count=1,
                channel_url=vid_meta.channel_url or url,
            )

        channel_url, lookup_key = self._normalize_channel_url(url)
        resp = await self._send_request_with_retry(channel_url)
        html = resp.text

        # Verify page content is not a bot challenge / CAPTCHA
        if "cf-browser-verification" in html or "Our systems have detected unusual traffic" in html:
            raise YouTubeAccessForbiddenError(
                "YouTube anti-bot challenge encountered. Automated access blocked by upstream."
            )

        data = extract_json_blob(html, "ytInitialData")
        if not data:
            raise YouTubeParseError(f"Could not parse YouTube initial metadata from {channel_url}")

        # Extract metadata from channelMetadataRenderer
        metadata_rend = data.get("metadata", {}).get("channelMetadataRenderer", {})
        header = data.get("header", {})
        c4_header = header.get("c4TabbedHeaderRenderer", {})
        page_header = header.get("pageHeaderRenderer", {}).get("content", {}).get("pageHeaderViewModel", {})

        channel_id = (
            metadata_rend.get("externalId")
            or c4_header.get("channelId")
            or self._extract_channel_id_from_html(html)
        )
        if not channel_id:
            raise YouTubeParseError("Could not locate public YouTube channel ID in response.")

        title = (
            metadata_rend.get("title")
            or c4_header.get("title")
            or self._extract_text(page_header.get("title", {}))
            or lookup_key
        )

        handle = (
            metadata_rend.get("vanityChannelUrl")
            or self._extract_handle_from_url(channel_url)
            or (f"@{lookup_key}" if not lookup_key.startswith("@") else lookup_key)
        )
        if handle and "/" in handle:
            handle = "@" + handle.split("/")[-1].lstrip("@")

        description = metadata_rend.get("description") or None

        # Best resolution avatar
        avatar_thumbnails = metadata_rend.get("avatar", {}).get("thumbnails", [])
        if not avatar_thumbnails:
            avatar_thumbnails = c4_header.get("avatar", {}).get("thumbnails", [])
        thumbnail_url = avatar_thumbnails[-1].get("url") if avatar_thumbnails else None

        # Subscriber count
        sub_text = c4_header.get("subscriberCountText", {}).get("simpleText")
        if not sub_text and "metadata" in page_header:
            # Check contentMetadataViewModel rows
            rows = page_header.get("metadata", {}).get("contentMetadataViewModel", {}).get("metadataRows", [])
            for r in rows:
                for item in r.get("metadataParts", []):
                    t = item.get("text", {}).get("content", "")
                    if "subscriber" in t.lower():
                        sub_text = t
                        break
        sub_count = parse_count_number(sub_text)

        # Video count
        vid_count_text = c4_header.get("videosCountText", {}).get("runs", [{}])[0].get("text")
        if not vid_count_text and "metadata" in page_header:
            rows = page_header.get("metadata", {}).get("contentMetadataViewModel", {}).get("metadataRows", [])
            for r in rows:
                for item in r.get("metadataParts", []):
                    t = item.get("text", {}).get("content", "")
                    if "video" in t.lower():
                        vid_count_text = t
                        break
        vid_count = parse_count_number(vid_count_text)

        canonical_url = metadata_rend.get("channelUrl") or f"https://www.youtube.com/channel/{channel_id}"

        return ChannelMeta(
            youtube_channel_id=channel_id,
            name=title,
            handle=handle,
            description=description,
            thumbnail_url=thumbnail_url,
            subscriber_count=sub_count,
            video_count=vid_count,
            channel_url=canonical_url,
        )

    async def get_channel_videos(self, channel_id: str, page: int = 1, page_size: int = 24) -> List[VideoMeta]:
        """
        Fetch public videos for a channel with continuation pagination support.
        Supports channels with zero videos, handles pagination beyond page 1,
        and eliminates duplicates.
        """
        # If imported from a direct video
        if channel_id.startswith("CH_"):
            vid_id = channel_id[3:]
            vid_info = await self.get_video_info(vid_id)
            return [vid_info]

        url = f"https://www.youtube.com/channel/{channel_id}/videos"
        resp = await self._send_request_with_retry(url)
        html = resp.text

        data = extract_json_blob(html, "ytInitialData")
        if not data:
            raise YouTubeParseError(f"Could not parse video list for channel {channel_id}")

        all_videos: List[VideoMeta] = []
        continuation_token: Optional[str] = None

        # 1. Extract first page videos from tabs
        tabs = data.get("contents", {}).get("twoColumnBrowseResultsRenderer", {}).get("tabs", [])
        grid_contents: List[Dict[str, Any]] = []

        for tab in tabs:
            tab_rend = tab.get("tabRenderer", {})
            title = tab_rend.get("title", "")
            if title.lower() in ("videos", "uploads") or tab_rend.get("selected"):
                content = tab_rend.get("content", {})
                grid = content.get("richGridRenderer", {}) or content.get("sectionListRenderer", {})
                grid_contents = grid.get("contents", [])
                if grid_contents:
                    break

        seen_ids = set()
        has_items = False
        for item in grid_contents:
            if "richItemRenderer" in item or "videoRenderer" in item or "lockupViewModel" in item:
                has_items = True

            v_meta = self._parse_video_item(item)
            if v_meta:
                if v_meta.youtube_video_id not in seen_ids:
                    seen_ids.add(v_meta.youtube_video_id)
                    all_videos.append(v_meta)
            else:
                # Check for continuation token
                cont_rend = item.get("continuationItemRenderer", {})
                endpoint = cont_rend.get("continuationEndpoint", {}).get("continuationCommand", {})
                if endpoint.get("token"):
                    continuation_token = endpoint["token"]

        if has_items and not all_videos:
            raise YouTubeParseError(
                f"Failed to parse any video items from channel {channel_id} (found {len(grid_contents)} items in grid)"
            )

        # 2. If page > 1 and continuation token exists, follow continuation pagination
        target_offset = (page - 1) * page_size
        target_count = target_offset + page_size

        while len(all_videos) < target_count and continuation_token:
            next_videos, continuation_token = await self._fetch_continuation_videos(continuation_token)
            if not next_videos:
                break
            for v_meta in next_videos:
                if v_meta.youtube_video_id not in seen_ids:
                    seen_ids.add(v_meta.youtube_video_id)
                    all_videos.append(v_meta)

        # Slice for the requested page
        start = (page - 1) * page_size
        return all_videos[start: start + page_size]

    async def get_video_info(self, video_id: str) -> VideoMeta:
        """
        Fetch metadata for a single public video without YouTube Data API v3.
        """
        url = f"https://www.youtube.com/watch?v={video_id}"
        resp = await self._send_request_with_retry(url)
        html = resp.text

        player_data = extract_json_blob(html, "ytInitialPlayerResponse")
        if not player_data:
            raise YouTubeParseError(f"Could not extract video player response for video {video_id}")

        # Check playability status
        playability = player_data.get("playabilityStatus", {})
        status = playability.get("status", "OK")
        if status in ("UNPLAYABLE", "LOGIN_REQUIRED", "ERROR"):
            reason = playability.get("reason", "This video is unavailable or restricted.")
            raise YouTubeContentUnavailableError(f"Video {video_id} is unavailable: {reason}")

        video_details = player_data.get("videoDetails", {})
        if not video_details:
            raise YouTubeParseError(f"No videoDetails found for video {video_id}")

        title = video_details.get("title", f"Video {video_id}")
        description = video_details.get("shortDescription") or None
        duration_secs = int(video_details.get("lengthSeconds", 0)) or None
        view_count = int(video_details.get("viewCount", 0)) if video_details.get("viewCount") else None
        channel_name = video_details.get("author") or None
        channel_id = video_details.get("channelId") or None
        channel_url = f"https://www.youtube.com/channel/{channel_id}" if channel_id else None

        thumbnails = video_details.get("thumbnail", {}).get("thumbnails", [])
        thumbnail_url = thumbnails[-1].get("url") if thumbnails else f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"

        return VideoMeta(
            youtube_video_id=video_id,
            title=title,
            description=description,
            thumbnail_url=thumbnail_url,
            duration_seconds=duration_secs,
            view_count=view_count,
            video_url=url,
            channel_name=channel_name,
            channel_url=channel_url,
            uploaded_at=None,
        )

    # ─── Internal Helper Methods ──────────────────────────────────────────────

    async def _fetch_continuation_videos(self, token: str) -> Tuple[List[VideoMeta], Optional[str]]:
        """
        Fetch subsequent videos using YouTube's public web continuation browse endpoint.
        """
        payload = {
            "context": {
                "client": {
                    "clientName": "WEB",
                    "clientVersion": "2.20240101.00.00",
                    "hl": "en",
                    "gl": "US",
                }
            },
            "continuation": token,
        }
        headers = {
            "Content-Type": "application/json",
            "X-YouTube-Client-Name": "1",
            "X-YouTube-Client-Version": "2.20240101.00.00",
        }

        try:
            resp = await self._send_request_with_retry(
                self.INNERTUBE_URL,
                method="POST",
                data=payload,
                headers=headers,
            )
            data = resp.json()
        except Exception as e:
            logger.warning(f"Failed to fetch continuation: {e}")
            return [], None

        actions = data.get("onResponseReceivedActions", [])
        if not actions:
            return [], None

        items: List[Dict[str, Any]] = []
        for action in actions:
            action_items = action.get("appendContinuationItemsAction", {}).get("continuationItems", [])
            items.extend(action_items)

        videos: List[VideoMeta] = []
        next_token: Optional[str] = None

        for item in items:
            v_meta = self._parse_video_item(item)
            if v_meta:
                videos.append(v_meta)
            else:
                c_rend = item.get("continuationItemRenderer", {})
                c_tok = c_rend.get("continuationEndpoint", {}).get("continuationCommand", {}).get("token")
                if c_tok:
                    next_token = c_tok

        return videos, next_token

    def _parse_video_item(self, item: Dict[str, Any], default_channel: Optional[str] = None) -> Optional[VideoMeta]:
        """Dispatch parsing between videoRenderer and lockupViewModel inside richItemRenderer or item directly."""
        content = item.get("richItemRenderer", {}).get("content", {}) if "richItemRenderer" in item else item

        # Check lockupViewModel (YouTube's newer web layout)
        lockup_vm = content.get("lockupViewModel")
        if lockup_vm:
            return self._parse_lockup_view_model(lockup_vm, default_channel=default_channel)

        # Check videoRenderer (YouTube's legacy web layout)
        v_rend = content.get("videoRenderer")
        if v_rend:
            return self._parse_video_renderer(v_rend)

        return None

    def _parse_lockup_view_model(self, model: Dict[str, Any], default_channel: Optional[str] = None) -> Optional[VideoMeta]:
        """Parse YouTube lockupViewModel dictionary into VideoMeta."""
        # 1. videoId: rendererContext.commandContext.onTap.innertubeCommand.watchEndpoint.videoId
        video_id = (
            model.get("rendererContext", {})
            .get("commandContext", {})
            .get("onTap", {})
            .get("innertubeCommand", {})
            .get("watchEndpoint", {})
            .get("videoId")
        )
        if not video_id:
            content_id = model.get("contentId")
            if content_id and len(content_id) == 11:
                video_id = content_id
            else:
                return None

        # 2. title: metadata.lockupMetadataViewModel.title.content
        lockup_meta = model.get("metadata", {}).get("lockupMetadataViewModel", {})
        title = lockup_meta.get("title", {}).get("content") or f"Video {video_id}"

        # 3. thumbnail & duration from contentImage.thumbnailViewModel
        thumb_vm = model.get("contentImage", {}).get("thumbnailViewModel", {})
        sources = thumb_vm.get("image", {}).get("sources", [])
        thumbnail_url = sources[-1].get("url") if sources else f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"

        # Duration is inside overlays -> thumbnailBottomOverlayViewModel -> badges -> thumbnailBadgeViewModel -> text
        duration_secs = None
        overlays = thumb_vm.get("overlays", [])
        for overlay in overlays:
            bottom_ov = overlay.get("thumbnailBottomOverlayViewModel", {})
            badges = bottom_ov.get("badges", [])
            for badge in badges:
                b_vm = badge.get("thumbnailBadgeViewModel", {})
                b_text = b_vm.get("text")
                if b_text:
                    duration_secs = parse_duration_to_seconds(b_text)
                    if duration_secs is not None:
                        break
            if duration_secs is not None:
                break

        # 4. View count from metadataRows
        view_count = None
        rows = lockup_meta.get("metadata", {}).get("contentMetadataViewModel", {}).get("metadataRows", [])
        for row in rows:
            for part in row.get("metadataParts", []):
                part_text = part.get("text", {}).get("content", "")
                acc_label = part.get("accessibilityLabel", "")
                leading_icon = part.get("leadingIcon", {}).get("name", "")
                if "views" in acc_label.lower() or "view" in acc_label.lower():
                    view_count = parse_count_number(acc_label)
                    if view_count is not None:
                        break
                elif leading_icon == "PLAY_ARROW_OUTLINED" and part_text:
                    view_count = parse_count_number(part_text)
                    if view_count is not None:
                        break
                elif "views" in part_text.lower() or "view" in part_text.lower():
                    view_count = parse_count_number(part_text)
                    if view_count is not None:
                        break
            if view_count is not None:
                break

        return VideoMeta(
            youtube_video_id=video_id,
            title=title,
            description=None,
            thumbnail_url=thumbnail_url,
            duration_seconds=duration_secs,
            view_count=view_count,
            video_url=f"https://www.youtube.com/watch?v={video_id}",
            channel_name=default_channel,
            channel_url=None,
            uploaded_at=None,
        )

    def _parse_video_renderer(self, rend: Dict[str, Any]) -> Optional[VideoMeta]:
        """Parse YouTube videoRenderer dictionary into VideoMeta."""
        video_id = rend.get("videoId")
        if not video_id:
            return None

        title = self._extract_text(rend.get("title", {})) or f"Video {video_id}"

        # Description snippet
        desc_snippet = rend.get("descriptionSnippet", {})
        description = self._extract_text(desc_snippet) if desc_snippet else None

        # Thumbnails
        thumbs = rend.get("thumbnail", {}).get("thumbnails", [])
        thumb_url = thumbs[-1].get("url") if thumbs else f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"

        # Duration
        dur_text = rend.get("lengthText", {}).get("simpleText")
        duration = parse_duration_to_seconds(dur_text)

        # View count
        view_text = rend.get("viewCountText", {}).get("simpleText")
        if not view_text:
            runs = rend.get("viewCountText", {}).get("runs", [])
            view_text = "".join(r.get("text", "") for r in runs)
        views = parse_count_number(view_text)

        # Channel info
        owner = rend.get("ownerText", {}) or rend.get("shortBylineText", {})
        channel_name = self._extract_text(owner)

        return VideoMeta(
            youtube_video_id=video_id,
            title=title,
            description=description,
            thumbnail_url=thumb_url,
            duration_seconds=duration,
            view_count=views,
            video_url=f"https://www.youtube.com/watch?v={video_id}",
            channel_name=channel_name,
            channel_url=None,
            uploaded_at=None,
        )

    def _extract_text(self, obj: Dict[str, Any]) -> str:
        """Helper to extract text from simpleText, runs, or dynamicTextViewModel."""
        if not obj:
            return ""
        if "simpleText" in obj:
            return obj["simpleText"]
        if "runs" in obj:
            return "".join(r.get("text", "") for r in obj["runs"])
        if "dynamicTextViewModel" in obj:
            return obj["dynamicTextViewModel"].get("text", {}).get("content", "")
        if "content" in obj:
            return obj["content"]
        return ""

    def _extract_channel_id_from_html(self, html: str) -> Optional[str]:
        """Fallback to extract channel ID from meta tags in HTML."""
        patterns = [
            r'<meta itemprop="channelId" content="([^"]+)">',
            r'<link rel="canonical" href="https://www\.youtube\.com/channel/([^"]+)">',
            r'"channelId":"(UC[a-zA-Z0-9_-]{20,24})"',
        ]
        for p in patterns:
            m = re.search(p, html)
            if m:
                return m.group(1)
        return None

    def _extract_handle_from_url(self, url: str) -> Optional[str]:
        """Extract handle from URL if present."""
        m = re.search(r"@([\w.-]+)", url)
        return f"@{m.group(1)}" if m else None
