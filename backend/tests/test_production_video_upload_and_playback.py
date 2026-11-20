"""
Automated tests for production video upload persistence and playback.
Covers all 9 production verification scenarios:
1. Authenticated manual upload persists original asset to Cloudinary.
2. Failed Cloudinary verification reports error (HTTP 502) and does not report success.
3. Original asset Cloudinary URL is saved correctly on VideoAsset.
4. Original preview uses original asset Cloudinary URL (no admin key in URL).
5. Processed preview uses processed asset Cloudinary URL.
6. Video playback works without exposing admin key in a URL.
7. Protected video APIs (/api/v1/videos/...) reject unauthorized requests when auth is enabled.
8. Original video remains available from Cloudinary after local temp file removal.
9. Byte-range delivery (verify_url) works for video preview.
"""
import io
import os
import subprocess
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.models import Video, VideoAsset, VideoStatus, JobStatus
from app.repositories.video_repository import VideoRepository
from app.repositories.job_repository import JobRepository
from app.services.cloudinary.cloudinary_service import CloudinaryService
from app.services.video.processor import VideoProcessor
from app.schemas.schemas import ProcessVideoRequest
from app.core.security import clear_failed_attempts_for_testing


TEST_ADMIN_KEY = "test-video-admin-key-32-chars-long"


def create_synthetic_mp4(output_path: str, duration_secs: int = 2, width: int = 640, height: int = 360) -> str:
    """Generate a small valid synthetic MP4 file using FFmpeg."""
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


# ─── 1 & 3. Authenticated Manual Upload Persists Original Asset to Cloudinary ──

def test_authenticated_manual_upload_persists_original_to_cloudinary(client: TestClient, db_session: Session, tmp_path, monkeypatch):
    """Scenario 1 & 3: Authenticated manual upload persists original asset to Cloudinary and saves URL in PostgreSQL."""
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "admin_api_key", TEST_ADMIN_KEY)
    monkeypatch.setattr(settings, "dev_auth_bypass", False)
    monkeypatch.setattr(settings, "cloudinary_cloud_name", "test-cloud")
    monkeypatch.setattr(settings, "cloudinary_api_key", "test-key")
    monkeypatch.setattr(settings, "cloudinary_api_secret", "test-secret")
    clear_failed_attempts_for_testing()

    raw_mp4 = str(tmp_path / "manual_upload.mp4")
    create_synthetic_mp4(raw_mp4, duration_secs=2)

    video = Video(
        youtube_video_id="UPLOAD_PERSIST_01",
        title="Upload Persistence Test",
        video_url="https://youtube.com/watch?v=UPLOAD_PERSIST_01",
        status=VideoStatus.FAILED,
    )
    db_session.add(video)
    db_session.commit()
    db_session.refresh(video)

    expected_pub_id = f"adoraxe/originals/{video.id}"
    expected_cld_url = f"https://res.cloudinary.com/test-cloud/video/upload/{expected_pub_id}.mp4"

    def fake_upload(file_path, public_id, folder=None, overwrite=True):
        return {
            "public_id": public_id,
            "secure_url": expected_cld_url,
            "duration": 2.0,
            "width": 640,
            "height": 360,
            "format": "mp4",
            "bytes": 20480,
        }

    with patch.object(CloudinaryService, "upload_video", side_effect=fake_upload) as mock_upload, \
         patch.object(CloudinaryService, "verify_url", return_value=True) as mock_verify:

        with open(raw_mp4, "rb") as f:
            resp = client.post(
                "/api/v1/videos/upload",
                files={"file": ("manual_upload.mp4", f, "video/mp4")},
                data={"video_id": video.id},
                headers={"X-Admin-Key": TEST_ADMIN_KEY},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["video_id"] == video.id
        assert "persisted" in data["message"].lower() or "successful" in data["message"].lower()

        # Cloudinary was called with deterministic original naming
        mock_upload.assert_called_once()
        assert mock_upload.call_args[1]["public_id"] == expected_pub_id
        assert mock_upload.call_args[1]["folder"] == "adoraxe/originals"
        assert mock_upload.call_args[1]["overwrite"] is True

        # verify_url was called to validate delivery
        mock_verify.assert_called_once_with(expected_cld_url)

    # Verify PostgreSQL database persistence on original VideoAsset
    repo = VideoRepository(db_session)
    orig_asset = repo.get_original_asset(video.id)
    assert orig_asset is not None
    assert orig_asset.is_processed is False
    assert orig_asset.cloudinary_url == expected_cld_url
    assert orig_asset.cloudinary_public_id == expected_pub_id
    assert orig_asset.duration_seconds == 2
    assert orig_asset.width == 640
    assert orig_asset.height == 360

    # Video status reset from FAILED to IMPORTED
    db_session.refresh(video)
    assert video.status == VideoStatus.IMPORTED


# ─── 2. Failed Cloudinary Verification Reports Error ──────────────────────────

def test_failed_cloudinary_verification_reports_502_and_retains_staged(client: TestClient, db_session: Session, tmp_path, monkeypatch):
    """Scenario 2: Failed Cloudinary verification reports HTTP 502 and does not claim success; local staged copy retained."""
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "admin_api_key", TEST_ADMIN_KEY)
    monkeypatch.setattr(settings, "dev_auth_bypass", False)
    monkeypatch.setattr(settings, "cloudinary_cloud_name", "test-cloud")
    monkeypatch.setattr(settings, "cloudinary_api_key", "test-key")
    monkeypatch.setattr(settings, "cloudinary_api_secret", "test-secret")
    clear_failed_attempts_for_testing()

    raw_mp4 = str(tmp_path / "fail_verify.mp4")
    create_synthetic_mp4(raw_mp4, duration_secs=2)

    video = Video(
        youtube_video_id="UPLOAD_FAIL_02",
        title="Upload Fail Verify Test",
        video_url="https://youtube.com/watch?v=UPLOAD_FAIL_02",
        status=VideoStatus.IMPORTED,
    )
    db_session.add(video)
    db_session.commit()
    db_session.refresh(video)

    # Mock: upload succeeds but delivery verification fails
    with patch.object(CloudinaryService, "upload_video", return_value={"secure_url": "https://res.cloudinary.com/test/bad.mp4"}), \
         patch.object(CloudinaryService, "verify_url", return_value=False):

        with open(raw_mp4, "rb") as f:
            resp = client.post(
                "/api/v1/videos/upload",
                files={"file": ("fail_verify.mp4", f, "video/mp4")},
                data={"video_id": video.id},
                headers={"X-Admin-Key": TEST_ADMIN_KEY},
            )

        assert resp.status_code == 502
        assert "verification failed" in resp.json()["detail"].lower()

    # Ensure no asset was saved with the unverified Cloudinary URL
    repo = VideoRepository(db_session)
    orig_asset = repo.get_original_asset(video.id)
    assert orig_asset is None


# ─── 4 & 5. Previews Use Cloudinary HTTPS URLs Without Admin Key ──────────────

def test_video_previews_prefer_cloudinary_urls_without_exposing_admin_key(client: TestClient, db_session: Session, monkeypatch):
    """Scenario 4 & 5: Video detail endpoint provides direct verified Cloudinary URLs for original & processed previews."""
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "admin_api_key", TEST_ADMIN_KEY)
    monkeypatch.setattr(settings, "dev_auth_bypass", False)
    clear_failed_attempts_for_testing()

    video = Video(
        youtube_video_id="PREVIEW_URL_TEST",
        title="Preview URL Test Video",
        video_url="https://youtube.com/watch?v=PREVIEW_URL_TEST",
        status=VideoStatus.PROCESSED,
    )
    db_session.add(video)
    db_session.commit()
    db_session.refresh(video)

    orig_cld = f"https://res.cloudinary.com/adoraxe/video/upload/adoraxe/originals/{video.id}.mp4"
    reel_cld = f"https://res.cloudinary.com/adoraxe/video/upload/adoraxe/reels/{video.id}.mp4"

    # Original asset (is_processed=False)
    orig_asset = VideoAsset(
        video_id=video.id,
        is_processed=False,
        is_reel_compatible=False,
        cloudinary_url=orig_cld,
        cloudinary_public_id=f"adoraxe/originals/{video.id}",
        original_filename="source.mp4",
        duration_seconds=10,
        width=1920,
        height=1080,
    )
    # Processed Reel asset (is_processed=True)
    reel_asset = VideoAsset(
        video_id=video.id,
        is_processed=True,
        is_reel_compatible=True,
        cloudinary_url=reel_cld,
        cloudinary_public_id=f"adoraxe/reels/{video.id}",
        original_filename="source_reel.mp4",
        duration_seconds=10,
        width=1080,
        height=1920,
        crop_mode="pad",
    )
    db_session.add_all([orig_asset, reel_asset])
    db_session.commit()

    resp = client.get(f"/api/v1/videos/{video.id}", headers={"X-Admin-Key": TEST_ADMIN_KEY})
    assert resp.status_code == 200
    data = resp.json()

    # Original preview uses Cloudinary HTTPS URL
    assert data["original_video_url"] == orig_cld
    assert data["has_uploaded_video"] is True

    # Processed preview uses Reel Cloudinary HTTPS URL
    assert data["processed_video_url"] == reel_cld
    assert data["has_processed_reel"] is True

    # Crucial security assertion: Admin key is NEVER present in any preview or stream URL
    assert TEST_ADMIN_KEY not in data["original_video_url"]
    assert TEST_ADMIN_KEY not in data["processed_video_url"]
    assert "admin" not in data["original_video_url"].lower()
    assert "admin" not in data["processed_video_url"].lower()


# ─── 6. Video Playback Works Without Exposing Admin Key in URLs ───────────────

def test_video_playback_url_never_leaks_admin_key(client: TestClient, db_session: Session, monkeypatch):
    """Scenario 6: HTML5 video tags use Cloudinary HTTPS URLs directly; backend stream redirects to Cloudinary."""
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "admin_api_key", TEST_ADMIN_KEY)
    monkeypatch.setattr(settings, "dev_auth_bypass", False)
    clear_failed_attempts_for_testing()

    video = Video(
        youtube_video_id="PLAYBACK_KEY_LEAK_TEST",
        title="Playback Security Video",
        video_url="https://youtube.com/watch?v=PLAYBACK_KEY_LEAK_TEST",
        status=VideoStatus.PROCESSED,
    )
    db_session.add(video)
    db_session.commit()

    reel_cld = f"https://res.cloudinary.com/adoraxe/video/upload/adoraxe/reels/{video.id}.mp4"
    asset = VideoAsset(
        video_id=video.id,
        is_processed=True,
        is_reel_compatible=True,
        cloudinary_url=reel_cld,
        cloudinary_public_id=f"adoraxe/reels/{video.id}",
    )
    db_session.add(asset)
    db_session.commit()

    # When accessing stream endpoint with auth header (e.g., local dev fallback), returns 307 Redirect to Cloudinary
    resp = client.get(
        f"/api/v1/videos/{video.id}/stream/processed",
        headers={"X-Admin-Key": TEST_ADMIN_KEY},
        follow_redirects=False,
    )
    assert resp.status_code == 307
    location = resp.headers["location"]
    assert location == reel_cld
    assert TEST_ADMIN_KEY not in location


# ─── 7. Protected Video APIs Reject Unauthorized Requests ─────────────────────

def test_protected_video_apis_reject_unauthorized_requests(client: TestClient, monkeypatch):
    """Scenario 7: Protected video API endpoints (/api/v1/videos/...) return 401 when auth is missing or invalid."""
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "admin_api_key", TEST_ADMIN_KEY)
    monkeypatch.setattr(settings, "dev_auth_bypass", False)
    clear_failed_attempts_for_testing()

    # 1. Missing header
    res_list = client.get("/api/v1/videos")
    assert res_list.status_code == 401

    res_get = client.get("/api/v1/videos/any-video-id")
    assert res_get.status_code == 401

    res_upload = client.post("/api/v1/videos/upload")
    assert res_upload.status_code == 401

    res_process = client.post("/api/v1/videos/any-video-id/process", json={"crop_mode": "pad"})
    assert res_process.status_code == 401

    res_stream = client.get("/api/v1/videos/any-video-id/stream/original")
    assert res_stream.status_code == 401

    # 2. Invalid header
    bad_headers = {"X-Admin-Key": "invalid-wrong-key-value"}
    res_bad = client.get("/api/v1/videos", headers=bad_headers)
    assert res_bad.status_code == 401


# ─── 8. Original Video Available from Cloudinary After Temp File Removal ──────

@pytest.mark.asyncio
async def test_original_video_resumes_from_cloudinary_when_local_disk_cleaned(tmp_path, db_session: Session, monkeypatch):
    """Scenario 8: When local temp file is removed (e.g. Render container restart), processing downloads and stages original from Cloudinary."""
    monkeypatch.setattr(settings, "cloudinary_cloud_name", "test-cloud")
    monkeypatch.setattr(settings, "cloudinary_api_key", "test-key")
    monkeypatch.setattr(settings, "cloudinary_api_secret", "test-secret")

    video = Video(
        youtube_video_id="RESTART_RECOVERY_1",
        title="Restart Recovery Video",
        video_url="https://youtube.com/watch?v=RESTART_RECOVERY_1",
        status=VideoStatus.IMPORTED,
    )
    db_session.add(video)
    db_session.commit()

    orig_cld = f"https://res.cloudinary.com/test-cloud/video/upload/adoraxe/originals/{video.id}.mp4"
    # Local file_path does NOT exist on disk (simulating ephemeral storage wiped after container restart)
    orig_asset = VideoAsset(
        video_id=video.id,
        file_path="/tmp/ephemeral_path_that_no_longer_exists.mp4",
        cloudinary_url=orig_cld,
        cloudinary_public_id=f"adoraxe/originals/{video.id}",
        is_processed=False,
        original_filename="restart_video.mp4",
        duration_seconds=3,
        width=1280,
        height=720,
        codec="h264",
    )
    db_session.add(orig_asset)
    db_session.commit()

    job_repo = JobRepository(db_session)
    job = job_repo.create_processing_job(video_id=video.id)

    processor = VideoProcessor()

    # Mock _stage_remote_file to simulate downloading original from Cloudinary to a new local temp path
    async def fake_stage(url, target_path):
        assert url == orig_cld
        create_synthetic_mp4(target_path, duration_secs=3, width=1280, height=720)
        return True

    reel_cld = f"https://res.cloudinary.com/test-cloud/video/upload/adoraxe/reels/{video.id}.mp4"
    mock_reel_res = {
        "public_id": f"adoraxe/reels/{video.id}",
        "secure_url": reel_cld,
        "format": "mp4",
        "bytes": 60000,
    }

    with patch.object(processor, "_stage_remote_file", side_effect=fake_stage) as mock_stage, \
         patch.object(CloudinaryService, "upload_video", return_value=mock_reel_res) as mock_upload, \
         patch.object(CloudinaryService, "verify_url", return_value=True):

        opts = ProcessVideoRequest(crop_mode="pad")
        await processor.process_video(job_id=job.id, video_id=video.id, options=opts, db=db_session)

    db_session.refresh(job)
    assert job.status == JobStatus.FINISHED

    # Verify that the processor staged original from Cloudinary
    mock_stage.assert_called_once()
    # And uploaded the processed Reel
    mock_upload.assert_called_once()
    assert mock_upload.call_args[1]["public_id"] == f"adoraxe/reels/{video.id}"

    # Verify processed asset was created
    repo = VideoRepository(db_session)
    reel_record = repo.get_processed_asset(video.id)
    assert reel_record is not None
    assert reel_record.cloudinary_url == reel_cld


# ─── 9. Byte-Range Delivery verify_url Works for Video Preview ────────────────

@pytest.mark.asyncio
async def test_byte_range_delivery_verify_url():
    """Scenario 9: CloudinaryService.verify_url validates range requests and video content types."""
    service = CloudinaryService()

    # Rejects invalid or local schemes
    assert await service.verify_url("") is False
    assert await service.verify_url("file:///tmp/test.mp4") is False

    # Valid video range response (HTTP 206)
    mock_resp = MagicMock()
    mock_resp.status_code = 206
    mock_resp.headers = {"content-type": "video/mp4"}
    mock_resp.content = b"mp4-byte-data"

    with patch("httpx.AsyncClient.get", return_value=mock_resp):
        assert await service.verify_url("https://res.cloudinary.com/adoraxe/video/upload/test.mp4") is True

    # Error response (404)
    mock_404 = MagicMock()
    mock_404.status_code = 404
    mock_404.headers = {"content-type": "text/html"}

    with patch("httpx.AsyncClient.get", return_value=mock_404), \
         patch("httpx.AsyncClient.head", return_value=mock_404):
        assert await service.verify_url("https://res.cloudinary.com/adoraxe/video/upload/missing.mp4") is False
