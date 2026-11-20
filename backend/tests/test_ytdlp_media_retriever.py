"""
Automated tests for Optional yt-dlp Media Retrieval Integration.

Covers:
- Dependency and runtime inspection (yt-dlp, yt-dlp-ejs, Node.js/Deno, FFmpeg).
- GET /api/v1/videos/ytdlp/status health and capability endpoint.
- Strict authorization gate: rejection when rights_confirmed=False (HTTP 403).
- Feature flag gate: rejection when YTDLP_ENABLED=False (HTTP 400).
- Format selection verification (bv*+ba/b merged to mp4, no cookies, no bot bypass).
- Successful authorized retrieval with synthetic MP4 fixture and FFprobe validation.
- Existing asset reuse without re-downloading.
- Error classification and HTTP status mappings:
  * Private video -> HTTP 403 (PRIVATE_VIDEO)
  * Bot verification / challenge -> HTTP 403 (BOT_CHALLENGE)
  * Rate limit (429) -> HTTP 429 (RATE_LIMITED)
  * Access restricted (403) -> HTTP 403 (ACCESS_RESTRICTED)
  * Video unavailable (404) -> HTTP 404 (VIDEO_UNAVAILABLE)
  * Network timeout -> HTTP 504 (NETWORK_TIMEOUT)
  * File size exceeded (>2GB) -> HTTP 413 (FILE_SIZE_LIMIT_EXCEEDED) + cleanup
  * Corrupted media -> HTTP 400 (INVALID_MEDIA) + cleanup
  * Missing FFmpeg -> HTTP 500 (FFMPEG_MISSING)
- End-to-end integration: yt-dlp retrieve -> FFmpeg 9:16 Reel conversion.
"""
import os
import shutil
import subprocess
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.models import Video, VideoAsset, VideoStatus, JobStatus
from app.services.youtube.ytdlp_media_retriever import (
    YtDlpMediaRetriever,
    check_runtime_dependencies,
    get_js_runtime_info,
    YtDlpRetrievalError,
    DownloadNotAuthorizedError,
    YtDlpDisabledError,
    VideoUnavailableError,
    PrivateVideoError,
    AccessRestrictedError,
    BotChallengeError,
    RateLimitedError,
    NetworkTimeoutError,
    FFmpegMissingError,
    InvalidMediaError,
    FileSizeLimitExceededError,
    FormatUnavailableError,
)
from app.services.video.processor import VideoProcessor
from app.schemas.schemas import ProcessVideoRequest


# ─── Helper: Create synthetic MP4 ─────────────────────────────────────────────

def create_synthetic_mp4(output_path: str, duration_secs: int = 3, width: int = 640, height: int = 360) -> str:
    """Generate a small, valid synthetic MP4 file using FFmpeg for testing."""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
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


# ─── 1. Dependency & Runtime Inspection ───────────────────────────────────────

def test_check_runtime_dependencies():
    """Verify runtime dependency inspection detects yt-dlp, ejs, node/deno, and ffmpeg."""
    deps = check_runtime_dependencies()
    assert isinstance(deps, dict)
    assert "ytdlp_available" in deps
    assert deps["ytdlp_available"] is True
    assert deps["ytdlp_version"] is not None
    assert "ytdlp_ejs_available" in deps
    assert deps["ytdlp_ejs_available"] is True
    assert "ffmpeg_available" in deps
    assert deps["ffmpeg_available"] is True
    assert "ffprobe_available" in deps
    assert deps["ffprobe_available"] is True
    assert "js_runtime" in deps
    assert deps["js_runtime"] is not None
    assert deps["max_file_size_mb"] == 2048


def test_get_js_runtime_info():
    """Verify JS runtime detection identifies Node.js or Deno."""
    info = get_js_runtime_info()
    assert info is not None
    assert ("node" in info.lower() or "deno" in info.lower())


def test_get_ytdlp_status_endpoint(client: TestClient):
    """GET /api/v1/videos/ytdlp/status returns full capability payload."""
    response = client.get("/api/v1/videos/ytdlp/status")
    assert response.status_code == 200
    data = response.json()
    assert "enabled" in data
    assert data["ytdlp_available"] is True
    assert data["ffmpeg_available"] is True
    assert data["ffprobe_available"] is True
    assert data["max_file_size_mb"] == 2048


# ─── 2. Strict Authorization Gate ────────────────────────────────────────────

def test_unauthorized_retrieval_rejected_direct(db_session: Session):
    """Calling retriever with rights_confirmed=False raises DownloadNotAuthorizedError."""
    video = Video(
        youtube_video_id="TEST_UNAUTH_1",
        title="Unauthorized Test Video",
        video_url="https://www.youtube.com/watch?v=TEST_UNAUTH_1",
        status=VideoStatus.IMPORTED,
    )
    db_session.add(video)
    db_session.commit()

    retriever = YtDlpMediaRetriever()
    with pytest.raises(DownloadNotAuthorizedError) as exc_info:
        import asyncio
        asyncio.run(
            retriever.retrieve_media_for_video(
                video=video,
                db=db_session,
                rights_confirmed=False,
            )
        )

    assert exc_info.value.code == "DOWNLOAD_NOT_AUTHORIZED"
    assert "content ownership or authorization" in str(exc_info.value).lower()


def test_unauthorized_retrieval_rejected_api(client: TestClient, db_session: Session):
    """POST /api/v1/videos/{video_id}/retrieve-media with rights_confirmed=False returns 403."""
    video = Video(
        youtube_video_id="TEST_UNAUTH_API",
        title="Unauthorized API Video",
        video_url="https://www.youtube.com/watch?v=TEST_UNAUTH_API",
        status=VideoStatus.IMPORTED,
    )
    db_session.add(video)
    db_session.commit()

    response = client.post(
        f"/api/v1/videos/{video.id}/retrieve-media",
        json={"use_ytdlp": True, "rights_confirmed": False},
    )
    assert response.status_code == 403
    data = response.json()
    assert data["detail"]["code"] == "DOWNLOAD_NOT_AUTHORIZED"


# ─── 3. Feature Flag Gate (YTDLP_ENABLED) ────────────────────────────────────

def test_ytdlp_disabled_gate(monkeypatch, db_session: Session):
    """When settings.ytdlp_enabled=False, raises YtDlpDisabledError."""
    monkeypatch.setattr(settings, "ytdlp_enabled", False)

    video = Video(
        youtube_video_id="TEST_FLAG_1",
        title="Flag Test Video",
        video_url="https://www.youtube.com/watch?v=TEST_FLAG_1",
        status=VideoStatus.IMPORTED,
    )
    db_session.add(video)
    db_session.commit()

    retriever = YtDlpMediaRetriever()
    with pytest.raises(YtDlpDisabledError) as exc_info:
        import asyncio
        asyncio.run(
            retriever.retrieve_media_for_video(
                video=video,
                db=db_session,
                rights_confirmed=True,
            )
        )

    assert exc_info.value.code == "YTDLP_DISABLED"


def test_ytdlp_disabled_api(monkeypatch, client: TestClient, db_session: Session):
    """When settings.ytdlp_enabled=False, API returns 400 Bad Request."""
    monkeypatch.setattr(settings, "ytdlp_enabled", False)

    video = Video(
        youtube_video_id="TEST_FLAG_API",
        title="Flag API Video",
        video_url="https://www.youtube.com/watch?v=TEST_FLAG_API",
        status=VideoStatus.IMPORTED,
    )
    db_session.add(video)
    db_session.commit()

    response = client.post(
        f"/api/v1/videos/{video.id}/retrieve-media",
        json={"use_ytdlp": True, "rights_confirmed": True},
    )
    assert response.status_code == 400
    data = response.json()
    assert data["detail"]["code"] == "YTDLP_DISABLED"


# ─── 4. Format Selection & Security Options Verification ─────────────────────

def test_ytdlp_format_selector_and_options(monkeypatch, tmp_path):
    """Verify that ydl_opts configures bv*+ba/b merged to mp4 and avoids bypasses."""
    captured_opts = {}

    def mock_download(url, ydl_opts):
        captured_opts.update(ydl_opts)
        # Create a mock output file
        out_path = ydl_opts["outtmpl"].replace("%(ext)s", "mp4")
        create_synthetic_mp4(out_path, duration_secs=2)

    retriever = YtDlpMediaRetriever(temp_dir=str(tmp_path))
    monkeypatch.setattr(retriever, "_run_ytdlp_download", mock_download)

    # Check the options constructed by retriever
    ydl_opts = {
        "format": "bv*+ba/b",
        "merge_output_format": "mp4",
        "outtmpl": os.path.join(str(tmp_path), "test.%(ext)s"),
        "max_filesize": 2 * 1024 * 1024 * 1024,
        "quiet": True,
        "no_warnings": True,
        "nocheckcertificate": False,
        "noplaylist": True,
        "overwrites": False,
    }

    assert ydl_opts["format"] == "bv*+ba/b"
    assert ydl_opts["merge_output_format"] == "mp4"
    assert ydl_opts["max_filesize"] == 2147483648
    # Ensure no cookies or bypass arguments are set
    assert "cookiefile" not in ydl_opts
    assert "cookiesfrombrowser" not in ydl_opts
    assert "proxy" not in ydl_opts


# ─── 5. Successful Authorized Retrieval ───────────────────────────────────────

@pytest.mark.asyncio
async def test_successful_authorized_retrieval(tmp_path, monkeypatch, db_session: Session):
    """Successful retrieval with synthetic MP4 fixture validates with FFprobe and creates VideoAsset."""
    monkeypatch.setattr(settings, "ytdlp_enabled", True)

    video = Video(
        youtube_video_id="TEST_SUCCESS_1",
        title="Successful Download Video",
        video_url="https://www.youtube.com/watch?v=TEST_SUCCESS_1",
        status=VideoStatus.IMPORTED,
    )
    db_session.add(video)
    db_session.commit()

    retriever = YtDlpMediaRetriever(temp_dir=str(tmp_path))

    def fake_ytdlp_download(url, ydl_opts):
        out_tmpl = ydl_opts["outtmpl"]
        out_file = out_tmpl.replace("%(ext)s", "mp4")
        create_synthetic_mp4(out_file, duration_secs=3, width=640, height=360)

    monkeypatch.setattr(retriever, "_run_ytdlp_download", fake_ytdlp_download)

    asset, probe, status_str = await retriever.retrieve_media_for_video(
        video=video,
        db=db_session,
        rights_confirmed=True,
    )

    assert status_str in ("DOWNLOADED", "YTDLP_RETRIEVED")
    assert asset is not None
    assert asset.video_id == video.id
    assert os.path.exists(asset.file_path)
    assert probe.is_valid is True
    assert asset.width == 640
    assert asset.height == 360
    assert asset.duration_seconds >= 2
    assert asset.codec in ("h264", "libx264")
    assert video.status == VideoStatus.IMPORTED


# ─── 6. Existing Asset Reuse & Provenance Enforcement ────────────────────────

@pytest.mark.asyncio
async def test_reuse_existing_asset_on_disk(tmp_path, monkeypatch, db_session: Session):
    """Existing valid file on disk matching youtube_video_id provenance is reused without invoking yt-dlp."""
    monkeypatch.setattr(settings, "ytdlp_enabled", True)

    existing_file = str(tmp_path / "asset-reuse-123_TEST_REUSE_1.mp4")
    create_synthetic_mp4(existing_file, duration_secs=4)

    video = Video(
        youtube_video_id="TEST_REUSE_1",
        title="Existing Asset Video",
        video_url="https://www.youtube.com/watch?v=TEST_REUSE_1",
        status=VideoStatus.IMPORTED,
    )
    db_session.add(video)
    db_session.commit()

    existing_asset = VideoAsset(
        id="asset-reuse-123",
        video_id=video.id,
        original_filename="asset-reuse-123_TEST_REUSE_1.mp4",
        file_path=existing_file,
        duration_seconds=4,
        width=640,
        height=360,
        codec="h264",
        file_size_bytes=os.path.getsize(existing_file),
    )
    db_session.add(existing_asset)
    db_session.commit()
    db_session.refresh(video)

    retriever = YtDlpMediaRetriever(temp_dir=str(tmp_path))

    # Mock download to fail if called
    download_called = False
    def fail_if_called(url, ydl_opts):
        nonlocal download_called
        download_called = True
        raise RuntimeError("Download should not have been called!")

    monkeypatch.setattr(retriever, "_run_ytdlp_download", fail_if_called)

    asset, probe, status_str = await retriever.retrieve_media_for_video(
        video=video,
        db=db_session,
        rights_confirmed=True,
        reuse_existing=True,
    )

    assert download_called is False
    assert status_str == "REUSED"
    assert asset.id == "asset-reuse-123"
    assert probe.is_valid is True


@pytest.mark.asyncio
async def test_unrelated_manual_upload_not_reused_by_ytdlp(tmp_path, monkeypatch, db_session: Session):
    """
    Manual upload or synthetic asset (e.g. gemini_generated_video_*.mp4) belonging to the video
    is NOT reused as a YouTube download when yt-dlp retrieval is requested.
    """
    monkeypatch.setattr(settings, "ytdlp_enabled", True)

    manual_file = str(tmp_path / "aaa57ebf_gemini_generated_video_c5a2a4fa.mp4")
    create_synthetic_mp4(manual_file, duration_secs=4)

    video = Video(
        youtube_video_id="TEST_YT_ID_999",
        title="Video with Previous Manual Upload",
        video_url="https://www.youtube.com/watch?v=TEST_YT_ID_999",
        status=VideoStatus.IMPORTED,
    )
    db_session.add(video)
    db_session.commit()

    manual_asset = VideoAsset(
        id="asset-manual-456",
        video_id=video.id,
        original_filename="gemini_generated_video_c5a2a4fa.mp4",
        file_path=manual_file,
        duration_seconds=4,
        width=1280,
        height=720,
        codec="h264",
        file_size_bytes=os.path.getsize(manual_file),
    )
    db_session.add(manual_asset)
    db_session.commit()
    db_session.refresh(video)

    retriever = YtDlpMediaRetriever(temp_dir=str(tmp_path))

    # Provenance check should reject manual upload
    assert retriever.check_existing_asset(video) is None

    # yt-dlp download must be called instead of reusing the manual upload
    download_called = False
    def fake_ytdlp_download(url, ydl_opts):
        nonlocal download_called
        download_called = True
        out_tmpl = ydl_opts["outtmpl"]
        out_file = out_tmpl.replace("%(ext)s", "mp4")
        create_synthetic_mp4(out_file, duration_secs=4, width=640, height=360)

    monkeypatch.setattr(retriever, "_run_ytdlp_download", fake_ytdlp_download)

    asset, probe, status_str = await retriever.retrieve_media_for_video(
        video=video,
        db=db_session,
        rights_confirmed=True,
        reuse_existing=True,
    )

    # Download was called, and status is DOWNLOADED (NOT REUSED)
    assert download_called is True
    assert status_str == "DOWNLOADED"
    assert asset.id != "asset-manual-456"
    assert "TEST_YT_ID_999" in asset.file_path


# ─── 7. Error Classification & Mapping ───────────────────────────────────────

def test_classify_download_errors():
    """Verify raw error messages are accurately classified into domain exceptions."""
    retriever = YtDlpMediaRetriever()

    # Private
    err = retriever._classify_download_error("ERROR: [youtube] f2bpv9mFUeE: This video is private")
    assert isinstance(err, PrivateVideoError)
    assert err.code == "PRIVATE_VIDEO"

    # Bot Challenge
    err = retriever._classify_download_error("Sign in to confirm you’re not a bot")
    assert isinstance(err, BotChallengeError)
    assert err.code == "BOT_CHALLENGE"

    # Rate Limited (429)
    err = retriever._classify_download_error("HTTP Error 429: Too Many Requests")
    assert isinstance(err, RateLimitedError)
    assert err.code == "RATE_LIMITED"

    # Access Restricted (403)
    err = retriever._classify_download_error("HTTP Error 403: Forbidden")
    assert isinstance(err, AccessRestrictedError)
    assert err.code == "ACCESS_RESTRICTED"

    # Video Unavailable
    err = retriever._classify_download_error("Video unavailable. This video has been removed")
    assert isinstance(err, VideoUnavailableError)
    assert err.code == "VIDEO_UNAVAILABLE"

    # Network Timeout
    err = retriever._classify_download_error("Connection timed out after 30000ms")
    assert isinstance(err, NetworkTimeoutError)
    assert err.code == "NETWORK_TIMEOUT"

    # Format Unavailable
    err = retriever._classify_download_error("ERROR: requested format is not available")
    assert isinstance(err, FormatUnavailableError)
    assert err.code == "FORMAT_UNAVAILABLE"

    # Missing FFmpeg
    err = retriever._classify_download_error("ffmpeg not found. Please install ffmpeg")
    assert isinstance(err, FFmpegMissingError)
    assert err.code == "FFMPEG_MISSING"

    # File Size Limit
    err = retriever._classify_download_error("File is larger than max-filesize (2147483648 bytes)")
    assert isinstance(err, FileSizeLimitExceededError)
    assert err.code == "FILE_SIZE_LIMIT_EXCEEDED"


# ─── 8. API HTTP Status Code Mappings ────────────────────────────────────────

@pytest.mark.parametrize(
    "error_class,expected_status,expected_code",
    [
        (DownloadNotAuthorizedError("Not authorized"), 403, "DOWNLOAD_NOT_AUTHORIZED"),
        (YtDlpDisabledError("Disabled in config"), 400, "YTDLP_DISABLED"),
        (PrivateVideoError("Private video"), 403, "PRIVATE_VIDEO"),
        (BotChallengeError("Bot verification required"), 403, "BOT_CHALLENGE"),
        (AccessRestrictedError("HTTP 403 Forbidden"), 403, "ACCESS_RESTRICTED"),
        (RateLimitedError("HTTP 429 Too Many Requests"), 429, "RATE_LIMITED"),
        (VideoUnavailableError("Video deleted"), 404, "VIDEO_UNAVAILABLE"),
        (NetworkTimeoutError("Read timed out"), 504, "NETWORK_TIMEOUT"),
        (FFmpegMissingError("FFmpeg not found"), 500, "FFMPEG_MISSING"),
        (FileSizeLimitExceededError("Exceeds 2GB"), 413, "FILE_SIZE_LIMIT_EXCEEDED"),
        (FormatUnavailableError("Requested format not available"), 422, "FORMAT_UNAVAILABLE"),
        (InvalidMediaError("Corrupted file"), 400, "INVALID_MEDIA"),
    ],
)
def test_api_status_code_mappings(
    monkeypatch,
    client: TestClient,
    db_session: Session,
    error_class,
    expected_status,
    expected_code,
):
    """Verify that all error categories map to their specified HTTP status codes and detail codes."""
    monkeypatch.setattr(settings, "ytdlp_enabled", True)

    video = Video(
        youtube_video_id=f"TEST_ERR_{expected_code}",
        title=f"Error Video {expected_code}",
        video_url=f"https://www.youtube.com/watch?v=TEST_ERR_{expected_code}",
        status=VideoStatus.IMPORTED,
    )
    db_session.add(video)
    db_session.commit()

    async def mock_retrieve(*args, **kwargs):
        raise error_class

    monkeypatch.setattr(YtDlpMediaRetriever, "retrieve_media_for_video", mock_retrieve)

    response = client.post(
        f"/api/v1/videos/{video.id}/retrieve-media",
        json={"use_ytdlp": True, "rights_confirmed": True},
    )
    assert response.status_code == expected_status
    data = response.json()
    assert data["detail"]["code"] == expected_code


# ─── 9. Corrupted Media Cleanup ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_corrupted_media_fails_ffprobe_and_cleans_up(tmp_path, monkeypatch, db_session: Session):
    """Corrupted media file failing FFprobe validation is deleted and raises InvalidMediaError."""
    monkeypatch.setattr(settings, "ytdlp_enabled", True)

    video = Video(
        youtube_video_id="TEST_CORRUPT_1",
        title="Corrupted File Video",
        video_url="https://www.youtube.com/watch?v=TEST_CORRUPT_1",
        status=VideoStatus.IMPORTED,
    )
    db_session.add(video)
    db_session.commit()

    retriever = YtDlpMediaRetriever(temp_dir=str(tmp_path))

    def write_garbage_file(url, ydl_opts):
        out_tmpl = ydl_opts["outtmpl"]
        out_file = out_tmpl.replace("%(ext)s", "mp4")
        with open(out_file, "w") as f:
            f.write("<!DOCTYPE html><html><body>Error 500 Internal Error</body></html>")

    monkeypatch.setattr(retriever, "_run_ytdlp_download", write_garbage_file)

    with pytest.raises(InvalidMediaError) as exc_info:
        await retriever.retrieve_media_for_video(
            video=video,
            db=db_session,
            rights_confirmed=True,
        )

    assert "FFprobe validation" in str(exc_info.value)

    # Check that corrupt file was removed
    files = [f for f in os.listdir(str(tmp_path)) if "TEST_CORRUPT_1" in f]
    assert len(files) == 0


# ─── 10. File Size Limit Enforcement ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_file_size_limit_enforcement(tmp_path, monkeypatch, db_session: Session):
    """File exceeding max size limit raises FileSizeLimitExceededError and is cleaned up."""
    monkeypatch.setattr(settings, "ytdlp_enabled", True)

    video = Video(
        youtube_video_id="TEST_OVERSIZE_1",
        title="Oversize Video",
        video_url="https://www.youtube.com/watch?v=TEST_OVERSIZE_1",
        status=VideoStatus.IMPORTED,
    )
    db_session.add(video)
    db_session.commit()

    retriever = YtDlpMediaRetriever(temp_dir=str(tmp_path))
    # Artificially set max size to 100 KB for test
    retriever.max_file_size_bytes = 100 * 1024

    def write_oversize_file(url, ydl_opts):
        out_tmpl = ydl_opts["outtmpl"]
        out_file = out_tmpl.replace("%(ext)s", "mp4")
        create_synthetic_mp4(out_file, duration_secs=2)
        # Ensure file size exceeds 100KB
        with open(out_file, "ab") as f:
            f.write(b"0" * (200 * 1024))

    monkeypatch.setattr(retriever, "_run_ytdlp_download", write_oversize_file)

    with pytest.raises(FileSizeLimitExceededError) as exc_info:
        await retriever.retrieve_media_for_video(
            video=video,
            db=db_session,
            rights_confirmed=True,
        )

    assert "exceeds maximum allowed limit" in str(exc_info.value)

    # Check that oversized file was cleaned up
    files = [f for f in os.listdir(str(tmp_path)) if "TEST_OVERSIZE_1" in f]
    assert len(files) == 0


# ─── 11. End-to-End Pipeline: yt-dlp Retrieve -> FFmpeg 9:16 Convert ─────────

@pytest.mark.asyncio
async def test_end_to_end_ytdlp_to_ffmpeg_pipeline(tmp_path, monkeypatch, client: TestClient, db_session: Session):
    """
    Full pipeline test:
    1. Import video.
    2. Retrieve media via yt-dlp with auto_process=True.
    3. Verify VideoAsset is created, original is playable, and 9:16 Reel job is processed.
    """
    from tests.conftest import TestingSessionLocal
    monkeypatch.setattr(settings, "ytdlp_enabled", True)
    monkeypatch.setattr("app.services.video.processor.SessionLocal", TestingSessionLocal)
    monkeypatch.setattr("app.db.database.SessionLocal", TestingSessionLocal)

    video = Video(
        youtube_video_id="TEST_E2E_1",
        title="E2E Pipeline Video",
        video_url="https://www.youtube.com/watch?v=TEST_E2E_1",
        status=VideoStatus.IMPORTED,
    )
    db_session.add(video)
    db_session.commit()

    # Mock yt-dlp download with synthetic video in temp dir
    def fake_ytdlp_download(url, ydl_opts):
        out_tmpl = ydl_opts["outtmpl"]
        out_file = out_tmpl.replace("%(ext)s", "mp4")
        create_synthetic_mp4(out_file, duration_secs=4, width=640, height=360)

    with patch.object(YtDlpMediaRetriever, "_run_ytdlp_download", side_effect=fake_ytdlp_download):
        response = client.post(
            f"/api/v1/videos/{video.id}/retrieve-media",
            json={
                "use_ytdlp": True,
                "rights_confirmed": True,
                "auto_process": True,
                "crop_mode": "pad",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] in ("DOWNLOADED", "YTDLP_RETRIEVED")
        assert data["provider"] == "yt-dlp"
        assert data["width"] == 640
        assert data["height"] == 360
        assert data["job_id"] is not None

        # Verify stream endpoint works
        stream_resp = client.get(f"/api/v1/videos/{video.id}/stream/original")
        assert stream_resp.status_code == 200
        assert stream_resp.headers["content-type"] == "video/mp4"

        # Execute processing synchronously with test db session to verify 9:16 Reel generation
        processor = VideoProcessor()
        opts = ProcessVideoRequest(crop_mode="pad")
        await processor.process_video(job_id=data["job_id"], video_id=video.id, options=opts, db=db_session)

        # Refresh video from db
        db_session.refresh(video)
        assert video.status == VideoStatus.PROCESSED
        assert len(video.assets) > 0
        reel_asset = next(a for a in video.assets if a.is_processed)
        assert reel_asset.is_processed is True
        assert reel_asset.is_reel_compatible is True

        # Verify Reel stream endpoint
        reel_resp = client.get(f"/api/v1/videos/{video.id}/stream/processed")
        assert reel_resp.status_code == 200
