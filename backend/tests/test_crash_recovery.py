"""
Crash Recovery and State-Machine Invariant Tests.
Tests simulation of process crashes, restarts, concurrent runners,
and recovery across every stage of the publishing pipeline.
"""
import pytest
import asyncio
from datetime import datetime, timedelta
from unittest.mock import patch, AsyncMock, MagicMock
from sqlalchemy.orm import Session
import httpx

from app.models.models import (
    Video, VideoAsset, VideoStatus,
    InstagramAccount, InstagramAccountStatus,
    PublishingJob, PublishingHistory,
    JobStatus, PublishStatus
)
from app.repositories.job_repository import JobRepository
from app.repositories.history_repository import HistoryRepository
from app.repositories.instagram_repository import InstagramRepository
from app.services.instagram.publisher import InstagramPublisher
from app.services.instagram.token_manager import TokenManager


@pytest.fixture
def recovery_env(db_session: Session):
    """Setup video, asset, and connected Instagram account."""
    video = Video(
        title="Crash Recovery Test Video #AI",
        youtube_video_id="yt-crash-recovery-99",
        status=VideoStatus.PROCESSED,
        video_url="https://youtube.com/watch?v=yt-crash-recovery-99",
        duration_seconds=45,
    )
    db_session.add(video)
    db_session.commit()
    db_session.refresh(video)

    asset = VideoAsset(
        video_id=video.id,
        cloudinary_url="https://res.cloudinary.com/demo/video/upload/v1234/reel_crash.mp4",
        cloudinary_public_id="reels/crash_test",
        is_processed=True,
        is_reel_compatible=True,
        duration_seconds=45,
        width=1080,
        height=1920,
    )
    db_session.add(asset)
    db_session.commit()

    token_mgr = TokenManager()
    encrypted_token = token_mgr.encrypt_token("EAAG_MOCK_VALID_SUAT_TOKEN_CRASH_TEST")

    account = InstagramAccount(
        instagram_user_id="17841454191511409",
        username="yotube_insta_automation",
        full_name="Crash Recovery Creator",
        encrypted_access_token=encrypted_token,
        status=InstagramAccountStatus.CONNECTED,
    )
    db_session.add(account)
    db_session.commit()
    db_session.refresh(account)

    return {"video": video, "asset": asset, "account": account}


# ─── 1. Interruption while QUEUED ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_recovery_interrupted_while_queued(db_session: Session, recovery_env):
    """When server restarts while job is QUEUED, startup recovery picks it up and executes it."""
    job_repo = JobRepository(db_session)
    job = job_repo.create_publish_job(
        video_id=recovery_env["video"].id,
        instagram_account_id=recovery_env["account"].id,
        caption="Queued crash test",
        is_demo=False,
        cloudinary_url=recovery_env["asset"].cloudinary_url,
    )
    assert job.status == JobStatus.QUEUED.value

    # Simulate server crash and restart: recovery scanner finds it
    pending = job_repo.get_jobs_for_startup_recovery()
    assert any(j.id == job.id for j in pending)

    # Mock container creation and publish
    req = httpx.Request("POST", "https://graph.facebook.com/v26.0/media")
    resp_create = httpx.Response(200, json={"id": "container-queued-resumed"}, request=req)
    resp_poll = httpx.Response(200, json={"status_code": "FINISHED"}, request=req)
    resp_pub = httpx.Response(200, json={"id": "media-queued-success"}, request=req)
    resp_link = httpx.Response(200, json={"permalink": "https://www.instagram.com/reel/queued-ok/"}, request=req)

    publisher = InstagramPublisher()
    with patch("httpx.AsyncClient.post", side_effect=[resp_create, resp_pub]):
        with patch("httpx.AsyncClient.get", side_effect=[resp_poll, resp_link]):
            with patch("asyncio.sleep", return_value=None):
                await publisher.publish_reel(job.id, is_demo=False, db=db_session, is_recovery=True)

    db_session.expire_all()
    updated = job_repo.get_by_id(job.id)
    assert updated.status == JobStatus.PUBLISHED.value
    assert updated.instagram_media_id == "media-queued-success"
    assert updated.permalink == "https://www.instagram.com/reel/queued-ok/"


# ─── 2. Interruption during CONTAINER_CREATING ────────────────────────────────

@pytest.mark.asyncio
async def test_recovery_interrupted_during_container_creating(db_session: Session, recovery_env):
    """When server restarts during CONTAINER_CREATING, recovery scanner finds and resumes it."""
    job_repo = JobRepository(db_session)
    job = job_repo.create_publish_job(
        video_id=recovery_env["video"].id,
        instagram_account_id=recovery_env["account"].id,
        caption="Container creating crash test",
        is_demo=False,
        cloudinary_url=recovery_env["asset"].cloudinary_url,
    )
    job_repo.update_status(job.id, JobStatus.CONTAINER_CREATING.value, current_stage="CONTAINER_CREATING")

    # Scanner identifies it
    pending = job_repo.get_jobs_for_startup_recovery()
    assert any(j.id == job.id for j in pending)

    req = httpx.Request("POST", "https://graph.facebook.com/v26.0/media")
    resp_create = httpx.Response(200, json={"id": "container-newly-created"}, request=req)
    resp_poll = httpx.Response(200, json={"status_code": "FINISHED"}, request=req)
    resp_pub = httpx.Response(200, json={"id": "media-creating-success"}, request=req)
    resp_link = httpx.Response(200, json={"permalink": "https://www.instagram.com/reel/creating-ok/"}, request=req)

    publisher = InstagramPublisher()
    with patch("httpx.AsyncClient.post", side_effect=[resp_create, resp_pub]):
        with patch("httpx.AsyncClient.get", side_effect=[resp_poll, resp_link]):
            with patch("asyncio.sleep", return_value=None):
                await publisher.publish_reel(job.id, is_demo=False, db=db_session, is_recovery=True)

    db_session.expire_all()
    updated = job_repo.get_by_id(job.id)
    assert updated.status == JobStatus.PUBLISHED.value
    assert updated.instagram_media_id == "media-creating-success"


# ─── 3. Interruption during CONTAINER_PROCESSING ──────────────────────────────

@pytest.mark.asyncio
async def test_recovery_interrupted_during_container_processing(db_session: Session, recovery_env):
    """When restarted during CONTAINER_PROCESSING, runner reuses existing container_id without recreating."""
    job_repo = JobRepository(db_session)
    job = job_repo.create_publish_job(
        video_id=recovery_env["video"].id,
        instagram_account_id=recovery_env["account"].id,
        caption="Container processing crash test",
        is_demo=False,
        cloudinary_url=recovery_env["asset"].cloudinary_url,
    )
    job_repo.update_status(job.id, JobStatus.CONTAINER_PROCESSING.value, current_stage="CONTAINER_PROCESSING")
    job_repo.update_container_id(job.id, "container-existing-12345")

    req = httpx.Request("POST", "https://graph.facebook.com/v26.0/media")
    resp_poll = httpx.Response(200, json={"status_code": "FINISHED"}, request=req)
    resp_pub = httpx.Response(200, json={"id": "media-proc-resumed"}, request=req)
    resp_link = httpx.Response(200, json={"permalink": "https://www.instagram.com/reel/proc-ok/"}, request=req)

    mock_post = AsyncMock(side_effect=[resp_pub])
    mock_get = AsyncMock(side_effect=[resp_poll, resp_link])

    publisher = InstagramPublisher()
    with patch("httpx.AsyncClient.post", mock_post):
        with patch("httpx.AsyncClient.get", mock_get):
            with patch("asyncio.sleep", return_value=None):
                await publisher.publish_reel(job.id, is_demo=False, db=db_session)

    # CRITICAL: Verify POST to create container was NEVER called because container_id was reused!
    # The only POST was to media_publish
    assert mock_post.call_count == 1
    call_url = str(mock_post.call_args_list[0][0][0])
    assert "media_publish" in call_url

    db_session.expire_all()
    updated = job_repo.get_by_id(job.id)
    assert updated.status == JobStatus.PUBLISHED.value
    assert updated.instagram_media_id == "media-proc-resumed"


# ─── 4. Interruption after PUBLISH_REQUESTED (Safe Reconciliation) ────────────

@pytest.mark.asyncio
async def test_recovery_interrupted_after_publish_requested_triggers_reconciliation(db_session: Session, recovery_env):
    """
    When server crashed after media_publish was dispatched (PUBLISH_REQUESTED),
    recovery scanner flags it for read-only reconciliation. NEVER blind retry!
    """
    job_repo = JobRepository(db_session)
    job = job_repo.create_publish_job(
        video_id=recovery_env["video"].id,
        instagram_account_id=recovery_env["account"].id,
        caption="Publish requested crash test",
        is_demo=False,
        cloudinary_url=recovery_env["asset"].cloudinary_url,
    )
    job_repo.update_status(job.id, JobStatus.PUBLISH_REQUESTED.value, current_stage="PUBLISH_REQUESTED")

    # Scanner identifies it
    pending = job_repo.get_jobs_for_startup_recovery()
    match = next((j for j in pending if j.id == job.id), None)
    assert match is not None
    assert match.status == JobStatus.PUBLISH_REQUESTED.value

    # Simulate read-only reconciliation: query Instagram media
    req = httpx.Request("GET", "https://graph.facebook.com/v26.0/me/media")
    mock_recent_media = httpx.Response(200, json={
        "data": [
            {
                "id": "18352999888777",
                "caption": "Publish requested crash test",
                "media_type": "VIDEO",
                "permalink": "https://www.instagram.com/reel/reconciled-ok/",
                "timestamp": "2026-11-20T16:00:00+0000",
            }
        ]
    }, request=req)

    publisher = InstagramPublisher()
    mock_post = AsyncMock()

    with patch("httpx.AsyncClient.get", return_value=mock_recent_media):
        with patch("httpx.AsyncClient.post", mock_post):
            success, msg, _ = await publisher.reconcile_job(job.id, db=db_session)

    # CRITICAL INVARIANT: Reconcile NEVER calls POST media_publish
    assert mock_post.call_count == 0
    assert success is True

    db_session.expire_all()
    updated = job_repo.get_by_id(job.id)
    assert updated.status == JobStatus.PUBLISHED.value
    assert updated.instagram_media_id == "18352999888777"
    assert updated.permalink == "https://www.instagram.com/reel/reconciled-ok/"


# ─── 5. Meta Published but Process Died Before DB Commit ──────────────────────

@pytest.mark.asyncio
async def test_interruption_after_meta_publishes_recovers_live_media_id(db_session: Session, recovery_env):
    """
    If Meta published the Reel but backend crashed before saving PUBLISHED status,
    read-only reconciliation recovers the live media ID and permalink.
    """
    job_repo = JobRepository(db_session)
    job = job_repo.create_publish_job(
        video_id=recovery_env["video"].id,
        instagram_account_id=recovery_env["account"].id,
        caption="Crash before DB commit test",
        is_demo=False,
        cloudinary_url=recovery_env["asset"].cloudinary_url,
    )
    job_repo.update_status(job.id, JobStatus.OUTCOME_UNKNOWN.value, current_stage="OUTCOME_UNKNOWN")

    req = httpx.Request("GET", "https://graph.facebook.com/v26.0/me/media")
    mock_media_resp = httpx.Response(200, json={
        "data": [
            {
                "id": "18999999999999",
                "caption": "Crash before DB commit test",
                "media_type": "VIDEO",
                "permalink": "https://www.instagram.com/reel/recovered-commit/",
                "timestamp": "2026-11-20T16:30:00+0000",
            }
        ]
    }, request=req)

    publisher = InstagramPublisher()
    with patch("httpx.AsyncClient.get", return_value=mock_media_resp):
        success, msg, _ = await publisher.reconcile_job(job.id, db=db_session)

    assert success is True
    db_session.expire_all()
    updated = job_repo.get_by_id(job.id)
    assert updated.status == JobStatus.PUBLISHED.value
    assert updated.instagram_media_id == "18999999999999"
    assert updated.permalink == "https://www.instagram.com/reel/recovered-commit/"

    # History record is also reconciled to PUBLISHED
    hist = db_session.query(PublishingHistory).filter(PublishingHistory.publishing_job_id == job.id).first()
    assert hist is not None
    assert hist.status == PublishStatus.PUBLISHED.value
    assert hist.instagram_media_id == "18999999999999"


# ─── 6. Concurrent Workers Cannot Claim Same Job ──────────────────────────────

@pytest.mark.asyncio
async def test_concurrent_runners_cannot_claim_same_job(db_session: Session, recovery_env):
    """Two concurrent workers trying to execute the same job are serialized; active claims block second worker."""
    job_repo = JobRepository(db_session)
    job = job_repo.create_publish_job(
        video_id=recovery_env["video"].id,
        instagram_account_id=recovery_env["account"].id,
        caption="Concurrency test",
        is_demo=False,
        cloudinary_url=recovery_env["asset"].cloudinary_url,
    )

    # 1. First worker claims it -> transaction commits -> job is now PROCESSING with active lease
    claim1 = job_repo.claim_job_for_execution(job.id)
    assert claim1 is not None
    assert claim1.attempt_count == 2  # created at 1, incremented to 2
    assert claim1.started_at is not None

    # 2. Second worker attempts to claim while worker 1's claim is active (after commit) -> blocked!
    claim2 = job_repo.claim_job_for_execution(job.id)
    assert claim2 is None, "Second worker must NOT be able to claim an actively claimed job"

    # 3. Startup recovery with force_recovery=True CAN reclaim the job after process crash
    claim_recovery = job_repo.claim_job_for_execution(job.id, force_recovery=True)
    assert claim_recovery is not None
    assert claim_recovery.attempt_count == 3

    # 4. Expired lease (> 5 minutes ago) also allows reclamation to prevent permanent lockup
    stale_job = job_repo.get_by_id(job.id)
    stale_job.updated_at = datetime.utcnow() - timedelta(minutes=6)
    stale_job.started_at = datetime.utcnow() - timedelta(minutes=6)
    db_session.commit()

    claim_stale = job_repo.claim_job_for_execution(job.id)
    assert claim_stale is not None, "Expired lease must allow reclamation to prevent hung jobs"

    # 5. Terminal states cannot be claimed under any circumstances
    job_repo.update_status(job.id, JobStatus.PUBLISHED.value)
    claim_terminal = job_repo.claim_job_for_execution(job.id, force_recovery=True)
    assert claim_terminal is None


# ─── 7. Monotonic History Protection Against Regression ───────────────────────

def test_published_history_never_regresses_to_failed(db_session: Session, recovery_env):
    """Verify that a PUBLISHED history record can NEVER be overwritten with FAILED."""
    job_repo = JobRepository(db_session)
    job = job_repo.create_publish_job(
        video_id=recovery_env["video"].id,
        instagram_account_id=recovery_env["account"].id,
        caption="Monotonic history test",
        is_demo=False,
        cloudinary_url=recovery_env["asset"].cloudinary_url,
    )

    hist_repo = HistoryRepository(db_session)
    hist = hist_repo.create_or_update_published(
        job_id=job.id,
        video_id=recovery_env["video"].id,
        instagram_account_id=recovery_env["account"].id,
        media_id="18444444444444",
        permalink="https://www.instagram.com/reel/monotonic/",
    )
    assert hist.status == PublishStatus.PUBLISHED.value

    # Attempt to overwrite with FAILED
    failed_entry = hist_repo.create_entry_failed(job.id, "Simulated network timeout callback")
    assert failed_entry.status == PublishStatus.PUBLISHED.value
    assert failed_entry.instagram_media_id == "18444444444444"
