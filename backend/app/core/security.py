"""
Security & Administrator Authentication – Adoraxe Automation Portal.
Enforces single-owner administrator authentication via X-Admin-Key header.
Fail-closed in production; timing-attack resistant via secrets.compare_digest.
"""
import time
import secrets
import logging
from collections import defaultdict
from typing import Optional

from fastapi import Security, HTTPException, Request, status
from fastapi.security import APIKeyHeader

from app.core.config import settings

logger = logging.getLogger(__name__)

ADMIN_HEADER_NAME = "X-Admin-Key"
api_key_header = APIKeyHeader(name=ADMIN_HEADER_NAME, auto_error=False)

# In-memory sliding-window rate limiter for failed authentication attempts
# IP -> list of failure timestamps
_failed_attempts: dict[str, list[float]] = defaultdict(list)
RATE_LIMIT_WINDOW_SECONDS = 60.0
MAX_FAILED_ATTEMPTS = 10


def _is_rate_limited(client_ip: str) -> bool:
    """Check whether the client IP has exceeded the failed auth attempt threshold."""
    now = time.time()
    attempts = [t for t in _failed_attempts[client_ip] if now - t < RATE_LIMIT_WINDOW_SECONDS]
    _failed_attempts[client_ip] = attempts
    return len(attempts) >= MAX_FAILED_ATTEMPTS


def _record_failed_attempt(client_ip: str) -> None:
    """Record a failed auth attempt for the client IP."""
    now = time.time()
    _failed_attempts[client_ip].append(now)


def clear_failed_attempts_for_testing() -> None:
    """Clear rate limiting history (for test suites)."""
    _failed_attempts.clear()


async def require_admin(
    request: Request,
    header_key: Optional[str] = Security(api_key_header),
) -> bool:
    """
    FastAPI dependency enforcing single-owner administrator authentication.

    1. If auth is not enforced (explicit development mode), requests pass through.
    2. If auth is enforced:
       - Checks brute-force rate limit (HTTP 429).
       - Checks for presence of X-Admin-Key header (HTTP 401).
       - Compares supplied key with settings.admin_api_key via secrets.compare_digest (HTTP 401).
       - Never logs key values.
       - Returns True upon successful verification.
    """
    if not settings.is_auth_enforced:
        return True

    client_ip = request.client.host if request.client else "unknown"

    # Brute-force protection
    if _is_rate_limited(client_ip):
        logger.warning("Admin authentication rate limit exceeded for IP: %s", client_ip)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed authentication attempts. Please try again later.",
        )

    if not header_key or not header_key.strip():
        _record_failed_attempt(client_ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Administrator authentication required. Missing X-Admin-Key header.",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    expected_key = settings.admin_api_key or ""
    # Safe constant-time comparison
    if not expected_key or not secrets.compare_digest(header_key.strip(), expected_key.strip()):
        _record_failed_attempt(client_ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid administrator access key.",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    return True
