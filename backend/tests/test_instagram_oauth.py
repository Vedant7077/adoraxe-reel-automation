"""
Tests for Meta Unified Login (Facebook Login for Business) OAuth Flow,
System-User Access Tokens (SUAT), and Publishing Graph API Compatibility.
All external network interactions with Meta are mocked.
"""
import pytest
from unittest.mock import patch, MagicMock
from app.services.instagram.oauth import InstagramOAuth
from app.services.instagram.publisher import InstagramPublisher
from app.services.instagram.token_manager import TokenManager
from app.repositories.instagram_repository import InstagramRepository
from app.models.models import InstagramAccount, InstagramAccountStatus
from app.core.config import Settings, settings


def test_unified_login_authorization_url_format():
    """Verify Unified Login authorization URL format on facebook.com with config_id."""
    oauth = InstagramOAuth()
    state = "test_unified_state_12345"
    url = oauth.get_authorization_url(state=state)

    # Must use Facebook Unified Login dialog endpoint
    assert "facebook.com" in url
    assert "/dialog/oauth" in url
    assert f"client_id={settings.effective_app_id}" in url
    assert f"state={state}" in url
    assert "response_type=code" in url
    assert "override_default_response_type=true" in url
    assert "redirect_uri=" in url

    # Must NOT send legacy direct Instagram scopes or parameters
    assert "enable_fb_login" not in url
    assert "scope=instagram_business_basic" not in url
    assert "scope=instagram_business_content_publish" not in url


def test_unified_login_config_id_inclusion():
    """Verify config_id is included when configured and omitted when empty."""
    oauth = InstagramOAuth()
    with patch("app.core.config.settings.meta_config_id", "123456789012345"):
        url = oauth.get_authorization_url(state="state_with_config")
        assert "config_id=123456789012345" in url

    with patch("app.core.config.settings.meta_config_id", ""):
        url = oauth.get_authorization_url(state="state_without_config")
        assert "config_id=" not in url


def test_effective_app_id_and_secret_resolution():
    """Test resolution between META_APP_ID/META_APP_SECRET and legacy INSTAGRAM_* names."""
    s1 = Settings(meta_app_id="1681791737015717", meta_app_secret="sec_meta")
    assert s1.effective_app_id == "1681791737015717"
    assert s1.effective_app_secret == "sec_meta"

    s2 = Settings(instagram_app_id="111", meta_app_id="")
    assert s2.effective_app_id == "111"

    s3 = Settings(instagram_app_secret="sec_ig", meta_app_secret="")
    assert s3.effective_app_secret == "sec_ig"


@pytest.mark.asyncio
async def test_unified_login_token_exchange_and_assigned_account_discovery(db_session):
    """
    Mocked test:
    1. Authorization code exchanged with graph.facebook.com/oauth/access_token
    2. Assigned Instagram Professional account discovered via /me/assigned_instagram_accounts
    3. Non-expiring SUAT token correctly handled (null expires_at)
    """
    oauth = InstagramOAuth()

    with patch("httpx.AsyncClient.get") as mock_get:
        # Mock 1: Token exchange response (SUAT - no expiration)
        mock_token_resp = MagicMock()
        mock_token_resp.status_code = 200
        mock_token_resp.json.return_value = {
            "access_token": "EAAX_MOCK_SYSTEM_USER_ACCESS_TOKEN",
            "token_type": "bearer",
        }

        # Mock 2: /me/assigned_instagram_accounts response
        mock_assigned_resp = MagicMock()
        mock_assigned_resp.status_code = 200
        mock_assigned_resp.json.return_value = {
            "data": [
                {
                    "id": "17841405309211844",
                    "username": "youtube_insta_automation",
                    "name": "Adoraxe Automation Creator",
                    "profile_picture_url": "https://example.com/pic.jpg",
                    "followers_count": 250,
                }
            ]
        }

        mock_get.side_effect = [mock_token_resp, mock_assigned_resp]

        result = await oauth.exchange_code_for_token("MOCK_AUTH_CODE_RAW#_")

        assert result["access_token"] == "EAAX_MOCK_SYSTEM_USER_ACCESS_TOKEN"
        assert result["user_id"] == "17841405309211844"
        assert result["user_data"]["username"] == "youtube_insta_automation"
        assert result["expires_in"] is None

        # Verify database persistence with null expiration (SUAT non-expiring)
        repo = InstagramRepository(db_session)
        account = repo.upsert_account(result)

        assert account.instagram_user_id == "17841405309211844"
        assert account.username == "youtube_insta_automation"
        assert account.status == InstagramAccountStatus.CONNECTED
        assert account.token_expires_at is None
        assert account.encrypted_access_token is not None

        # Verify token decryption
        token_mgr = TokenManager()
        decrypted = token_mgr.decrypt_token(account.encrypted_access_token)
        assert decrypted == "EAAX_MOCK_SYSTEM_USER_ACCESS_TOKEN"


@pytest.mark.asyncio
async def test_account_discovery_fallback_to_page_accounts():
    """Verify fallback to /me/accounts when /me/assigned_instagram_accounts is empty."""
    oauth = InstagramOAuth()

    with patch("httpx.AsyncClient.get") as mock_get:
        # Mock 1: Token exchange response
        mock_token_resp = MagicMock()
        mock_token_resp.status_code = 200
        mock_token_resp.json.return_value = {
            "access_token": "EAAX_MOCK_TOKEN",
        }

        # Mock 2: /me/assigned_instagram_accounts is empty []
        mock_assigned_empty = MagicMock()
        mock_assigned_empty.status_code = 200
        mock_assigned_empty.json.return_value = {"data": []}

        # Mock 3: /me/accounts has linked instagram_business_account
        mock_pages_resp = MagicMock()
        mock_pages_resp.status_code = 200
        mock_pages_resp.json.return_value = {
            "data": [
                {
                    "id": "100012345",
                    "name": "Creator Page",
                    "instagram_business_account": {
                        "id": "17841499999999999",
                        "username": "fallback_creator",
                        "name": "Fallback Creator",
                        "profile_picture_url": "https://example.com/fb_pic.jpg",
                        "followers_count": 1200,
                    },
                }
            ]
        }

        mock_get.side_effect = [mock_token_resp, mock_assigned_empty, mock_pages_resp]

        result = await oauth.exchange_code_for_token("CODE_FALLBACK")
        assert result["user_id"] == "17841499999999999"
        assert result["user_data"]["username"] == "fallback_creator"


@pytest.mark.asyncio
async def test_account_discovery_empty_raises_descriptive_error():
    """Verify descriptive error raised when no Instagram professional account is discovered."""
    oauth = InstagramOAuth()

    with patch("httpx.AsyncClient.get") as mock_get:
        mock_token_resp = MagicMock()
        mock_token_resp.status_code = 200
        mock_token_resp.json.return_value = {"access_token": "EAAX_TOKEN"}

        mock_empty = MagicMock()
        mock_empty.status_code = 200
        mock_empty.json.return_value = {"data": []}

        mock_me_empty = MagicMock()
        mock_me_empty.status_code = 200
        mock_me_empty.json.return_value = {"id": "123"}

        mock_get.side_effect = [mock_token_resp, mock_empty, mock_empty, mock_me_empty]

        with pytest.raises(ValueError) as excinfo:
            await oauth.exchange_code_for_token("CODE_NO_ASSET")

        assert "No assigned Instagram Professional account found" in str(excinfo.value)
        assert "Meta Business Settings" in str(excinfo.value)


def test_token_encryption_and_decryption_security():
    """Verify Fernet encryption preserves confidentiality of system-user access tokens."""
    token_mgr = TokenManager()
    plain_token = "EAAX_TEST_SYSTEM_USER_ACCESS_TOKEN_ABC123"

    encrypted = token_mgr.encrypt_token(plain_token)
    assert encrypted != plain_token
    assert "EAAX" not in encrypted

    decrypted = token_mgr.decrypt_token(encrypted)
    assert decrypted == plain_token


def test_publishing_service_uses_facebook_graph_api():
    """Verify InstagramPublisher uses graph.facebook.com for Meta Unified Login compatibility."""
    publisher = InstagramPublisher()
    assert publisher.GRAPH_API_BASE == "https://graph.facebook.com"


@pytest.mark.asyncio
async def test_publishing_demo_mode_simulation_preserved(db_session):
    """Verify DEMO_MODE=true runs simulation and prevents live Meta Graph API calls."""
    publisher = InstagramPublisher()
    from app.repositories.job_repository import JobRepository

    job_repo = JobRepository(db_session)
    job = job_repo.create_publish_job(video_id="video-test-1", instagram_account_id="acc-1", is_demo=True, caption="Test Reel")
    job.rights_confirmed = True
    job.cloudinary_url = "https://res.cloudinary.com/test/reel.mp4"
    db_session.commit()

    with patch.object(publisher, "_run_simulated_publish") as mock_sim, \
         patch("httpx.AsyncClient.post") as mock_http_post:

        await publisher.publish_reel(job_id=job.id, is_demo=True, db=db_session)

        # Simulation must be invoked
        mock_sim.assert_called_once()
        # Live Graph API HTTP POST must NOT be called
        mock_http_post.assert_not_called()


@pytest.mark.asyncio
async def test_real_account_status_rendering_and_metadata(db_session):
    """Verify real account status returns verified token health, non-hardcoded username, and permissions."""
    from app.api.routes.instagram import get_instagram_status
    from app.models.models import InstagramAccountStatus
    from datetime import datetime, timezone

    token_mgr = TokenManager()
    enc_token = token_mgr.encrypt_token("EAAX_VALID_SUAT_TOKEN_XYZ")

    # Add real connected account to DB
    acc = InstagramAccount(
        instagram_user_id="17841454191511409",
        username="dynamic_source_username",
        full_name="Source Creator Name",
        account_type="CREATOR",
        encrypted_access_token=enc_token,
        token_expires_at=None,
        status=InstagramAccountStatus.CONNECTED,
        connected_at=datetime.now(timezone.utc),
    )
    db_session.add(acc)
    db_session.commit()

    with patch("httpx.AsyncClient.get") as mock_get:
        # Mock 1: /debug_token
        mock_debug = MagicMock()
        mock_debug.status_code = 200
        mock_debug.json.return_value = {
            "data": {
                "app_id": "1681791737015717",
                "type": "SYSTEM_USER",
                "is_valid": True,
                "expires_at": 0,
                "data_access_expires_at": 0,
                "scopes": ["instagram_basic", "instagram_content_publish", "instagram_manage_messages", "public_profile"],
            }
        }

        # Mock 2: /{ig_user_id} profile query
        mock_prof = MagicMock()
        mock_prof.status_code = 200
        mock_prof.json.return_value = {
            "id": "17841454191511409",
            "username": "dynamic_source_username",
            "name": "Source Creator Name",
            "account_type": "CREATOR",
        }

        mock_get.side_effect = [mock_debug, mock_prof]

        resp = await get_instagram_status(force_refresh=True, db=db_session)

        # Must not be placeholder
        assert resp.is_demo_placeholder is False
        assert resp.status == InstagramAccountStatus.CONNECTED
        assert resp.username == "dynamic_source_username"
        assert resp.instagram_user_id == "17841454191511409"
        assert resp.meta_app_id == "1681791737015717"
        assert resp.token_validity == "VALID"
        assert resp.token_type == "SYSTEM_USER"
        assert resp.expires_at_raw == 0
        assert resp.token_status_message == "No expiry reported by Meta"
        assert "instagram_basic" in resp.granted_scopes
        assert "instagram_content_publish" in resp.granted_scopes
        assert "instagram_manage_messages" in resp.additional_scopes
        assert len(resp.missing_scopes) == 0


@pytest.mark.asyncio
async def test_token_invalid_state_and_missing_scopes(db_session):
    """Verify invalid token status and missing scope identification."""
    from app.api.routes.instagram import get_instagram_status
    from app.models.models import InstagramAccountStatus
    from datetime import datetime, timezone

    token_mgr = TokenManager()
    enc_token = token_mgr.encrypt_token("EAAX_INVALID_TOKEN")

    acc = InstagramAccount(
        instagram_user_id="17841454191511409",
        username="test_user_inv",
        full_name="Invalid Test User",
        encrypted_access_token=enc_token,
        status=InstagramAccountStatus.CONNECTED,
        connected_at=datetime.now(timezone.utc),
    )
    db_session.add(acc)
    db_session.commit()

    with patch("httpx.AsyncClient.get") as mock_get:
        mock_debug = MagicMock()
        mock_debug.status_code = 200
        mock_debug.json.return_value = {
            "data": {
                "app_id": "1681791737015717",
                "type": "USER",
                "is_valid": False,
                "expires_at": 1700000000,
                "scopes": ["public_profile"],  # Missing instagram_basic & instagram_content_publish
            }
        }
        mock_prof = MagicMock()
        mock_prof.status_code = 400
        mock_prof.json.return_value = {"error": "Invalid OAuth token"}

        mock_get.side_effect = [mock_debug, mock_prof]

        resp = await get_instagram_status(force_refresh=True, db=db_session)

        assert resp.token_validity == "INVALID"
        assert "instagram_basic" in resp.missing_scopes
        assert "instagram_content_publish" in resp.missing_scopes


@pytest.mark.asyncio
async def test_verification_network_failure_falls_back_to_unknown(db_session):
    """Verify network error during verification marks status UNKNOWN without crashing."""
    from app.api.routes.instagram import get_instagram_status
    from app.models.models import InstagramAccountStatus
    from datetime import datetime, timezone

    token_mgr = TokenManager()
    enc_token = token_mgr.encrypt_token("EAAX_NET_ERR_TOKEN")

    acc = InstagramAccount(
        instagram_user_id="17841454191511409",
        username="test_net_err",
        encrypted_access_token=enc_token,
        status=InstagramAccountStatus.CONNECTED,
        connected_at=datetime.now(timezone.utc),
    )
    db_session.add(acc)
    db_session.commit()

    with patch("httpx.AsyncClient.get", side_effect=Exception("Connection timeout to Meta")):
        resp = await get_instagram_status(force_refresh=True, db=db_session)

        assert resp.token_validity == "UNKNOWN"
        assert "failed" in (resp.token_status_message or "").lower()


@pytest.mark.asyncio
async def test_demo_placeholder_never_shown_as_connected_real_account(db_session):
    """Verify that when no real account exists, placeholder is returned with NOT_CONNECTED and is_demo_placeholder=True."""
    from app.api.routes.instagram import get_instagram_status
    from app.models.models import InstagramAccountStatus

    # Ensure no real account exists in DB session
    resp = await get_instagram_status(db=db_session)

    assert resp.status == InstagramAccountStatus.NOT_CONNECTED
    assert resp.is_demo_placeholder is True
    assert resp.username == "adoraxe_demo"
    assert resp.token_validity is None


@pytest.mark.asyncio
async def test_refresh_endpoint_invokes_force_refresh(db_session):
    """Verify POST /refresh triggers live verification."""
    from app.api.routes.instagram import refresh_instagram_status
    from app.models.models import InstagramAccountStatus
    from datetime import datetime, timezone

    token_mgr = TokenManager()
    acc = InstagramAccount(
        instagram_user_id="17841454191511409",
        username="refreshed_user",
        encrypted_access_token=token_mgr.encrypt_token("TOKEN_1"),
        status=InstagramAccountStatus.CONNECTED,
        connected_at=datetime.now(timezone.utc),
    )
    db_session.add(acc)
    db_session.commit()

    with patch("app.services.instagram.oauth.InstagramOAuth.verify_token_and_fetch_profile") as mock_v:
        mock_v.return_value = {
            "token_validity": "VALID",
            "token_type": "SYSTEM_USER",
            "expires_at_raw": 0,
            "data_access_expires_at": 0,
            "token_status_message": "No expiry reported by Meta",
            "granted_scopes": ["instagram_basic", "instagram_content_publish"],
            "required_scopes": ["instagram_basic", "instagram_content_publish"],
            "additional_scopes": [],
            "missing_scopes": [],
            "last_verified_at": datetime.now(timezone.utc),
            "profile": None,
        }

        resp = await refresh_instagram_status(db=db_session)
        assert resp.token_validity == "VALID"
        mock_v.assert_called_once()
        # Verify force_refresh=True was passed
        _, kwargs = mock_v.call_args
        assert kwargs.get("force_refresh") is True


@pytest.mark.asyncio
async def test_disconnect_preserves_publishing_history(db_session):
    """Verify disconnect_instagram marks account NOT_CONNECTED and preserves publishing history intact."""
    from app.api.routes.instagram import disconnect_instagram
    from app.models.models import InstagramAccountStatus, PublishingHistory, Video, PublishStatus, PublishingJob
    from datetime import datetime, timezone

    token_mgr = TokenManager()
    acc = InstagramAccount(
        instagram_user_id="17841454191511409",
        username="user_to_disconnect",
        encrypted_access_token=token_mgr.encrypt_token("TOKEN_DISC"),
        status=InstagramAccountStatus.CONNECTED,
        connected_at=datetime.now(timezone.utc),
    )
    db_session.add(acc)
    db_session.commit()

    # Create dummy video, job, and history
    vid = Video(title="Test Hist Video", youtube_video_id="yt-hist-1", video_url="https://youtube.com/watch?v=yt-hist-1")
    db_session.add(vid)
    db_session.commit()

    job = PublishingJob(video_id=vid.id, instagram_account_id=acc.id, is_demo=False)
    db_session.add(job)
    db_session.commit()

    hist = PublishingHistory(
        publishing_job_id=job.id,
        video_id=vid.id,
        instagram_account_id=acc.id,
        instagram_media_id="18000000000",
        video_title="Test Hist Video",
        status=PublishStatus.PUBLISHED,
        published_at=datetime.now(timezone.utc),
    )
    db_session.add(hist)
    db_session.commit()

    # Execute disconnect
    res = await disconnect_instagram(db=db_session)
    assert res["message"] == "Instagram account disconnected successfully"

    # Verify account state
    db_session.refresh(acc)
    assert acc.status == InstagramAccountStatus.NOT_CONNECTED
    assert acc.encrypted_access_token is None

    # Verify publishing history is NOT deleted
    remaining_hist = db_session.query(PublishingHistory).filter(PublishingHistory.id == hist.id).first()
    assert remaining_hist is not None
    assert remaining_hist.instagram_media_id == "18000000000"
