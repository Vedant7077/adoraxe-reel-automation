"""
Tests for duplicate publishing prevention, race protection, and timezone serialization.
"""
import pytest
from unittest.mock import patch
from datetime import datetime, timezone
from app.models.models import Video, VideoStatus, InstagramAccount, InstagramAccountStatus, PublishingJob, PublishingHistory, JobStatus, PublishStatus
from app.repositories.instagram_repository import InstagramRepository, DEMO_ACCOUNT_ID, DEMO_USERNAME
from app.repositories.job_repository import JobRepository
from app.repositories.video_repository import VideoRepository


@pytest.fixture(autouse=True)
def mock_publish_reel():
    with patch("app.services.instagram.publisher.InstagramPublisher.publish_reel") as mock, \
         patch("app.services.cloudinary.cloudinary_service.CloudinaryService.verify_url", return_value=True):
        yield mock


@pytest.fixture
def video_pair(db_session):
    repo = VideoRepository(db_session)
    v1 = Video(
        youtube_video_id="DUP_TEST_01",
        title="Duplicate Test Video 1",
        video_url="https://youtu.be/dup1",
        status=VideoStatus.PROCESSED,
    )
    v2 = Video(
        youtube_video_id="DUP_TEST_02",
        title="Duplicate Test Video 2",
        video_url="https://youtu.be/dup2",
        status=VideoStatus.PROCESSED,
    )
    db_session.add_all([v1, v2])
    db_session.commit()
    db_session.refresh(v1)
    db_session.refresh(v2)

    # Attach processed Cloudinary assets so videos are eligible for publishing queue tests
    from app.models.models import VideoAsset
    a1 = VideoAsset(
        video_id=v1.id,
        cloudinary_url=f"https://res.cloudinary.com/demo/video/upload/adoraxe/reels/{v1.id}.mp4",
        cloudinary_public_id=f"adoraxe/reels/{v1.id}",
        original_filename=f"{v1.id}_reel.mp4",
        is_processed=True,
        is_reel_compatible=True,
    )
    a2 = VideoAsset(
        video_id=v2.id,
        cloudinary_url=f"https://res.cloudinary.com/demo/video/upload/adoraxe/reels/{v2.id}.mp4",
        cloudinary_public_id=f"adoraxe/reels/{v2.id}",
        original_filename=f"{v2.id}_reel.mp4",
        is_processed=True,
        is_reel_compatible=True,
    )
    db_session.add_all([a1, a2])
    db_session.commit()
    db_session.refresh(v1)
    db_session.refresh(v2)
    return v1, v2


def test_duplicate_prevention_simulated_publish(client, db_session, video_pair):
    v1, _ = video_pair
    # 1. First publish succeeds
    resp1 = client.post("/api/v1/publishing", json={
        "video_id": v1.id,
        "caption": "First publish attempt",
        "rights_confirmed": True,
    })
    assert resp1.status_code == 200
    job_id = resp1.json()["job_id"]

    # In DEMO_MODE, job is created with PENDING
    # 2. Immediate second publish while job is active -> 409 Conflict
    resp_active = client.post("/api/v1/publishing", json={
        "video_id": v1.id,
        "caption": "Duplicate in-flight attempt",
        "rights_confirmed": True,
    })
    assert resp_active.status_code == 409
    assert "already in progress" in resp_active.json()["detail"].lower()

    # Simulate job finishing and history entry creation
    job_repo = JobRepository(db_session)
    job_repo.update_status(job_id, JobStatus.FINISHED, progress=100)
    history = PublishingHistory(
        publishing_job_id=job_id,
        video_id=v1.id,
        instagram_account_id=DEMO_ACCOUNT_ID,
        video_title=v1.title,
        instagram_username=DEMO_USERNAME,
        status=PublishStatus.SIMULATED,
        published_at=datetime.now(timezone.utc),
    )
    db_session.add(history)
    db_session.commit()

    # 3. Third publish after completion -> 409 Conflict (Already published/simulated)
    resp_after = client.post("/api/v1/publishing", json={
        "video_id": v1.id,
        "caption": "Duplicate completed attempt",
        "rights_confirmed": True,
    })
    assert resp_after.status_code == 409
    assert "already been published" in resp_after.json()["detail"].lower()


def test_failed_job_remains_retryable(client, db_session, video_pair):
    v1, _ = video_pair
    job_repo = JobRepository(db_session)

    # Create a job that FAILED
    failed_job = job_repo.create_publish_job(
        video_id=v1.id,
        instagram_account_id=DEMO_ACCOUNT_ID,
        caption="Failed job",
        is_demo=True,
    )
    job_repo.update_status(failed_job.id, JobStatus.FAILED, error="Simulated network failure")

    # Publishing again should succeed (retryable)
    resp = client.post("/api/v1/publishing", json={
        "video_id": v1.id,
        "caption": "Retry attempt after failure",
        "rights_confirmed": True,
    })
    assert resp.status_code == 200
    assert resp.json()["status"] == "PENDING"


def test_different_account_can_publish_same_video(client, db_session, video_pair):
    v1, _ = video_pair
    job_repo = JobRepository(db_session)

    # Account 1 publishes v1
    acc1_id = "11111111-1111-1111-1111-111111111111"
    acc2_id = "22222222-2222-2222-2222-222222222222"

    acc1 = InstagramAccount(
        id=acc1_id,
        instagram_user_id="user_acc1",
        username="creator_one",
        status=InstagramAccountStatus.CONNECTED,
    )
    acc2 = InstagramAccount(
        id=acc2_id,
        instagram_user_id="user_acc2",
        username="creator_two",
        status=InstagramAccountStatus.CONNECTED,
    )
    db_session.add_all([acc1, acc2])
    db_session.commit()

    # Mark v1 published for Account 1
    job1 = job_repo.create_publish_job(video_id=v1.id, instagram_account_id=acc1_id, caption="Cap 1", is_demo=False)
    job_repo.update_status(job1.id, JobStatus.FINISHED)
    h1 = PublishingHistory(
        publishing_job_id=job1.id,
        video_id=v1.id,
        instagram_account_id=acc1_id,
        video_title=v1.title,
        status=PublishStatus.PUBLISHED,
        published_at=datetime.now(timezone.utc),
    )
    db_session.add(h1)
    db_session.commit()

    # Duplicate check for Account 1: duplicate!
    is_dup1, _ = job_repo.check_duplicate_publish(v1.id, acc1_id, is_demo=False)
    assert is_dup1 is True

    # Duplicate check for Account 2: allowed!
    is_dup2, _ = job_repo.check_duplicate_publish(v1.id, acc2_id, is_demo=False)
    assert is_dup2 is False


def test_queue_videos_endpoint_marks_duplicates(client, db_session, video_pair):
    v1, v2 = video_pair

    # Mark v1 already simulated in demo mode
    job_repo = JobRepository(db_session)
    job = job_repo.create_publish_job(video_id=v1.id, instagram_account_id=DEMO_ACCOUNT_ID, caption="Cap", is_demo=True)
    job_repo.update_status(job.id, JobStatus.FINISHED)
    h = PublishingHistory(
        publishing_job_id=job.id,
        video_id=v1.id,
        instagram_account_id=DEMO_ACCOUNT_ID,
        video_title=v1.title,
        status=PublishStatus.SIMULATED,
        published_at=datetime.now(timezone.utc),
    )
    db_session.add(h)
    db_session.commit()

    # Fetch queue videos
    resp = client.get("/api/v1/publishing/queue-videos")
    assert resp.status_code == 200
    items = resp.json()

    v1_item = next(i for i in items if i["id"] == v1.id)
    v2_item = next(i for i in items if i["id"] == v2.id)

    assert v1_item["can_publish"] is False
    assert v1_item["is_already_published"] is True
    assert "already been published" in v1_item["disabled_reason"].lower()

    assert v2_item["can_publish"] is True
    assert v2_item["is_already_published"] is False
    assert v2_item["disabled_reason"] is None


def test_api_timestamps_have_utc_z_indicator(client, db_session, video_pair):
    v1, _ = video_pair
    # Query history endpoint
    h = PublishingHistory(
        publishing_job_id="dummy-job-id",
        video_id=v1.id,
        instagram_account_id=DEMO_ACCOUNT_ID,
        video_title="Timezone Check",
        status=PublishStatus.SIMULATED,
        published_at=datetime(2026, 11, 19, 7, 59, 0),
        created_at=datetime(2026, 11, 19, 7, 59, 0),
    )
    db_session.add(h)
    db_session.commit()

    resp = client.get("/api/v1/history")
    assert resp.status_code == 200
    item = next(i for i in resp.json()["items"] if i["video_title"] == "Timezone Check")
    assert item["created_at"].endswith("Z")
    assert item["published_at"].endswith("Z")
    assert "2026-11-19T07:59:00Z" == item["published_at"]


def test_concurrent_publish_requests_rejected(client, db_session, video_pair):
    from concurrent.futures import ThreadPoolExecutor
    v1, _ = video_pair

    def try_publish():
        return client.post("/api/v1/publishing", json={
            "video_id": v1.id,
            "caption": "Concurrent test publish",
            "rights_confirmed": True,
        })

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(try_publish) for _ in range(4)]
        responses = [f.result() for f in futures]

    statuses = [r.status_code for r in responses]
    # Exactly one request must succeed (200), and all remaining requests must be rejected with 409 Conflict
    assert statuses.count(200) == 1
    assert statuses.count(409) == 3
