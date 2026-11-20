"""
Tests for video processing concurrency, idempotency, stale job detection,
and state-machine transition validation.
"""
import asyncio
import os
import subprocess
from datetime import datetime, timezone, timedelta
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.models import Video, VideoAsset, VideoStatus, PublishingJob, JobStatus
from app.repositories.video_repository import VideoRepository
from app.repositories.job_repository import JobRepository
from app.services.video.processor import VideoProcessor, _PROCESSING_SEMAPHORE
from app.schemas.schemas import ProcessVideoRequest
from app.core.security import clear_failed_attempts_for_testing


TEST_ADMIN_KEY = "test-proc-admin-key-32-chars-long"


def create_synthetic_mp4(output_path: str, duration_secs: int = 2) -> str:
    """Generate small valid synthetic MP4 file using FFmpeg."""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"testsrc=duration={duration_secs}:size=640x360:rate=30",
        "-f", "lavfi", "-i", f"sine=frequency=1000:duration={duration_secs}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
        output_path
    ]
    res = subprocess.run(cmd, capture_output=True)
    if res.returncode != 0:
        raise RuntimeError(f"FFmpeg synthetic video creation failed: {res.stderr.decode()}")
    return output_path


# ─── 1. Idempotency: Multiple Calls Return Same Active Job ─────────────────────

def test_process_video_idempotency_prevents_duplicate_jobs(client: TestClient, db_session: Session, tmp_path, monkeypatch):
    """Calling POST /api/v1/videos/{id}/process repeatedly returns the active job without spawning duplicates."""
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "admin_api_key", TEST_ADMIN_KEY)
    monkeypatch.setattr(settings, "dev_auth_bypass", False)
    clear_failed_attempts_for_testing()

    raw_mp4 = str(tmp_path / "idempotent.mp4")
    create_synthetic_mp4(raw_mp4)

    video = Video(
        youtube_video_id="IDEMPOTENCY_TEST_01",
        title="Idempotency Test Video",
        video_url="https://youtube.com/watch?v=IDEMPOTENCY_TEST_01",
        status=VideoStatus.IMPORTED,
    )
    db_session.add(video)
    db_session.commit()

    repo = VideoRepository(db_session)
    repo.create_asset(video_id=video.id, file_path=raw_mp4, original_filename="idempotent.mp4")

    headers = {"X-Admin-Key": TEST_ADMIN_KEY}

    # Simulate in-flight processing job by keeping status in PROCESSING during the calls
    async def fake_in_flight_process(job_id, video_id, options, db=None):
        pass  # Leaves job in its initial PENDING/PROCESSING state to simulate active background task

    with patch.object(VideoProcessor, "process_video", side_effect=fake_in_flight_process):
        # First call: triggers processing, creates job 1
        res1 = client.post(f"/api/v1/videos/{video.id}/process", json={"crop_mode": "pad"}, headers=headers)
        assert res1.status_code == 200
        job1_id = res1.json()["job_id"]

        # Second call (immediate repeated submission while job 1 is in-flight): returns active job 1
        res2 = client.post(f"/api/v1/videos/{video.id}/process", json={"crop_mode": "pad"}, headers=headers)
        assert res2.status_code == 200
        job2_id = res2.json()["job_id"]
        assert job2_id == job1_id
        assert "already in progress" in res2.json()["message"].lower()

        # Third call: still returns job 1
        res3 = client.post(f"/api/v1/videos/{video.id}/process", json={"crop_mode": "pad"}, headers=headers)
        assert res3.status_code == 200
        assert res3.json()["job_id"] == job1_id

    # Verify only ONE job exists in the database for this video
    job_repo = JobRepository(db_session)
    jobs = db_session.query(PublishingJob).filter(PublishingJob.video_id == video.id).all()
    assert len(jobs) == 1
    assert jobs[0].id == job1_id


# ─── 2. Video Detail Response Surfaces Active Job ID & Is Processing ──────────

def test_video_details_surfaces_active_job_id_and_processing_flag(client: TestClient, db_session: Session, tmp_path, monkeypatch):
    """GET /api/v1/videos/{id} returns active_job_id and is_processing=True when a job is in-flight."""
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "admin_api_key", TEST_ADMIN_KEY)
    clear_failed_attempts_for_testing()

    video = Video(
        youtube_video_id="ACTIVE_JOB_FLAG_TEST",
        title="Active Job Flag Test Video",
        video_url="https://youtube.com/watch?v=ACTIVE_JOB_FLAG_TEST",
        status=VideoStatus.IMPORTED,
    )
    db_session.add(video)
    db_session.commit()

    job_repo = JobRepository(db_session)
    job = job_repo.create_processing_job(video.id)
    job_repo.update_status(job.id, JobStatus.PROCESSING, progress=35)

    headers = {"X-Admin-Key": TEST_ADMIN_KEY}
    res = client.get(f"/api/v1/videos/{video.id}", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["active_job_id"] == job.id
    assert data["is_processing"] is True


# ─── 3. State Machine Self-Transition Warning Suppression ─────────────────────

def test_processing_progress_updates_do_not_log_unusual_transition(db_session: Session, caplog):
    """Updating progress within PROCESSING status does not trigger 'Unusual state transition' warnings."""
    import logging
    caplog.set_level(logging.WARNING)

    video = Video(
        youtube_video_id="TRANSITION_WARNING_TEST",
        title="Transition Warning Test Video",
        video_url="https://youtube.com/watch?v=TRANSITION_WARNING_TEST",
        status=VideoStatus.IMPORTED,
    )
    db_session.add(video)
    db_session.commit()

    job_repo = JobRepository(db_session)
    job = job_repo.create_processing_job(video.id)

    # Initial transition: PENDING -> PROCESSING
    job_repo.update_status(job.id, JobStatus.PROCESSING, progress=5)

    # Subsequent progress milestones within PROCESSING (the exact sequence that caused the production warning)
    milestones = [8, 10, 20, 30, 60, 70, 75, 85, 90]
    for m in milestones:
        job_repo.update_status(job.id, JobStatus.PROCESSING, progress=m)

    # Assert that NO 'Unusual state transition' was logged for any of these updates
    warnings = [rec.message for rec in caplog.records if "Unusual state transition" in rec.message]
    assert len(warnings) == 0, f"Unexpected transition warnings: {warnings}"


# ─── 4. Stale Processing Job Expiration & Re-processing ────────────────────────

def test_stale_processing_job_expires_and_allows_reprocessing(client: TestClient, db_session: Session, tmp_path, monkeypatch):
    """When a job is abandoned (>10m lease expired), subsequent process call expires it and creates a fresh job."""
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "admin_api_key", TEST_ADMIN_KEY)
    clear_failed_attempts_for_testing()

    raw_mp4 = str(tmp_path / "stale_test.mp4")
    create_synthetic_mp4(raw_mp4)

    video = Video(
        youtube_video_id="STALE_RECOVERY_TEST",
        title="Stale Recovery Test Video",
        video_url="https://youtube.com/watch?v=STALE_RECOVERY_TEST",
        status=VideoStatus.IMPORTED,
    )
    db_session.add(video)
    db_session.commit()

    repo = VideoRepository(db_session)
    repo.create_asset(video_id=video.id, file_path=raw_mp4, original_filename="stale_test.mp4")

    job_repo = JobRepository(db_session)
    stale_job = job_repo.create_processing_job(video.id)
    job_repo.update_status(stale_job.id, JobStatus.PROCESSING, progress=40)

    # Backdate stale_job timestamps to simulate an interrupted run from 20 minutes ago
    stale_time = datetime.utcnow() - timedelta(minutes=20)
    stale_job.started_at = stale_time
    stale_job.updated_at = stale_time
    stale_job.created_at = stale_time
    db_session.commit()

    assert job_repo.is_processing_job_stale(stale_job) is True

    headers = {"X-Admin-Key": TEST_ADMIN_KEY}

    # New process call detects stale job, marks it FAILED, and creates fresh job
    res = client.post(f"/api/v1/videos/{video.id}/process", json={"crop_mode": "pad"}, headers=headers)
    assert res.status_code == 200
    new_job_id = res.json()["job_id"]
    assert new_job_id != stale_job.id

    # Verify stale job was expired to FAILED with descriptive error
    db_session.refresh(stale_job)
    assert stale_job.status == JobStatus.FAILED.value
    assert "timed out" in stale_job.error_message.lower() or "stale" in stale_job.error_message.lower()


# ─── 5. Concurrency Semaphore Limits Simultaneous FFmpeg Conversions ──────────

@pytest.mark.asyncio
async def test_processing_semaphore_prevents_simultaneous_ffmpeg(tmp_path):
    """The _PROCESSING_SEMAPHORE ensures only 1 FFmpeg conversion runs at any moment."""
    assert _PROCESSING_SEMAPHORE._value == 1

    # Acquire semaphore
    async with _PROCESSING_SEMAPHORE:
        # Semaphore value is now 0 (locked)
        assert _PROCESSING_SEMAPHORE._value == 0

    # Released back to 1
    assert _PROCESSING_SEMAPHORE._value == 1


# ─── 6. Recovery From Cloudinary Original Does Not Duplicate Processed Assets ──

@pytest.mark.asyncio
async def test_recovery_from_cloudinary_avoids_duplicate_processed_assets(tmp_path, db_session: Session, monkeypatch):
    """When re-processing a video, create_or_update_reel_asset updates the existing Reel asset without duplicate rows."""
    video = Video(
        youtube_video_id="DEDUP_ASSET_TEST",
        title="Dedup Asset Test Video",
        video_url="https://youtube.com/watch?v=DEDUP_ASSET_TEST",
        status=VideoStatus.PROCESSED,
    )
    db_session.add(video)
    db_session.commit()

    repo = VideoRepository(db_session)

    # First Reel creation (e.g. crop mode)
    reel1 = repo.create_or_update_reel_asset(
        video_id=video.id,
        cloudinary_url="https://res.cloudinary.com/test/video/upload/adoraxe/reels/v1.mp4",
        cloudinary_public_id="adoraxe/reels/v1",
        original_filename="v1_reel.mp4",
        duration=5,
        width=1080,
        height=1920,
        codec="h264",
        file_size=50000,
        crop_mode="crop",
    )
    assert reel1 is not None

    # Re-process with different mode (e.g. pad mode)
    reel2 = repo.create_or_update_reel_asset(
        video_id=video.id,
        cloudinary_url="https://res.cloudinary.com/test/video/upload/adoraxe/reels/v2.mp4",
        cloudinary_public_id="adoraxe/reels/v2",
        original_filename="v2_reel.mp4",
        duration=5,
        width=1080,
        height=1920,
        codec="h264",
        file_size=52000,
        crop_mode="pad",
    )

    # Both refer to the exact same database record (updated in place)
    assert reel1.id == reel2.id
    assert reel2.crop_mode == "pad"
    assert reel2.cloudinary_url == "https://res.cloudinary.com/test/video/upload/adoraxe/reels/v2.mp4"

    # Assert exactly ONE processed asset exists for this video
    processed_assets = db_session.query(VideoAsset).filter(
        VideoAsset.video_id == video.id,
        VideoAsset.is_processed == True,
    ).all()
    assert len(processed_assets) == 1
