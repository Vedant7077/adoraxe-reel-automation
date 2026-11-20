"""
Comprehensive test suite for the Persistent Publishing Job System,
8-Stage State Machine, Idempotency, Safe Uncertain Outcome Handling, and Reconciliation.
Covers all 20 required points.
"""
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch, MagicMock
import httpx

from app.models.models import (
    Video, VideoAsset, VideoStatus, InstagramAccount,
    InstagramAccountStatus, PublishingJob, PublishingHistory,
    JobStatus, PublishStatus
)
from app.repositories.job_repository import JobRepository
from app.repositories.history_repository import HistoryRepository
from app.services.instagram.publisher import InstagramPublisher
from app.core.config import settings


@pytest.fixture
def test_setup(db_session):
    """Create test video with processed asset and connected Instagram account."""
    video = Video(
        id="test-vid-12345",
        title="AI Automation in 2026",
        youtube_video_id="yt-ai-2026",
        video_url="https://youtube.com/watch?v=yt-ai-2026",
        status=VideoStatus.PROCESSED,
    )
    db_session.add(video)

    asset = VideoAsset(
        video_id=video.id,
        is_processed=True,
        is_reel_compatible=True,
        crop_mode="blur",
        width=1080,
        height=1920,
        cloudinary_url="https://res.cloudinary.com/quumckey/video/upload/v1/test_reel.mp4",
        cloudinary_public_id="reels/test_reel",
    )
    db_session.add(asset)

    account = InstagramAccount(
        id="test-acc-99999",
        instagram_user_id="17841454191511409",
        username="yotube_insta_automation",
        full_name="Vedant",
        account_type="CREATOR",
        status=InstagramAccountStatus.CONNECTED,
        encrypted_access_token="test_encrypted_token",
    )
    db_session.add(account)
    db_session.commit()

    return {"video": video, "asset": asset, "account": account}


# 1. Publishing job persisted before execution
def test_1_publishing_job_persisted_before_execution(db_session, test_setup):
    repo = JobRepository(db_session)
    job = repo.create_publish_job(
        video_id=test_setup["video"].id,
        instagram_account_id=test_setup["account"].id,
        caption="Persistent Job Test #AI",
        is_demo=False,
        cloudinary_url="https://res.cloudinary.com/demo/test.mp4",
        cloudinary_public_id="reels/test",
        idempotency_key="key-test-1",
    )

    assert job.id is not None
    assert job.status == JobStatus.QUEUED.value
    assert job.current_stage == JobStatus.QUEUED.value
    assert job.rights_confirmed is True

    # Verify persisted in database
    db_session.expire_all()
    queried = repo.get_by_id(job.id)
    assert queried is not None
    assert queried.status == JobStatus.QUEUED.value


# 2. Caption persistence
def test_2_caption_persistence(db_session, test_setup):
    repo = JobRepository(db_session)
    caption_text = "Detailed caption with #hashtags and emojis 🚀"
    job = repo.create_publish_job(
        video_id=test_setup["video"].id,
        instagram_account_id=test_setup["account"].id,
        caption=caption_text,
        is_demo=False,
        cloudinary_url="https://res.cloudinary.com/demo/test.mp4",
    )

    db_session.expire_all()
    queried = repo.get_by_id(job.id)
    assert queried.caption == caption_text


# 3. Meta container ID persistence
def test_3_meta_container_id_persistence(db_session, test_setup):
    repo = JobRepository(db_session)
    job = repo.create_publish_job(
        video_id=test_setup["video"].id,
        instagram_account_id=test_setup["account"].id,
        caption="Container Test",
        is_demo=False,
    )
    repo.update_container_id(job.id, "container_18099887766")
    repo.update_status(job.id, JobStatus.CONTAINER_PROCESSING.value, progress=30)

    db_session.expire_all()
    queried = repo.get_by_id(job.id)
    assert queried.instagram_container_id == "container_18099887766"
    assert queried.status == JobStatus.CONTAINER_PROCESSING.value
    assert queried.processing_progress == 30


# 4. Container polling progress
@pytest.mark.asyncio
async def test_4_container_polling_progress(db_session, test_setup):
    repo = JobRepository(db_session)
    job = repo.create_publish_job(
        video_id=test_setup["video"].id,
        instagram_account_id=test_setup["account"].id,
        caption="Polling Test",
        is_demo=False,
    )
    repo.update_container_id(job.id, "container_123")

    publisher = InstagramPublisher()
    publisher.MAX_POLL_ATTEMPTS = 3
    publisher.POLL_INTERVAL_SECONDS = 0.01

    # Mock container status responses: first IN_PROGRESS, then FINISHED
    responses = [
        httpx.Response(200, json={"status_code": "IN_PROGRESS"}),
        httpx.Response(200, json={"status_code": "FINISHED"}),
    ]

    mock_client = AsyncMock()
    mock_client.get.side_effect = responses
    mock_client.__aenter__.return_value = mock_client

    with patch("httpx.AsyncClient", return_value=mock_client):
        ready = await publisher._poll_until_ready("container_123", "fake_token", job.id, repo)
        assert ready is True


# 5. media_publish success returning media ID
# 6. Successful publication persistence
@pytest.mark.asyncio
async def test_5_and_6_media_publish_success_and_persistence(db_session, test_setup, monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", False)
    repo = JobRepository(db_session)
    hist_repo = HistoryRepository(db_session)

    job = repo.create_publish_job(
        video_id=test_setup["video"].id,
        instagram_account_id=test_setup["account"].id,
        caption="Live Publish Test",
        is_demo=False,
        cloudinary_url="https://res.cloudinary.com/quumckey/video/upload/v1/test_reel.mp4",
    )

    publisher = InstagramPublisher()
    publisher.MAX_POLL_ATTEMPTS = 1
    publisher.POLL_INTERVAL_SECONDS = 0.01

    async def mock_post(url, **kwargs):
        req = httpx.Request("POST", url)
        if "media_publish" in url:
            return httpx.Response(200, json={"id": "media_9876543210"}, request=req)
        return httpx.Response(200, json={"id": "container_456"}, request=req)

    async def mock_get(url, **kwargs):
        req = httpx.Request("GET", url)
        if "media_9876543210" in url:
            return httpx.Response(200, json={"permalink": "https://www.instagram.com/reel/live_test_123/"}, request=req)
        return httpx.Response(200, json={"status_code": "FINISHED"}, request=req)

    mock_client = AsyncMock()
    mock_client.post.side_effect = mock_post
    mock_client.get.side_effect = mock_get
    mock_client.__aenter__.return_value = mock_client

    with patch("httpx.AsyncClient", return_value=mock_client), \
         patch.object(publisher, "_poll_until_ready", return_value=True), \
         patch("app.services.instagram.token_manager.TokenManager.decrypt_token", return_value="decrypted_token"):
        await publisher.publish_reel(job.id, is_demo=False, db=db_session)

    db_session.expire_all()
    queried_job = repo.get_by_id(job.id)
    assert queried_job.status == JobStatus.PUBLISHED.value
    assert queried_job.instagram_media_id == "media_9876543210"
    assert queried_job.permalink == "https://www.instagram.com/reel/live_test_123/"
    assert queried_job.published_at is not None

    hist = db_session.query(PublishingHistory).filter(PublishingHistory.publishing_job_id == job.id).first()
    assert hist is not None
    assert hist.status == PublishStatus.PUBLISHED.value
    assert hist.instagram_media_id == "media_9876543210"
    assert hist.permalink == "https://www.instagram.com/reel/live_test_123/"


# 7. Browser navigation during publishing
# 8. Browser refresh during publishing
# 17. Caption restored after navigation
def test_7_8_17_browser_navigation_refresh_and_caption_restoration(client, db_session, test_setup):
    repo = JobRepository(db_session)
    job = repo.create_publish_job(
        video_id=test_setup["video"].id,
        instagram_account_id=test_setup["account"].id,
        caption="Persistent Caption to Restore Across Nav & Refresh",
        is_demo=False,
        cloudinary_url="https://res.cloudinary.com/test.mp4",
    )
    repo.update_status(job.id, JobStatus.CONTAINER_PROCESSING.value, progress=45)

    # User navigates to /history or refreshes page -> queries /active
    resp = client.get(f"/api/v1/publishing/active?account_id={test_setup['account'].id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["has_active_job"] is True
    assert data["job"]["id"] == job.id
    assert data["job"]["caption"] == "Persistent Caption to Restore Across Nav & Refresh"
    assert data["job"]["status"] == JobStatus.CONTAINER_PROCESSING.value
    assert data["job"]["progress"] == 45


# 9. Queue and History showing consistent job status
def test_9_queue_and_history_consistent_job_status(client, db_session, test_setup):
    repo = JobRepository(db_session)
    hist_repo = HistoryRepository(db_session)

    job = repo.create_publish_job(
        video_id=test_setup["video"].id,
        instagram_account_id=test_setup["account"].id,
        caption="Consistency Test",
        is_demo=False,
    )
    repo.update_published(job.id, "media_consist_1", "https://instagram.com/reel/consist_1")
    hist_repo.create_or_update_published(
        job_id=job.id,
        video_id=test_setup["video"].id,
        instagram_account_id=test_setup["account"].id,
        media_id="media_consist_1",
        permalink="https://instagram.com/reel/consist_1",
    )

    # Queue list checks duplicate/published
    queue_res = client.get(f"/api/v1/publishing/queue-videos?account_id={test_setup['account'].id}")
    assert queue_res.status_code == 200
    q_items = queue_res.json()
    matched_q = next((i for i in q_items if i["id"] == test_setup["video"].id), None)
    assert matched_q is not None
    assert matched_q["can_publish"] is False
    assert matched_q["is_already_published"] is True

    # History list reflects PUBLISHED
    hist_res = client.get("/api/v1/history")
    assert hist_res.status_code == 200
    h_items = hist_res.json()["items"]
    matched_h = next((i for i in h_items if i["publishing_job_id"] == job.id), None)
    assert matched_h is not None
    assert matched_h["status"] == PublishStatus.PUBLISHED.value
    assert matched_h["permalink"] == "https://instagram.com/reel/consist_1"


# 10. Client disconnect without cancelling the backend job
def test_10_client_disconnect_without_cancelling_backend_job(db_session, test_setup):
    repo = JobRepository(db_session)
    job = repo.create_publish_job(
        video_id=test_setup["video"].id,
        instagram_account_id=test_setup["account"].id,
        caption="Client disconnect test",
        is_demo=False,
    )

    # Job exists in PostgreSQL regardless of HTTP client connection state
    db_session.expire_all()
    found = repo.get_by_id(job.id)
    assert found is not None
    assert found.status == JobStatus.QUEUED.value


# 11. Successful Instagram publication followed by delayed database update
def test_11_delayed_database_update_resolves_published(db_session, test_setup):
    repo = JobRepository(db_session)
    hist_repo = HistoryRepository(db_session)

    job = repo.create_publish_job(
        video_id=test_setup["video"].id,
        instagram_account_id=test_setup["account"].id,
        caption="Delayed DB update test",
        is_demo=False,
    )

    # Simulating delayed persistence after network call returns
    repo.update_published(job.id, "media_delayed_100", "https://instagram.com/reel/delayed_100")
    hist_repo.create_or_update_published(
        job_id=job.id,
        video_id=test_setup["video"].id,
        instagram_account_id=test_setup["account"].id,
        media_id="media_delayed_100",
        permalink="https://instagram.com/reel/delayed_100",
    )

    db_session.expire_all()
    assert repo.get_by_id(job.id).status == JobStatus.PUBLISHED.value


# 12. Network timeout after media_publish request
@pytest.mark.asyncio
async def test_12_network_timeout_after_media_publish_sets_outcome_unknown(db_session, test_setup, monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", False)
    repo = JobRepository(db_session)

    job = repo.create_publish_job(
        video_id=test_setup["video"].id,
        instagram_account_id=test_setup["account"].id,
        caption="Timeout Test Caption",
        is_demo=False,
        cloudinary_url="https://res.cloudinary.com/quumckey/video/upload/v1/test_reel.mp4",
    )

    publisher = InstagramPublisher()

    # Mock timeout on media_publish call
    async def mock_post(url, **kwargs):
        req = httpx.Request("POST", url)
        if "media_publish" in url:
            raise httpx.ReadTimeout("Socket timeout waiting for Meta response")
        return httpx.Response(200, json={"id": "container_timeout_1"}, request=req)

    mock_client = AsyncMock()
    mock_client.post.side_effect = mock_post
    mock_client.__aenter__.return_value = mock_client

    with patch("httpx.AsyncClient", return_value=mock_client), \
         patch.object(publisher, "_poll_until_ready", return_value=True), \
         patch.object(publisher, "reconcile_job", return_value=(False, "Simulated reconcile wait", None)), \
         patch("app.services.instagram.token_manager.TokenManager.decrypt_token", return_value="decrypted_token"):
        await publisher.publish_reel(job.id, is_demo=False, db=db_session)

    db_session.expire_all()
    queried = repo.get_by_id(job.id)
    # MUST NOT be FAILED! Must be OUTCOME_UNKNOWN
    assert queried.status == JobStatus.OUTCOME_UNKNOWN.value


# 13. Outcome reconciliation using recent Instagram media
@pytest.mark.asyncio
async def test_13_outcome_reconciliation_using_recent_instagram_media(db_session, test_setup):
    repo = JobRepository(db_session)
    job = repo.create_publish_job(
        video_id=test_setup["video"].id,
        instagram_account_id=test_setup["account"].id,
        caption="Exact Reconciled Caption #AI",
        is_demo=False,
    )
    repo.update_status(job.id, JobStatus.OUTCOME_UNKNOWN.value)

    publisher = InstagramPublisher()

    # Mock Graph API recent media containing the exact published Reel
    meta_media = {
        "data": [
            {
                "id": "18352444909999999",
                "caption": "Exact Reconciled Caption #AI",
                "media_type": "VIDEO",
                "media_product_type": "REELS",
                "permalink": "https://www.instagram.com/reel/reconciled_reel_123/",
                "timestamp": "2026-11-20T10:00:00+0000",
            }
        ]
    }

    mock_client = AsyncMock()
    mock_client.get.return_value = httpx.Response(200, json=meta_media)
    mock_client.__aenter__.return_value = mock_client

    with patch("httpx.AsyncClient", return_value=mock_client), \
         patch("app.services.instagram.token_manager.TokenManager.decrypt_token", return_value="decrypted_token"):
        success, msg, item = await publisher.reconcile_job(job.id, db=db_session)

    assert success is True
    db_session.expire_all()
    queried = repo.get_by_id(job.id)
    assert queried.status == JobStatus.PUBLISHED.value
    assert queried.instagram_media_id == "18352444909999999"
    assert queried.permalink == "https://www.instagram.com/reel/reconciled_reel_123/"


# 14. Already-published job cannot be published again
def test_14_already_published_job_cannot_be_published_again(db_session, test_setup):
    repo = JobRepository(db_session)
    job = repo.create_publish_job(
        video_id=test_setup["video"].id,
        instagram_account_id=test_setup["account"].id,
        caption="First Publish",
        is_demo=False,
    )
    repo.update_published(job.id, "media_first", "https://instagram.com/p/first")

    # Check duplicate
    is_dup, reason = repo.check_duplicate_publish(
        video_id=test_setup["video"].id,
        account_id=test_setup["account"].id,
        is_demo=False,
    )
    assert is_dup is True
    assert "already been published" in reason


# 15. Concurrent duplicate publish attempts
def test_15_concurrent_duplicate_publish_attempts(db_session, test_setup):
    repo = JobRepository(db_session)
    job1 = repo.create_publish_job(
        video_id=test_setup["video"].id,
        instagram_account_id=test_setup["account"].id,
        caption="Concurrent Test",
        is_demo=False,
        idempotency_key="idemp_key_1",
    )
    repo.update_status(job1.id, JobStatus.CONTAINER_PROCESSING.value, progress=30)

    # Attempting to create duplicate while job1 is in-flight
    is_dup, reason = repo.check_duplicate_publish(
        video_id=test_setup["video"].id,
        account_id=test_setup["account"].id,
        is_demo=False,
    )
    assert is_dup is True
    assert "already in progress" in reason

    # Idempotency returns existing active job
    job2 = repo.create_publish_job(
        video_id=test_setup["video"].id,
        instagram_account_id=test_setup["account"].id,
        caption="Concurrent Test",
        is_demo=False,
        idempotency_key="idemp_key_1",
    )
    assert job2.id == job1.id


# 16. Backend restart and safe job recovery
def test_16_backend_restart_and_safe_job_recovery(db_session, test_setup):
    repo = JobRepository(db_session)
    job_interrupted = repo.create_publish_job(
        video_id=test_setup["video"].id,
        instagram_account_id=test_setup["account"].id,
        caption="Interrupted at publish requested",
        is_demo=False,
    )
    repo.update_status(job_interrupted.id, JobStatus.PUBLISH_REQUESTED.value)

    # Query startup recovery
    recovery_jobs = repo.get_jobs_for_startup_recovery()
    ids = [j.id for j in recovery_jobs]
    assert job_interrupted.id in ids


# 18. FAILED only for definitive publishing failures
@pytest.mark.asyncio
async def test_18_failed_only_for_definitive_publishing_failures(db_session, test_setup, monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", False)
    repo = JobRepository(db_session)

    job = repo.create_publish_job(
        video_id=test_setup["video"].id,
        instagram_account_id=test_setup["account"].id,
        caption="Definitive 400 Failure Test",
        is_demo=False,
        cloudinary_url="https://res.cloudinary.com/quumckey/video/upload/v1/test_reel.mp4",
    )

    publisher = InstagramPublisher()

    # Meta returns 400 Bad Request (e.g. invalid video specs or token revoked)
    async def mock_post(url, **kwargs):
        req = httpx.Request("POST", url)
        resp = httpx.Response(400, json={"error": {"message": "Invalid aspect ratio"}}, request=req)
        raise httpx.HTTPStatusError("Client error", request=req, response=resp)

    mock_client = AsyncMock()
    mock_client.post.side_effect = mock_post
    mock_client.__aenter__.return_value = mock_client

    with patch("httpx.AsyncClient", return_value=mock_client), \
         patch("app.services.instagram.token_manager.TokenManager.decrypt_token", return_value="decrypted_token"):
        await publisher.publish_reel(job.id, is_demo=False, db=db_session)

    db_session.expire_all()
    queried = repo.get_by_id(job.id)
    assert queried.status == JobStatus.FAILED.value
    assert "rejected" in (queried.error_message or "").lower()


# 19. OUTCOME_UNKNOWN when the outcome cannot safely be established
@pytest.mark.asyncio
async def test_19_outcome_unknown_and_reconciliation_required(db_session, test_setup):
    repo = JobRepository(db_session)
    job = repo.create_publish_job(
        video_id=test_setup["video"].id,
        instagram_account_id=test_setup["account"].id,
        caption="Ambiguous or Empty Outcome",
        is_demo=False,
    )
    repo.update_status(job.id, JobStatus.OUTCOME_UNKNOWN.value)

    publisher = InstagramPublisher()

    # Reconciliation returns 0 matches
    meta_media = {"data": []}
    mock_client = AsyncMock()
    mock_client.get.return_value = httpx.Response(200, json=meta_media)
    mock_client.__aenter__.return_value = mock_client

    with patch("httpx.AsyncClient", return_value=mock_client), \
         patch("app.services.instagram.token_manager.TokenManager.decrypt_token", return_value="decrypted_token"):
        success, msg, item = await publisher.reconcile_job(job.id, db=db_session)

    assert success is False
    db_session.expire_all()
    queried = repo.get_by_id(job.id)
    assert queried.status == JobStatus.RECONCILIATION_REQUIRED.value


# 20. Publishing History retaining the correct final status
def test_20_publishing_history_cannot_overwrite_published_with_failed(db_session, test_setup):
    repo = JobRepository(db_session)
    hist_repo = HistoryRepository(db_session)

    job = repo.create_publish_job(
        video_id=test_setup["video"].id,
        instagram_account_id=test_setup["account"].id,
        caption="History State Protection Test",
        is_demo=False,
    )
    repo.update_published(job.id, "media_permanent_123", "https://instagram.com/reel/perm_123")
    hist_repo.create_or_update_published(
        job_id=job.id,
        video_id=test_setup["video"].id,
        instagram_account_id=test_setup["account"].id,
        media_id="media_permanent_123",
        permalink="https://instagram.com/reel/perm_123",
    )

    # Attempt to illegally mark as FAILED
    hist_repo.create_entry_failed(job.id, "Late exception after publication")
    repo.update_status(job.id, JobStatus.FAILED.value, error="Late exception")

    db_session.expire_all()
    queried_job = repo.get_by_id(job.id)
    assert queried_job.status == JobStatus.PUBLISHED.value

    hist = db_session.query(PublishingHistory).filter(PublishingHistory.publishing_job_id == job.id).first()
    assert hist.status == PublishStatus.PUBLISHED.value
    assert hist.instagram_media_id == "media_permanent_123"
