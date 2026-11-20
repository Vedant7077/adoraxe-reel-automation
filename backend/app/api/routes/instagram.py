"""
Instagram OAuth and account management routes.
"""
import secrets
from typing import List
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.schemas import InstagramStatusResponse, InstagramConnectResponse, InstagramAccountItem
from app.services.instagram.oauth import InstagramOAuth
from app.services.instagram.token_manager import TokenManager
from app.repositories.instagram_repository import InstagramRepository
from app.core.config import settings
from app.core.security import require_admin
from app.models.models import InstagramAccountStatus

router = APIRouter()

# In-memory OAuth state store (use Redis in production)
_oauth_states: dict[str, bool] = {}


@router.get("/status", response_model=InstagramStatusResponse, dependencies=[Depends(require_admin)])
async def get_instagram_status(
    force_refresh: bool = Query(False, description="Bypass cache and force fresh Meta token and profile verification"),
    db: Session = Depends(get_db),
):
    """
    Get current Instagram account connection status and verified token health.
    Uses safe server-side caching to avoid excessive Meta API calls.
    """
    repo = InstagramRepository(db)
    account = repo.get_active_account()

    all_accs = repo.list_all_accounts()
    account_items = [
        InstagramAccountItem(
            id=a.id,
            instagram_user_id=a.instagram_user_id,
            username=a.username,
            full_name=a.full_name,
            profile_picture_url=a.profile_picture_url,
            account_type=a.account_type,
            status=a.status,
            is_active=bool(account and account.id == a.id) or (not account and a.id == "00000000-0000-0000-0000-000000000001"),
            is_demo=(a.id == "00000000-0000-0000-0000-000000000001"),
            connected_at=a.connected_at,
        )
        for a in all_accs
    ]

    base = {
        "instagram_configured": settings.instagram_configured,
        "demo_mode": settings.demo_mode,
        "meta_app_name": getattr(settings, "meta_app_name", "Adoraxe Reel Automation"),
        "meta_app_id": settings.effective_app_id,
        "meta_config_id": getattr(settings, "meta_config_id", "") or None,
        "meta_api_version": settings.meta_api_version,
        "connected_accounts": account_items,
    }

    if not account:
        if settings.demo_mode:
            demo_acc = repo.get_or_create_demo_account()
            return InstagramStatusResponse(
                status=InstagramAccountStatus.NOT_CONNECTED,
                account_id=demo_acc.id,
                username=demo_acc.username,
                full_name=demo_acc.full_name,
                account_type=demo_acc.account_type,
                connected_at=demo_acc.connected_at,
                is_demo_placeholder=True,
                token_validity=None,
                token_status_message="Demo placeholder - not connected to Meta",
                granted_scopes=[],
                missing_scopes=["instagram_basic", "instagram_content_publish"],
                additional_scopes=[],
                **base
            )
        return InstagramStatusResponse(
            status=InstagramAccountStatus.NOT_CONNECTED,
            is_demo_placeholder=False,
            token_validity=None,
            token_status_message="No account connected",
            granted_scopes=[],
            missing_scopes=["instagram_basic", "instagram_content_publish"],
            additional_scopes=[],
            **base
        )

    # Real connected account: perform verified token health and profile check
    oauth = InstagramOAuth()
    token_mgr = TokenManager()
    access_token = ""
    if account.encrypted_access_token:
        try:
            access_token = token_mgr.decrypt_token(account.encrypted_access_token)
        except Exception:
            access_token = ""

    verification = await oauth.verify_token_and_fetch_profile(
        access_token=access_token,
        instagram_user_id=account.instagram_user_id,
        force_refresh=force_refresh,
    )

    # If Graph API profile was returned, update the local DB record with fresh details
    fresh_profile = verification.get("profile")
    if fresh_profile and isinstance(fresh_profile, dict):
        updated_username = fresh_profile.get("username")
        updated_name = fresh_profile.get("name")
        updated_pic = fresh_profile.get("profile_picture_url")
        updated_followers = fresh_profile.get("followers_count")
        updated_type = fresh_profile.get("account_type")
        repo.update_account_profile(
            account_id=account.id,
            username=updated_username,
            full_name=updated_name,
            profile_picture_url=updated_pic,
            followers_count=updated_followers,
            account_type=updated_type,
        )

    return InstagramStatusResponse(
        status=account.status,
        account_id=account.id,
        instagram_user_id=account.instagram_user_id,
        username=account.username,
        full_name=account.full_name,
        profile_picture_url=account.profile_picture_url,
        account_type=account.account_type,
        followers_count=account.followers_count,
        token_expires_at=account.token_expires_at,
        connected_at=account.connected_at,
        is_demo_placeholder=False,
        token_validity=verification.get("token_validity"),
        token_type=verification.get("token_type"),
        expires_at_raw=verification.get("expires_at_raw"),
        data_access_expires_at=verification.get("data_access_expires_at"),
        token_status_message=verification.get("token_status_message"),
        last_verified_at=verification.get("last_verified_at"),
        granted_scopes=verification.get("granted_scopes", []),
        required_scopes=verification.get("required_scopes", ["instagram_basic", "instagram_content_publish"]),
        additional_scopes=verification.get("additional_scopes", []),
        missing_scopes=verification.get("missing_scopes", []),
        **base
    )


@router.post("/refresh", response_model=InstagramStatusResponse, dependencies=[Depends(require_admin)])
async def refresh_instagram_status(db: Session = Depends(get_db)):
    """
    Force a live backend verification of the Instagram access token and account profile.
    Updates the local database and caches the latest verified health state.
    """
    return await get_instagram_status(force_refresh=True, db=db)


@router.get("/accounts", response_model=List[InstagramAccountItem], dependencies=[Depends(require_admin)])
async def list_instagram_accounts(db: Session = Depends(get_db)):
    """List all connected Instagram accounts and indicate which one is currently active."""
    repo = InstagramRepository(db)
    active = repo.get_active_account()
    accounts = repo.list_all_accounts()

    items = []
    for a in accounts:
        is_demo = (a.id == "00000000-0000-0000-0000-000000000001")
        is_active = bool(active and active.id == a.id) or (not active and is_demo)
        items.append(
            InstagramAccountItem(
                id=a.id,
                instagram_user_id=a.instagram_user_id,
                username=a.username,
                full_name=a.full_name,
                profile_picture_url=a.profile_picture_url,
                account_type=a.account_type,
                status=a.status,
                is_active=is_active,
                is_demo=is_demo,
                connected_at=a.connected_at,
            )
        )
    return items


@router.post("/accounts/{account_id}/activate", dependencies=[Depends(require_admin)])
async def activate_instagram_account(account_id: str, db: Session = Depends(get_db)):
    """Set the active Instagram account for publishing."""
    repo = InstagramRepository(db)
    acc = repo.set_active_account(account_id)
    if not acc:
        raise HTTPException(status_code=404, detail="Instagram account not found")
    InstagramOAuth.clear_cache()
    return {"message": "Account activated successfully", "account_id": acc.id, "username": acc.username}


@router.get("/connect", response_model=InstagramConnectResponse, dependencies=[Depends(require_admin)])
async def connect_instagram():
    """Generate Instagram OAuth authorization URL."""
    if not settings.instagram_configured:
        raise HTTPException(
            status_code=503,
            detail="Instagram API is not configured. Please set INSTAGRAM_APP_ID (or META_APP_ID) and INSTAGRAM_APP_SECRET in your .env file."
        )

    state = secrets.token_urlsafe(32)
    _oauth_states[state] = True

    oauth = InstagramOAuth()
    auth_url = oauth.get_authorization_url(state=state)

    return InstagramConnectResponse(auth_url=auth_url)


@router.get("/callback")
async def instagram_callback(
    code: str = Query(None),
    state: str = Query(None),
    error: str = Query(None),
    error_reason: str = Query(None),
    error_description: str = Query(None),
    db: Session = Depends(get_db),
):
    """Handle Instagram OAuth callback."""
    if error:
        err_msg = error_description or error_reason or error
        return RedirectResponse(url=f"{settings.frontend_url}/instagram?error={err_msg}")

    if not state or state not in _oauth_states:
        raise HTTPException(status_code=400, detail="Invalid OAuth state. Possible CSRF attack.")

    del _oauth_states[state]

    clean_code = code.split("#_")[0].strip() if code else ""
    if not clean_code:
        return RedirectResponse(url=f"{settings.frontend_url}/instagram?error=missing_code")

    oauth = InstagramOAuth()
    try:
        account_data = await oauth.exchange_code_for_token(code=clean_code)
        repo = InstagramRepository(db)
        repo.upsert_account(account_data)
    except Exception as e:
        return RedirectResponse(url=f"{settings.frontend_url}/instagram?error=callback_failed")

    return RedirectResponse(url=f"{settings.frontend_url}/instagram?connected=true")


@router.post("/disconnect", dependencies=[Depends(require_admin)])
async def disconnect_instagram(db: Session = Depends(get_db)):
    """Disconnect the Instagram account. Preserves publishing history intact."""
    repo = InstagramRepository(db)
    repo.disconnect_all()
    InstagramOAuth.clear_cache()
    return {"message": "Instagram account disconnected successfully"}
