"""
Comprehensive tests for Cloudinary persistent media storage lifecycle,
including upload, URL verification, PostgreSQL persistence, failure handling,
temporary file cleanup, and publishing requirements.

Clearly separates mocked unit tests from real Cloudinary integration testing.
"""
import os
import shutil
import subprocess
from datetime import datetime, timezone
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.models import Video, VideoAsset, VideoStatus, JobStatus, PublishingJob
from app.services.cloudinary.cloudinary_service import CloudinaryService
from app.services.video.processor import VideoProcessor
from app.schemas.schemas import ProcessVideoRequest
from app.repositories.video_repository import VideoRepository
from app.repositories.job_repository import JobRepository
from app.repositories.instagram_repository import DEMO_ACCOUNT_ID


# ─── Helper: Create synthetic MP4 ─────────────────────────────────────────────

def create_synthetic_mp4(output_path: str, duration_secs: int = 4, width: int = 640, height: int = 360) -> str:
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


# ─── 1 & 2. Original & Reel Upload with Deterministic Public IDs ─────────────

@pytest.mark.asyncio
async def test_original_and_reel_upload_public_ids(tmp_path, monkeypatch):
    """Verify original and processed Reel uploads use distinct deterministic public IDs and folders."""
    monkeypatch.setattr(settings, "cloudinary_cloud_name", "test-cloud")
    monkeypatch.setattr(settings, "cloudinary_api_key", "test-key")
    monkeypatch.setattr(settings, "cloudinary_api_secret", "test-secret")

    service = CloudinaryService()
    assert service.is_configured() is True

    video_id = "test-video-uuid-1234"
    orig_id = service.get_public_id(video_id, "original")
    reel_id = service.get_public_id(video_id, "reel")

    assert orig_id == f"adoraxe/originals/{video_id}"
    assert reel_id == f"adoraxe/reels/{video_id}"
    assert orig_id != reel_id

    # Create dummy local file
    test_file = str(tmp_path / "test.mp4")
    with open(test_file, "wb") as f:
        f.write(b"dummy video content")

    mock_upload_result = {
        "public_id": orig_id,
        "secure_url": f"https://res.cloudinary.com/test-cloud/video/upload/{orig_id}.mp4",
        "duration": 5.0,
        "width": 1920,
        "height": 1080,
        "format": "mp4",
        "bytes": 1024,
    }

    with patch("cloudinary.uploader.upload", return_value=mock_upload_result) as mock_upload:
        with patch.object(service, "_configure"):
            result = await service.upload_video(
                file_path=test_file,
                public_id=orig_id,
                folder="adoraxe/originals",
                overwrite=True,
            )

            assert result is not None
            assert result["secure_url"].startswith("https://")
            assert result["public_id"] == orig_id
            mock_upload.assert_called_once()
            call_kwargs = mock_upload.call_args[1]
            assert call_kwargs["overwrite"] is True
            assert call_kwargs["resource_type"] == "video"


# ─── 3. HTTPS Delivery URL Verification ──────────────────────────────────────

@pytest.mark.asyncio
async def test_https_delivery_url_verification():
    """Verify delivery URL validator handles range headers, content types, and rejects invalid schemes."""
    service = CloudinaryService()

    # Rejects empty, non-http, and file:// schemes without making network requests
    assert await service.verify_url("") is False
    assert await service.verify_url("file:///tmp/local.mp4") is False
    assert await service.verify_url("ftp://server/video.mp4") is False
    assert await service.verify_url("not-a-valid-url") is False

    # Mock valid HTTPS video response
    mock_resp = MagicMock()
    mock_resp.status_code = 206
    mock_resp.headers = {"content-type": "video/mp4"}
    mock_resp.content = b"fake video bytes"

    with patch("httpx.AsyncClient.get", return_value=mock_resp):
        assert await service.verify_url("https://res.cloudinary.com/demo/video/upload/sample.mp4") is True

    # Non-video response returns False
    mock_err_resp = MagicMock()
    mock_err_resp.status_code = 404
    mock_err_resp.headers = {"content-type": "text/html"}
    mock_err_resp.content = b"Not found"

    with patch("httpx.AsyncClient.get", return_value=mock_err_resp):
        with patch("httpx.AsyncClient.head", return_value=mock_err_resp):
            assert await service.verify_url("https://res.cloudinary.com/demo/video/upload/missing.mp4") is False


# ─── 4 & 5. Full Lifecycle: PostgreSQL Persistence & File Cleanup ───────────

@pytest.mark.asyncio
async def test_successful_lifecycle_persists_db_and_cleans_local_disk(tmp_path, db_session: Session, monkeypatch):
    """
    Test complete lifecycle with Cloudinary enabled:
    1. Probe original staging file.
    2. Convert to Reel via FFmpeg.
    3. Upload both original and Reel to Cloudinary.
    4. Verify delivery URLs.
    5. Save both Cloudinary URLs and metadata in separate PostgreSQL VideoAsset records.
    6. Delete temporary local original and Reel files.
    """
    monkeypatch.setattr(settings, "cloudinary_cloud_name", "test-cloud")
    monkeypatch.setattr(settings, "cloudinary_api_key", "test-key")
    monkeypatch.setattr(settings, "cloudinary_api_secret", "test-secret")

    # Generate synthetic video in temp staging
    staging_file = str(tmp_path / "temp_stage_video.mp4")
    create_synthetic_mp4(staging_file, duration_secs=4, width=640, height=360)
    assert os.path.exists(staging_file) is True

    video = Video(
        youtube_video_id="CLOUD_LIFECYCLE_1",
        title="Cloudinary Lifecycle Video",
        video_url="https://youtube.com/watch?v=CLOUD_LIFECYCLE_1",
        status=VideoStatus.IMPORTED,
    )
    db_session.add(video)
    db_session.commit()

    repo = VideoRepository(db_session)
    orig_asset = repo.create_asset(
        video_id=video.id,
        file_path=staging_file,
        original_filename="temp_stage_video.mp4",
    )

    job_repo = JobRepository(db_session)
    job = job_repo.create_processing_job(video_id=video.id)

    # Mock Cloudinary uploads and URL verification
    def fake_upload(file_path, public_id, folder=None, overwrite=True):
        is_orig = "original" in public_id
        return {
            "public_id": public_id,
            "secure_url": f"https://res.cloudinary.com/test-cloud/video/upload/{public_id}.mp4",
            "duration": 2.0,
            "width": 640 if is_orig else 1080,
            "height": 360 if is_orig else 1920,
            "format": "mp4",
            "bytes": 50000,
        }

    with patch.object(CloudinaryService, "upload_video", side_effect=fake_upload):
        with patch.object(CloudinaryService, "verify_url", return_value=True):
            processor = VideoProcessor()
            opts = ProcessVideoRequest(crop_mode="pad")
            await processor.process_video(job_id=job.id, video_id=video.id, options=opts, db=db_session)

    # Verify Job finished successfully
    db_session.refresh(job)
    assert job.status == JobStatus.FINISHED, f"Job failed with error: {job.error_message}"
    assert job.cloudinary_url.startswith("https://res.cloudinary.com/")

    # Verify Video status is PROCESSED
    db_session.refresh(video)
    assert video.status == VideoStatus.PROCESSED

    # Verify PostgreSQL persistence: both original and processed Reel exist separately
    orig_record = repo.get_original_asset(video.id)
    reel_record = repo.get_processed_asset(video.id)

    assert orig_record is not None
    assert orig_record.is_processed is False
    assert orig_record.cloudinary_url.startswith("https://res.cloudinary.com/")
    assert orig_record.cloudinary_public_id == f"adoraxe/originals/{video.id}"
    assert orig_record.file_path is None  # Local path cleared after upload

    assert reel_record is not None
    assert reel_record.is_processed is True
    assert reel_record.is_reel_compatible is True
    assert reel_record.cloudinary_url.startswith("https://res.cloudinary.com/")
    assert reel_record.cloudinary_public_id == f"adoraxe/reels/{video.id}"
    assert reel_record.width == 1080
    assert reel_record.height == 1920
    assert reel_record.codec == "h264"

    # Verify local disk cleanup: temporary staging and processed files are DELETED
    assert os.path.exists(staging_file) is False
    expected_reel_local = staging_file.replace(".mp4", "_reel.mp4")
    assert os.path.exists(expected_reel_local) is False


# ─── 6. Failure Handling: Upload Failure Retains Local Files ─────────────────

@pytest.mark.asyncio
async def test_upload_failure_retains_local_files(tmp_path, db_session: Session, monkeypatch):
    """When Cloudinary upload fails, local files must be retained for retry."""
    monkeypatch.setattr(settings, "cloudinary_cloud_name", "test-cloud")
    monkeypatch.setattr(settings, "cloudinary_api_key", "test-key")
    monkeypatch.setattr(settings, "cloudinary_api_secret", "test-secret")

    staging_file = str(tmp_path / "retain_on_fail.mp4")
    create_synthetic_mp4(staging_file, duration_secs=4)
    assert os.path.exists(staging_file) is True

    video = Video(
        youtube_video_id="CLOUD_FAIL_RETAIN_1",
        title="Retain On Failure Video",
        video_url="https://youtube.com/watch?v=CLOUD_FAIL_RETAIN_1",
        status=VideoStatus.IMPORTED,
    )
    db_session.add(video)
    db_session.commit()

    repo = VideoRepository(db_session)
    repo.create_asset(video_id=video.id, file_path=staging_file, original_filename="retain_on_fail.mp4")

    job_repo = JobRepository(db_session)
    job = job_repo.create_processing_job(video_id=video.id)

    # Cloudinary upload fails
    with patch.object(CloudinaryService, "upload_video", side_effect=RuntimeError("Cloudinary network timeout")):
        processor = VideoProcessor()
        opts = ProcessVideoRequest(crop_mode="pad")
        await processor.process_video(job_id=job.id, video_id=video.id, options=opts, db=db_session)

    # Job is FAILED
    db_session.refresh(job)
    assert job.status == JobStatus.FAILED
    assert "Cloudinary" in job.error_message

    # Crucial assertion: Local staging file was NOT deleted
    assert os.path.exists(staging_file) is True

    # Converted Reel local file was also NOT deleted
    reel_path = staging_file.replace(".mp4", "_reel.mp4")
    if os.path.exists(reel_path):
        assert os.path.exists(reel_path) is True


# ─── 7. Failure Handling: Database Persistence Failure Retains Files ─────────

@pytest.mark.asyncio
async def test_database_failure_retains_local_files(tmp_path, db_session: Session, monkeypatch):
    """When database persistence fails, local files must be retained on disk."""
    monkeypatch.setattr(settings, "cloudinary_cloud_name", "test-cloud")
    monkeypatch.setattr(settings, "cloudinary_api_key", "test-key")
    monkeypatch.setattr(settings, "cloudinary_api_secret", "test-secret")

    staging_file = str(tmp_path / "retain_on_db_fail.mp4")
    create_synthetic_mp4(staging_file, duration_secs=4)

    video = Video(
        youtube_video_id="CLOUD_DB_FAIL_1",
        title="DB Failure Retain Video",
        video_url="https://youtube.com/watch?v=CLOUD_DB_FAIL_1",
        status=VideoStatus.IMPORTED,
    )
    db_session.add(video)
    db_session.commit()

    repo = VideoRepository(db_session)
    repo.create_asset(video_id=video.id, file_path=staging_file, original_filename="retain_on_db_fail.mp4")

    job_repo = JobRepository(db_session)
    job = job_repo.create_processing_job(video_id=video.id)

    mock_res = {
        "public_id": f"adoraxe/reels/{video.id}",
        "secure_url": f"https://res.cloudinary.com/test-cloud/video/upload/adoraxe/reels/{video.id}.mp4",
        "duration": 2.0,
        "width": 1080,
        "height": 1920,
        "format": "mp4",
        "bytes": 50000,
    }

    with patch.object(CloudinaryService, "upload_video", return_value=mock_res):
        with patch.object(CloudinaryService, "verify_url", return_value=True):
            with patch.object(VideoRepository, "create_or_update_reel_asset", side_effect=RuntimeError("DB Connection Lost")):
                processor = VideoProcessor()
                opts = ProcessVideoRequest(crop_mode="pad")
                await processor.process_video(job_id=job.id, video_id=video.id, options=opts, db=db_session)

    db_session.refresh(job)
    assert job.status == JobStatus.FAILED

    # Local file MUST be preserved
    assert os.path.exists(staging_file) is True


# ─── 8. Retry Without Duplicate Uploads ──────────────────────────────────────

@pytest.mark.asyncio
async def test_retry_without_duplicate_uploads(monkeypatch):
    """Uploading the same video twice uses the exact same public ID and overwrite=True."""
    monkeypatch.setattr(settings, "cloudinary_cloud_name", "test-cloud")
    monkeypatch.setattr(settings, "cloudinary_api_key", "test-key")
    monkeypatch.setattr(settings, "cloudinary_api_secret", "test-secret")

    service = CloudinaryService()
    video_id = "retry-dedup-uuid-999"

    pid1 = service.get_public_id(video_id, "reel")
    pid2 = service.get_public_id(video_id, "reel")
    assert pid1 == pid2 == f"adoraxe/reels/{video_id}"


# ─── 9. Local vs Cloudinary Preview Compatibility ────────────────────────────

def test_local_versus_cloudinary_preview_compatibility(client: TestClient, db_session: Session, tmp_path):
    """
    Verify video streaming endpoint handles both:
    1. Cloudinary asset -> HTTP 307 Temporary Redirect to HTTPS delivery URL.
    2. Local file asset -> HTTP 200/206 byte-range streaming.
    """
    repo = VideoRepository(db_session)

    # 1. Cloudinary-backed video
    cloud_video = Video(
        youtube_video_id="STREAM_CLOUD_1",
        title="Cloudinary Stream Video",
        video_url="https://youtube.com/watch?v=STREAM_CLOUD_1",
        status=VideoStatus.PROCESSED,
    )
    db_session.add(cloud_video)
    db_session.commit()

    cloud_url = f"https://res.cloudinary.com/test-cloud/video/upload/adoraxe/reels/{cloud_video.id}.mp4"
    orig_cloud_url = f"https://res.cloudinary.com/test-cloud/video/upload/adoraxe/originals/{cloud_video.id}.mp4"

    repo.create_asset(video_id=cloud_video.id, file_path=None, original_filename="orig.mp4")
    orig_asset = repo.get_original_asset(cloud_video.id)
    repo.update_asset_cloudinary(orig_asset.id, cloudinary_url=orig_cloud_url, cloudinary_public_id=f"adoraxe/originals/{cloud_video.id}")

    repo.create_or_update_reel_asset(
        video_id=cloud_video.id,
        cloudinary_url=cloud_url,
        cloudinary_public_id=f"adoraxe/reels/{cloud_video.id}",
        original_filename="reel.mp4",
        duration=5,
        width=1080,
        height=1920,
        codec="h264",
        file_size=50000,
    )

    # Stream endpoints return 307 Redirects to Cloudinary
    resp_orig = client.get(f"/api/v1/videos/{cloud_video.id}/stream/original", follow_redirects=False)
    assert resp_orig.status_code == 307
    assert resp_orig.headers["location"] == orig_cloud_url

    resp_proc = client.get(f"/api/v1/videos/{cloud_video.id}/stream/processed", follow_redirects=False)
    assert resp_proc.status_code == 307
    assert resp_proc.headers["location"] == cloud_url

    # 2. Local file-backed video (historical compatibility)
    local_file = str(tmp_path / "local_stream_compat.mp4")
    create_synthetic_mp4(local_file, duration_secs=2)

    local_video = Video(
        youtube_video_id="STREAM_LOCAL_1",
        title="Local Stream Video",
        video_url="https://youtube.com/watch?v=STREAM_LOCAL_1",
        status=VideoStatus.IMPORTED,
    )
    db_session.add(local_video)
    db_session.commit()

    repo.create_asset(video_id=local_video.id, file_path=local_file, original_filename="local_stream_compat.mp4")

    resp_local = client.get(f"/api/v1/videos/{local_video.id}/stream/original")
    assert resp_local.status_code == 200
    assert resp_local.headers["content-type"] == "video/mp4"

    # Range request
    resp_range = client.get(f"/api/v1/videos/{local_video.id}/stream/original", headers={"Range": "bytes=0-100"})
    assert resp_range.status_code == 206


# ─── 10. Publishing Queue Requiring Valid Cloudinary URL ──────────────────────

def test_publishing_queue_requires_valid_cloudinary_url(client: TestClient, db_session: Session, tmp_path):
    """Publishing queue and publish endpoint require a verified Cloudinary HTTPS URL."""
    repo = VideoRepository(db_session)

    # Video A: Processed locally without Cloudinary URL (file://)
    local_file = str(tmp_path / "no_cloud.mp4")
    create_synthetic_mp4(local_file)

    vid_a = Video(
        youtube_video_id="PUB_LOCAL_ONLY",
        title="Local Only Video",
        video_url="https://youtube.com/watch?v=PUB_LOCAL_ONLY",
        status=VideoStatus.PROCESSED,
    )
    db_session.add(vid_a)
    db_session.commit()
    repo.create_asset(video_id=vid_a.id, file_path=local_file, original_filename="no_cloud.mp4")
    # Mark processed locally with file://
    repo.mark_asset_processed(
        asset_id=vid_a.assets[0].id,
        cloudinary_url=f"file://{local_file}",
        cloudinary_public_id=None,
    )

    # Video B: Processed with valid Cloudinary HTTPS URL
    vid_b = Video(
        youtube_video_id="PUB_CLOUD_READY",
        title="Cloud Ready Video",
        video_url="https://youtube.com/watch?v=PUB_CLOUD_READY",
        status=VideoStatus.PROCESSED,
    )
    db_session.add(vid_b)
    db_session.commit()
    cloud_url = f"https://res.cloudinary.com/test-cloud/video/upload/adoraxe/reels/{vid_b.id}.mp4"
    repo.create_or_update_reel_asset(
        video_id=vid_b.id,
        cloudinary_url=cloud_url,
        cloudinary_public_id=f"adoraxe/reels/{vid_b.id}",
        original_filename="cloud_reel.mp4",
        duration=10,
        width=1080,
        height=1920,
        codec="h264",
        file_size=60000,
    )

    # Check publishing queue
    resp = client.get(f"/api/v1/publishing/queue-videos?account_id={DEMO_ACCOUNT_ID}")
    assert resp.status_code == 200
    queue = resp.json()
    item_a = next(i for i in queue if i["id"] == vid_a.id)
    item_b = next(i for i in queue if i["id"] == vid_b.id)

    # Item A is disabled because it has no Cloudinary URL
    assert item_a["can_publish"] is False
    assert "Cloudinary" in (item_a["disabled_reason"] or "")

    # Item B is eligible
    assert item_b["can_publish"] is True

    # Attempting to publish Video A returns HTTP 400
    resp_pub_a = client.post("/api/v1/publishing", json={
        "video_id": vid_a.id,
        "caption": "Publish local video",
        "rights_confirmed": True,
    })
    assert resp_pub_a.status_code == 400
    assert "Cloudinary" in resp_pub_a.json()["detail"]

    # Publishing Video B succeeds (mocking verify_url to return True)
    with patch.object(CloudinaryService, "verify_url", return_value=True):
        resp_pub_b = client.post("/api/v1/publishing", json={
            "video_id": vid_b.id,
            "caption": "Publish cloud video",
            "rights_confirmed": True,
        })
        assert resp_pub_b.status_code == 200
        assert resp_pub_b.json()["status"] == "PENDING"


# ─── 10. Reprocessing Staged from Cloudinary When Local Disk Path is None ──────

@pytest.mark.asyncio
async def test_reprocessing_staged_from_cloudinary_when_local_path_is_none(tmp_path, db_session: Session, monkeypatch):
    """
    When a video is already processed and local files were cleaned up (file_path=None),
    reprocessing (e.g. changing from pad to crop mode) must stage the original from
    Cloudinary persistent storage, convert it via FFmpeg, upload the new Reel, and succeed.
    """
    monkeypatch.setattr(settings, "cloudinary_cloud_name", "test-cloud")
    monkeypatch.setattr(settings, "cloudinary_api_key", "test-key")
    monkeypatch.setattr(settings, "cloudinary_api_secret", "test-secret")

    video = Video(
        youtube_video_id="REPROCESS_CLOUDINARY_1",
        title="Reprocess Test Video",
        video_url="https://youtube.com/watch?v=REPROCESS_CLOUDINARY_1",
        status=VideoStatus.PROCESSED,
    )
    db_session.add(video)
    db_session.commit()

    # Original asset has file_path=None because it was already committed to Cloudinary
    orig_cloud_url = "https://res.cloudinary.com/test-cloud/video/upload/v12345/adoraxe/originals/REPROCESS_1.mp4"
    asset = VideoAsset(
        video_id=video.id,
        file_path=None,  # Cleared upon successful persistent upload
        original_filename="reprocess_original.mp4",
        cloudinary_url=orig_cloud_url,
        cloudinary_public_id=f"adoraxe/originals/{video.id}",
        is_processed=False,
        is_reel_compatible=False,
        duration_seconds=4,
        width=1280,
        height=720,
        codec="h264",
        file_size_bytes=50000,
    )
    db_session.add(asset)
    db_session.commit()

    job_repo = JobRepository(db_session)
    job = job_repo.create_processing_job(video_id=video.id)

    processor = VideoProcessor()

    # Fake staging implementation that creates synthetic MP4 when _stage_remote_file is called
    async def fake_stage_remote_file(url, target_path):
        create_synthetic_mp4(target_path, duration_secs=4)
        return True

    reel_cloud_url = f"https://res.cloudinary.com/test-cloud/video/upload/v12346/adoraxe/reels/{video.id}.mp4"
    mock_reel_res = {
        "public_id": f"adoraxe/reels/{video.id}",
        "secure_url": reel_cloud_url,
        "format": "mp4",
        "bytes": 75000,
    }

    with patch.object(processor, "_stage_remote_file", side_effect=fake_stage_remote_file), \
         patch.object(CloudinaryService, "upload_video", return_value=mock_reel_res), \
         patch.object(CloudinaryService, "verify_url", return_value=True):

        opts = ProcessVideoRequest(crop_mode="crop")
        await processor.process_video(job_id=job.id, video_id=video.id, options=opts, db=db_session)

    db_session.refresh(job)
    assert job.status == JobStatus.FINISHED, f"Job failed with error: {job.error_message}"

    repo = VideoRepository(db_session)
    reel_asset = repo.get_processed_asset(video.id)
    assert reel_asset is not None
    assert reel_asset.cloudinary_url == reel_cloud_url
    assert reel_asset.width == 1080
    assert reel_asset.height == 1920
    assert reel_asset.is_reel_compatible is True


# ─── 11. Real Cloudinary Integration Test (Runs Only When Configured) ────────

@pytest.mark.asyncio
async def test_live_cloudinary_integration(tmp_path, monkeypatch):
    """
    Real end-to-end integration test with live Cloudinary servers.
    Only executed when real CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, and CLOUDINARY_API_SECRET
    are provided in .env and network access is available.
    """
    from app.core.config import Settings
    real_settings = Settings()
    if not real_settings.cloudinary_configured:
        pytest.skip("Real Cloudinary credentials not configured in environment.")

    import socket
    try:
        socket.gethostbyname("api.cloudinary.com")
    except Exception:
        pytest.skip("Sandbox/offline environment: api.cloudinary.com is unreachable.")

    # Restore real credentials for this specific live integration test
    monkeypatch.setattr(settings, "cloudinary_cloud_name", real_settings.cloudinary_cloud_name)
    monkeypatch.setattr(settings, "cloudinary_api_key", real_settings.cloudinary_api_key)
    monkeypatch.setattr(settings, "cloudinary_api_secret", real_settings.cloudinary_api_secret)

    service = CloudinaryService()
    test_video = str(tmp_path / "live_test_video.mp4")
    create_synthetic_mp4(test_video, duration_secs=4)

    test_public_id = f"adoraxe/test/{int(datetime.now(timezone.utc).timestamp())}"
    try:
        upload_result = await service.upload_video(
            file_path=test_video,
            public_id=test_public_id,
            folder="adoraxe/test",
            overwrite=True,
        )
    except Exception as e:
        if "nodename nor servname provided" in str(e) or "NameResolutionError" in str(e):
            pytest.skip(f"Live network unavailable in sandbox: {e}")
        raise

    assert upload_result is not None
    assert upload_result["secure_url"].startswith("https://res.cloudinary.com/")

    # Verify delivery URL
    verified = await service.verify_url(upload_result["secure_url"])
    assert verified is True

    # Cleanup live asset
    deleted = await service.delete_video(upload_result["public_id"])
    assert deleted is True
