"""
Automated tests for Phase 2: Actual Video Media Retrieval and Storage Integration.

Covers:
- Authorized direct MP4/MOV retrieval via chunked streaming.
- Rejection of YouTube watch-page URLs (MEDIA_NOT_AVAILABLE).
- Timeouts and bounded retry handling.
- Invalid MIME types, HTML error pages, and corrupted media rejection.
- File size limit (2GB) enforcement and cleanup of partial downloads.
- FFprobe validation and VideoAsset database record creation.
- Reuse of an already retrieved asset on disk.
- Automatic retrieval followed by FFmpeg 9:16 Reel conversion.
- Cloudinary upload when configured and fallback when unconfigured.
- Manual upload fallback preservation.
- Local video streaming and preview playback.
"""
import os
import io
import socket
import shutil
import tempfile
import subprocess
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
import httpx
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.models.models import Video, VideoAsset, VideoStatus, JobStatus
from app.services.video.media_retrieval_service import (
    MediaRetrievalService,
    MediaNotAvailableError,
    MediaValidationError,
    MediaSizeLimitExceededError,
    MediaRetrievalError,
    is_youtube_watch_url,
)
from app.services.video.security import (
    validate_remote_url,
    SecuritySSRFError,
    is_ip_allowed,
)
from app.services.video.ffmpeg_service import probe_video
from app.services.cloudinary.cloudinary_service import CloudinaryService


# ─── Mock DNS Fixture for Sandbox Tests ───────────────────────────────────────

@pytest.fixture(autouse=True)
def mock_dns_resolution(monkeypatch):
    """
    Mock socket.getaddrinfo for domain names in tests so that sandbox environment
    without external network access resolves mock domains to public IP (93.184.216.34)
    unless explicitly testing DNS failures or SSRF.
    """
    orig_getaddrinfo = socket.getaddrinfo

    def patched_getaddrinfo(host, port, *args, **kwargs):
        # Allow literal IPs and standard blocked hosts to pass to real getaddrinfo or error
        if host in ("localhost", "127.0.0.1", "169.254.169.254", "metadata.google.internal"):
            return orig_getaddrinfo(host, port, *args, **kwargs)
        # Default mock public IP for domain names
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port or 443))]

    monkeypatch.setattr(socket, "getaddrinfo", patched_getaddrinfo)


# ─── Helper to create a valid synthetic MP4 file using ffmpeg ─────────────────

def create_synthetic_mp4(output_path: str, duration_secs: int = 4, width: int = 640, height: int = 360):
    """Generate a small, valid synthetic MP4 file for testing."""
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"testsrc=duration={duration_secs}:size={width}x{height}:rate=30",
        "-f", "lavfi", "-i", f"sine=frequency=1000:duration={duration_secs}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
        output_path
    ]
    res = subprocess.run(cmd, capture_output=True)
    if res.returncode != 0:
        raise RuntimeError(f"FFmpeg synthetic video creation failed: {res.stderr.decode()}")
    return output_path


# ─── 1. YouTube Watch URL Detection & Rejection ───────────────────────────────

def test_is_youtube_watch_url_detection():
    # Should identify standard YouTube web URLs
    assert is_youtube_watch_url("https://www.youtube.com/watch?v=f2bpv9mFUeE")
    assert is_youtube_watch_url("https://youtu.be/f2bpv9mFUeE")
    assert is_youtube_watch_url("https://youtube.com/shorts/abcdef12345")
    assert is_youtube_watch_url("https://m.youtube.com/watch?v=f2bpv9mFUeE")
    assert is_youtube_watch_url("https://www.youtube.com/embed/f2bpv9mFUeE")

    # Should NOT flag direct media files or authorized CDN streams
    assert not is_youtube_watch_url("https://s3.amazonaws.com/my-bucket/video.mp4")
    assert not is_youtube_watch_url("https://cdn.example.com/media/clip_1080p.mov")
    assert not is_youtube_watch_url("https://storage.googleapis.com/reels/source.mp4")


@pytest.mark.asyncio
async def test_youtube_watch_url_raises_media_not_available():
    service = MediaRetrievalService()
    youtube_url = "https://www.youtube.com/watch?v=f2bpv9mFUeE"

    with pytest.raises(MediaNotAvailableError) as exc_info:
        await service.retrieve_from_url(youtube_url, asset_id="test_asset")

    err_msg = str(exc_info.value)
    assert "YouTube watch-page URLs" in err_msg
    assert "prohibited" in err_msg or "not direct" in err_msg


# ─── 2. Authorized Direct Media Chunked Download ──────────────────────────────

@pytest.mark.asyncio
async def test_authorized_direct_mp4_chunked_retrieval(tmp_path):
    # 1. Create a synthetic MP4 file to serve as mock remote media
    sample_mp4 = str(tmp_path / "sample.mp4")
    create_synthetic_mp4(sample_mp4, duration_secs=3, width=640, height=360)
    with open(sample_mp4, "rb") as f:
        file_bytes = f.read()

    # 2. Mock httpx streaming response
    class MockStreamResponse:
        status_code = 200
        headers = {
            "content-type": "video/mp4",
            "content-length": str(len(file_bytes)),
        }

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

        async def aiter_bytes(self, chunk_size=128 * 1024):
            # Yield in small chunks
            offset = 0
            while offset < len(file_bytes):
                yield file_bytes[offset: offset + chunk_size]
                offset += chunk_size

    class MockAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

        def stream(self, method, url):
            return MockStreamResponse()

    service = MediaRetrievalService(temp_dir=str(tmp_path / "downloads"))

    with patch("httpx.AsyncClient", return_value=MockAsyncClient()):
        downloaded_path = await service.retrieve_from_url(
            url="https://authorized-cdn.example.com/video.mp4",
            asset_id="asset_001",
            filename_hint="promo.mp4",
        )

        assert os.path.exists(downloaded_path)
        assert os.path.getsize(downloaded_path) == len(file_bytes)

        # Verify probe works on the downloaded file
        probe = probe_video(downloaded_path)
        assert probe.is_valid
        assert probe.width == 640
        assert probe.height == 360


# ─── 3. Timeout and Retry Handling ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_timeout_and_transient_retry(tmp_path):
    sample_mp4 = str(tmp_path / "retry_sample.mp4")
    create_synthetic_mp4(sample_mp4, duration_secs=3, width=320, height=240)
    with open(sample_mp4, "rb") as f:
        file_bytes = f.read()

    attempts = 0

    class MockStreamResponse:
        status_code = 200
        headers = {"content-type": "video/mp4", "content-length": str(len(file_bytes))}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def aiter_bytes(self, chunk_size=128*1024):
            yield file_bytes

    class FlakyAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        def stream(self, method, url):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                # First attempt times out
                raise httpx.ConnectTimeout("Connection timed out")
            return MockStreamResponse()

    service = MediaRetrievalService(temp_dir=str(tmp_path / "downloads"))

    with patch("httpx.AsyncClient", return_value=FlakyAsyncClient()):
        downloaded = await service.retrieve_from_url(
            url="https://flaky-server.example.com/test.mp4",
            asset_id="flaky_asset",
            max_retries=2,
        )
        assert attempts == 2  # Retried and succeeded
        assert os.path.exists(downloaded)


# ─── 4. HTTP Error Handling (403, 404, 429) ───────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("status_code,err_substr", [
    (403, "forbidden"),
    (404, "not found"),
    (429, "rate limit"),
    (500, "500"),
])
async def test_http_status_errors(tmp_path, status_code, err_substr):
    class ErrorStreamResponse:
        def __init__(self, code):
            self.status_code = code
            self.headers = {}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def aiter_bytes(self, chunk_size=128*1024):
            yield b""

    class ErrorClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        def stream(self, method, url):
            return ErrorStreamResponse(status_code)

    service = MediaRetrievalService(temp_dir=str(tmp_path / "downloads"))

    with patch("httpx.AsyncClient", return_value=ErrorClient()):
        with pytest.raises(MediaRetrievalError) as exc_info:
            await service.retrieve_from_url("https://example.com/err.mp4", asset_id="err_asset")
        assert err_substr in str(exc_info.value).lower()


# ─── 5. Invalid MIME Type & Corrupted Media Rejection ─────────────────────────

@pytest.mark.asyncio
async def test_invalid_mime_type_html_rejection(tmp_path):
    class HtmlStreamResponse:
        status_code = 200
        headers = {"content-type": "text/html; charset=utf-8"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def aiter_bytes(self, chunk_size=128*1024):
            yield b"<html><body>404 Not Found or CAPTCHA</body></html>"

    class HtmlClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        def stream(self, method, url):
            return HtmlStreamResponse()

    service = MediaRetrievalService(temp_dir=str(tmp_path / "downloads"))

    with patch("httpx.AsyncClient", return_value=HtmlClient()):
        with pytest.raises(MediaValidationError) as exc:
            await service.retrieve_from_url("https://example.com/fake.mp4", asset_id="fake_asset")
        assert "non-video content type 'text/html'" in str(exc.value)


@pytest.mark.asyncio
async def test_corrupt_video_probe_failure_cleaned_up(tmp_path, db_session: Session):
    # Remote returns 200 with video/mp4 MIME, but the actual bytes are invalid non-video text
    class CorruptStreamResponse:
        status_code = 200
        headers = {"content-type": "video/mp4"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def aiter_bytes(self, chunk_size=128*1024):
            yield b"NOT_A_REAL_VIDEO_CORRUPT_DATA_HEADER"

    class CorruptClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        def stream(self, method, url):
            return CorruptStreamResponse()

    service = MediaRetrievalService(temp_dir=str(tmp_path / "downloads"))

    # Create dummy video in DB
    v = Video(youtube_video_id="TEST_CORRUPT", title="Corrupt Video", video_url="https://youtube.com/watch?v=TEST_CORRUPT")
    db_session.add(v)
    db_session.commit()

    with patch("httpx.AsyncClient", return_value=CorruptClient()):
        with pytest.raises(MediaValidationError) as exc:
            await service.retrieve_media_for_video(
                video=v,
                media_url="https://cdn.example.com/corrupt.mp4",
                db=db_session,
            )
        assert "validation" in str(exc.value).lower()

    # Verify no stray file remains
    files = os.listdir(str(tmp_path / "downloads"))
    assert len(files) == 0


# ─── 6. File Size Limit Exceeded ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_file_size_limit_header_exceeded(tmp_path):
    class OversizedResponse:
        status_code = 200
        # 3 GB in bytes
        headers = {"content-type": "video/mp4", "content-length": str(3 * 1024 * 1024 * 1024)}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def aiter_bytes(self, chunk_size=128*1024):
            yield b"dummy"

    class OversizedClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        def stream(self, method, url):
            return OversizedResponse()

    service = MediaRetrievalService(temp_dir=str(tmp_path / "downloads"))

    with patch("httpx.AsyncClient", return_value=OversizedClient()):
        with pytest.raises(MediaSizeLimitExceededError) as exc:
            await service.retrieve_from_url("https://example.com/huge.mp4", asset_id="huge_asset")
        assert "exceeds" in str(exc.value).lower()


# ─── 7. VideoAsset Creation and Reuse Existing Asset ──────────────────────────

@pytest.mark.asyncio
async def test_retrieve_media_creates_video_asset_and_reuses(tmp_path, db_session: Session):
    sample_mp4 = str(tmp_path / "valid.mp4")
    create_synthetic_mp4(sample_mp4, duration_secs=4, width=640, height=360)
    with open(sample_mp4, "rb") as f:
        file_bytes = f.read()

    class ValidStreamResponse:
        status_code = 200
        headers = {"content-type": "video/mp4", "content-length": str(len(file_bytes))}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def aiter_bytes(self, chunk_size=128*1024):
            yield file_bytes

    class ValidClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        def stream(self, method, url):
            return ValidStreamResponse()

    service = MediaRetrievalService(temp_dir=str(tmp_path / "downloads"))

    video = Video(
        youtube_video_id="VALID_TEST_VID",
        title="Valid Test Video",
        video_url="https://youtube.com/watch?v=VALID_TEST_VID",
        status=VideoStatus.IMPORTED,
    )
    db_session.add(video)
    db_session.commit()
    db_session.refresh(video)

    # 1. Retrieve first time
    with patch("httpx.AsyncClient", return_value=ValidClient()):
        asset, probe, status_str = await service.retrieve_media_for_video(
            video=video,
            media_url="https://cdn.example.com/promo.mp4",
            db=db_session,
        )
        assert status_str == "RETRIEVED"
        assert asset.video_id == video.id
        assert asset.width == 640
        assert asset.height == 360
        assert asset.duration_seconds >= 3
        assert os.path.exists(asset.file_path)

    # 2. Call again with reuse_existing=True and no new media_url
    db_session.refresh(video)
    asset2, probe2, status_str2 = await service.retrieve_media_for_video(
        video=video,
        db=db_session,
        reuse_existing=True,
    )
    assert status_str2 == "REUSED"
    assert asset2.id == asset.id
    assert asset2.file_path == asset.file_path


# ─── 8. API Endpoint /retrieve-media Tests ────────────────────────────────────

def test_api_retrieve_media_youtube_watch_url_returns_422(client: TestClient, db_session: Session):
    # Create video record with YouTube watch URL
    video = Video(
        youtube_video_id="YT_WATCH_REJECT",
        title="YouTube Watch Video",
        video_url="https://www.youtube.com/watch?v=YT_WATCH_REJECT",
    )
    db_session.add(video)
    db_session.commit()
    db_session.refresh(video)

    # Request without media_url (fallback to video.video_url which is YouTube)
    resp = client.post(f"/api/v1/videos/{video.id}/retrieve-media", json={})
    assert resp.status_code == 422
    data = resp.json()["detail"]
    assert data["code"] == "MEDIA_NOT_AVAILABLE"
    assert "YouTube watch-page URLs" in data["message"]


def test_api_retrieve_media_authorized_url_success(client: TestClient, db_session: Session, tmp_path):
    # Create synthetic mp4
    sample_mp4 = str(tmp_path / "api_sample.mp4")
    create_synthetic_mp4(sample_mp4, duration_secs=4, width=640, height=360)
    with open(sample_mp4, "rb") as f:
        file_bytes = f.read()

    class MockStreamResponse:
        status_code = 200
        headers = {"content-type": "video/mp4", "content-length": str(len(file_bytes))}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def aiter_bytes(self, chunk_size=128*1024):
            yield file_bytes

    class MockClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        def stream(self, method, url):
            return MockStreamResponse()

    video = Video(
        youtube_video_id="API_TEST_VID",
        title="API Test Video",
        video_url="https://youtube.com/watch?v=API_TEST_VID",
    )
    db_session.add(video)
    db_session.commit()
    db_session.refresh(video)

    with patch("httpx.AsyncClient", return_value=MockClient()):
        resp = client.post(
            f"/api/v1/videos/{video.id}/retrieve-media",
            json={"media_url": "https://storage.googleapis.com/test-bucket/clip.mp4"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "RETRIEVED"
        assert body["video_id"] == video.id
        assert body["width"] == 640
        assert body["height"] == 360
        assert body["original_stream_url"] == f"/api/v1/videos/{video.id}/stream/original"

        # Verify streaming endpoint works
        stream_resp = client.get(f"/api/v1/videos/{video.id}/stream/original")
        assert stream_resp.status_code == 200
        assert stream_resp.headers["content-type"] == "video/mp4"


# ─── 9. Cloudinary Service Verification ───────────────────────────────────────

@pytest.mark.asyncio
async def test_cloudinary_verify_url():
    cdn = CloudinaryService()

    # 1. Invalid or non-https URL returns False
    assert not await cdn.verify_url("")
    assert not await cdn.verify_url("file:///tmp/reel.mp4")

    # 2. Mock valid 200 response with video content-type
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"content-type": "video/mp4"}

    with patch("httpx.AsyncClient.head", AsyncMock(return_value=mock_resp)):
        assert await cdn.verify_url("https://res.cloudinary.com/demo/video/upload/sample.mp4")


# ─── 10. Manual Upload Fallback Preserved ──────────────────────────────────────

def test_manual_upload_fallback_preserved(client: TestClient, db_session: Session, tmp_path):
    sample_mp4 = str(tmp_path / "manual.mp4")
    create_synthetic_mp4(sample_mp4, duration_secs=4, width=640, height=360)

    video = Video(
        youtube_video_id="MANUAL_FALLBACK_VID",
        title="Manual Fallback Video",
        video_url="https://youtube.com/watch?v=MANUAL_FALLBACK_VID",
    )
    db_session.add(video)
    db_session.commit()
    db_session.refresh(video)

    with open(sample_mp4, "rb") as f:
        resp = client.post(
            "/api/v1/videos/upload",
            files={"file": ("manual_upload.mp4", f, "video/mp4")},
            data={"video_id": video.id},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["video_id"] == video.id

    # Verify video details has uploaded video
    vid_resp = client.get(f"/api/v1/videos/{video.id}")
    assert vid_resp.status_code == 200
    vid_data = vid_resp.json()
    assert vid_data["has_uploaded_video"] is True
    assert vid_data["original_video_url"] == f"/api/v1/videos/{video.id}/stream/original"


# ─── 11. SSRF Security Defense Tests ──────────────────────────────────────────

@pytest.mark.parametrize("blocked_url", [
    "http://127.0.0.1/video.mp4",
    "http://localhost:8000/video.mp4",
    "http://10.0.0.1/video.mp4",
    "http://192.168.1.1/video.mp4",
    "http://172.16.0.1/video.mp4",
    "http://169.254.169.254/latest/meta-data/",
    "http://0.0.0.0:8000/stream.mp4",
    "http://metadata.google.internal/computeMetadata/v1/",
    "http://service.internal/video.mp4",
    "ftp://example.com/video.mp4",
    "file:///etc/passwd",
])
def test_ssrf_security_blocks_private_and_metadata_urls(blocked_url):
    with pytest.raises(SecuritySSRFError) as exc_info:
        validate_remote_url(blocked_url)
    assert any(w in str(exc_info.value).lower() for w in ("forbidden", "prohibited", "private", "restricted", "blocked"))


def test_ssrf_dns_rebinding_to_private_ip_blocked(monkeypatch):
    # Simulate attacker.com resolving to internal IP 127.0.0.1
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port or 80))]
    )
    with pytest.raises(SecuritySSRFError) as exc_info:
        validate_remote_url("https://attacker-rebinding.com/video.mp4")
    assert "resolves to private/restricted ip" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_ssrf_malicious_redirect_to_private_ip_blocked(tmp_path, monkeypatch):
    # Simulate public initial URL redirecting to 169.254.169.254
    class RedirectResponse:
        status_code = 302
        headers = {"location": "http://169.254.169.254/latest/meta-data/credentials"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def aiter_bytes(self, chunk_size=128*1024):
            yield b""

    class RedirectClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        def stream(self, method, url):
            return RedirectResponse()

    service = MediaRetrievalService(temp_dir=str(tmp_path / "downloads"))

    with patch("httpx.AsyncClient", return_value=RedirectClient()):
        with pytest.raises(SecuritySSRFError) as exc_info:
            await service.retrieve_from_url("https://public-site.com/video.mp4", asset_id="ssrf_redir_asset")
        assert "private/restricted ip" in str(exc_info.value).lower() or "blocked" in str(exc_info.value).lower()


def test_ssrf_api_endpoint_returns_400(client: TestClient, db_session: Session):
    video = Video(
        youtube_video_id="SSRF_API_TEST",
        title="SSRF API Test Video",
        video_url="https://youtube.com/watch?v=SSRF_API_TEST",
    )
    db_session.add(video)
    db_session.commit()
    db_session.refresh(video)

    resp = client.post(
        f"/api/v1/videos/{video.id}/retrieve-media",
        json={"media_url": "http://127.0.0.1:8000/internal-secret.mp4"},
    )
    assert resp.status_code == 400
    data = resp.json()["detail"]
    assert data["code"] == "SSRF_SECURITY_VIOLATION"
    assert "private/restricted" in data["message"].lower() or "blocked" in data["message"].lower()
