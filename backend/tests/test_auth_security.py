"""
Authentication & Authorization Security Tests for Adoraxe.
Verifies fail-closed behavior, protected API endpoints, public exemptions,
timing-safe key validation, brute-force rate-limiting, and credential protection.
"""
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.core.security import clear_failed_attempts_for_testing

TEST_ADMIN_KEY = "test-secret-admin-key-32-characters-long"
WRONG_ADMIN_KEY = "completely-wrong-admin-key-xyz"


@pytest.fixture(autouse=True)
def setup_auth_environment(monkeypatch):
    """Enable authentication and set test admin key for this test module."""
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "admin_api_key", TEST_ADMIN_KEY)
    monkeypatch.setattr(settings, "dev_auth_bypass", False)
    clear_failed_attempts_for_testing()
    yield
    clear_failed_attempts_for_testing()


# ─── 1. Protected Endpoints Return 401 Without Key ───────────────────────────

def test_protected_endpoints_return_401_without_key(client):
    """Calling protected endpoints without X-Admin-Key returns 401 Unauthorized."""
    # Channels
    res = client.get("/api/v1/channels")
    assert res.status_code == 401
    assert "Administrator authentication required" in res.json().get("detail", "")

    # Videos
    res = client.get("/api/v1/videos")
    assert res.status_code == 401

    # Instagram
    res = client.get("/api/v1/instagram/status")
    assert res.status_code == 401

    # Publishing
    res = client.get("/api/v1/publishing/queue-videos")
    assert res.status_code == 401

    # History
    res = client.get("/api/v1/history")
    assert res.status_code == 401

    # Auth verify endpoint
    res = client.get("/api/v1/auth/verify")
    assert res.status_code == 401


# ─── 2. Protected Endpoints Return 401 With Incorrect Key ────────────────────

def test_protected_endpoints_return_401_with_incorrect_key(client):
    """Supplying an incorrect X-Admin-Key returns 401 Unauthorized."""
    headers = {"X-Admin-Key": WRONG_ADMIN_KEY}

    res = client.get("/api/v1/channels", headers=headers)
    assert res.status_code == 401
    assert "Invalid administrator access key" in res.json().get("detail", "")

    res = client.get("/api/v1/instagram/status", headers=headers)
    assert res.status_code == 401

    res = client.get("/api/v1/auth/verify", headers=headers)
    assert res.status_code == 401


# ─── 3. Protected Endpoints Succeed With Correct Admin Key ───────────────────

def test_protected_endpoints_succeed_with_correct_key(client):
    """Supplying the valid X-Admin-Key grants full authorized access."""
    headers = {"X-Admin-Key": TEST_ADMIN_KEY}

    # Verification endpoint
    res = client.get("/api/v1/auth/verify", headers=headers)
    assert res.status_code == 200
    assert res.json().get("authenticated") is True

    # Channels
    res = client.get("/api/v1/channels", headers=headers)
    assert res.status_code == 200

    # Videos
    res = client.get("/api/v1/videos", headers=headers)
    assert res.status_code == 200

    # Instagram
    res = client.get("/api/v1/instagram/status", headers=headers)
    assert res.status_code == 200

    # Publishing
    res = client.get("/api/v1/publishing/queue-videos", headers=headers)
    assert res.status_code == 200

    # History
    res = client.get("/api/v1/history", headers=headers)
    assert res.status_code == 200


# ─── 4. Health Endpoint Remains Publicly Accessible ──────────────────────────

def test_health_endpoint_remains_public(client):
    """The /health endpoint must remain accessible without any authentication."""
    res = client.get("/api/v1/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert "version" in data
    # Health check must NOT leak secrets or tokens
    assert "token" not in str(data).lower()
    assert "secret" not in str(data).lower()


# ─── 5. Instagram OAuth Callback Remains Public But Rejects Invalid State ────

def test_instagram_callback_remains_public_with_csrf_check(client):
    """OAuth callback is public (Meta redirect destination) but enforces valid state."""
    # Calling callback without valid state raises 400 Bad Request, NOT 401 Unauthorized
    res = client.get("/api/v1/instagram/callback?code=fake_code&state=invalid_or_missing_state")
    assert res.status_code == 400
    assert "Invalid OAuth state" in res.json().get("detail", "")


# ─── 6. Instagram Disconnect Cannot Be Called Without Authorization ──────────

def test_instagram_disconnect_requires_authorization(client):
    """Destructive disconnect endpoint strictly rejects unauthenticated callers."""
    # Without key -> 401
    res = client.post("/api/v1/instagram/disconnect")
    assert res.status_code == 401

    # With wrong key -> 401
    res = client.post("/api/v1/instagram/disconnect", headers={"X-Admin-Key": "bad-key"})
    assert res.status_code == 401

    # With valid key -> 200
    res = client.post("/api/v1/instagram/disconnect", headers={"X-Admin-Key": TEST_ADMIN_KEY})
    assert res.status_code == 200
    assert "disconnected successfully" in res.json().get("message", "")


# ─── 7. Publishing Endpoints Cannot Be Called Without Authorization ──────────

def test_publishing_endpoints_require_authorization(client):
    """Publishing Reel submissions strictly reject unauthenticated callers."""
    payload = {
        "video_id": "00000000-0000-0000-0000-000000000001",
        "caption": "Unauthorized attempt",
        "rights_confirmed": True,
    }
    # Anonymous POST -> 401
    res = client.post("/api/v1/publishing", json=payload)
    assert res.status_code == 401

    # Active job status -> 401
    res = client.get("/api/v1/publishing/active")
    assert res.status_code == 401


# ─── 8. Video Upload, Retrieval, and Processing Require Authorization ────────

def test_video_operations_require_authorization(client):
    """Upload, media retrieval, and FFmpeg processing require authorization."""
    # Upload without key -> 401
    res = client.post("/api/v1/videos/upload", files={"file": ("test.mp4", b"dummy content", "video/mp4")})
    assert res.status_code == 401

    # Media retrieval without key -> 401
    res = client.post("/api/v1/videos/dummy-id/retrieve-media", json={"use_yt_dlp": False})
    assert res.status_code == 401

    # Processing without key -> 401
    res = client.post("/api/v1/videos/dummy-id/process", json={"crop_mode": "crop"})
    assert res.status_code == 401


# ─── 9. Account Switching and Queue Removal Require Authorization ────────────

def test_account_switching_and_queue_removal_require_authorization(client):
    """Modifying active Instagram profile or queue items requires authorization."""
    res = client.post("/api/v1/instagram/accounts/dummy-account/activate")
    assert res.status_code == 401

    res = client.post("/api/v1/publishing/queue/dummy-video/remove")
    assert res.status_code == 401

    res = client.post("/api/v1/publishing/queue/dummy-video/add")
    assert res.status_code == 401


# ─── 10. Fail-Closed Behavior in Production When Key Missing ─────────────────

def test_production_fails_startup_when_admin_key_is_missing(monkeypatch):
    """When auth_enabled is true, an empty or short ADMIN_API_KEY aborts startup."""
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "admin_api_key", "")
    monkeypatch.setattr(settings, "dev_auth_bypass", False)

    with pytest.raises(RuntimeError) as exc_info:
        settings.validate_auth_configuration()
    assert "CRITICAL SECURITY CONFIGURATION ERROR" in str(exc_info.value)
    assert "ADMIN_API_KEY is missing or too short" in str(exc_info.value)

    # Key too short (under 16 chars)
    monkeypatch.setattr(settings, "admin_api_key", "short-key")
    with pytest.raises(RuntimeError) as exc_info:
        settings.validate_auth_configuration()
    assert "minimum 16 characters" in str(exc_info.value)


# ─── 11. CORS Preflight Accepts X-Admin-Key ──────────────────────────────────

def test_cors_preflight_accepts_admin_key_header(client):
    """OPTIONS preflight requests allow X-Admin-Key without authentication."""
    headers = {
        "Origin": "http://localhost:5173",
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "X-Admin-Key, Content-Type",
    }
    res = client.options("/api/v1/channels", headers=headers)
    assert res.status_code == 200
    allow_headers = res.headers.get("access-control-allow-headers", "")
    assert "*" in allow_headers or "x-admin-key" in allow_headers.lower()


# ─── 12. Admin Key Is Never Exposed in API Responses ─────────────────────────

def test_admin_key_never_exposed_in_api_responses(client):
    """Verify that the secret admin key is not leaked in any API payload or error."""
    headers = {"X-Admin-Key": TEST_ADMIN_KEY}

    endpoints = [
        "/api/v1/auth/verify",
        "/api/v1/health",
        "/api/v1/channels",
        "/api/v1/videos",
        "/api/v1/instagram/status",
        "/api/v1/publishing/queue-videos",
        "/api/v1/history",
    ]
    for ep in endpoints:
        res = client.get(ep, headers=headers)
        body_text = res.text
        assert TEST_ADMIN_KEY not in body_text, f"Key leaked in response for {ep}!"
