"""
Instagram OAuth service for Meta Unified Login (Facebook Login for Business).
Authenticates via Meta Business configuration (config_id) and System-User Access Tokens (SUAT).
"""
import httpx
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
from urllib.parse import urlencode
from app.core.config import settings


class InstagramOAuth:
    """Handles Meta Unified Login (Facebook Login for Business) OAuth 2.0 flow."""

    DIALOG_OAUTH_URL = "https://www.facebook.com/{version}/dialog/oauth"
    TOKEN_URL = "https://graph.facebook.com/{version}/oauth/access_token"
    GRAPH_BASE_URL = "https://graph.facebook.com/{version}"

    # In-memory server-side verification cache: cache_key -> {"data": dict, "cached_at": datetime}
    _verification_cache: Dict[str, Dict[str, Any]] = {}
    CACHE_TTL_SECONDS: int = 300  # 5 minutes

    @classmethod
    def clear_cache(cls):
        """Clear the verification cache (e.g. on disconnect or refresh)."""
        cls._verification_cache.clear()

    def get_authorization_url(self, state: str) -> str:
        """
        Build the Meta Unified Login authorization URL using config_id.
        Does not send legacy scopes; permissions and token type (SUAT)
        are determined by the Meta Facebook Login for Business configuration.
        """
        app_id = settings.effective_app_id
        if not app_id:
            raise ValueError("META_APP_ID is not configured.")

        params: Dict[str, str] = {
            "client_id": app_id,
            "redirect_uri": settings.effective_redirect_uri,
            "response_type": "code",
            "override_default_response_type": "true",
            "state": state,
        }

        # Include config_id when configured in Meta dashboard
        config_id = (getattr(settings, "meta_config_id", "") or "").strip()
        if config_id:
            params["config_id"] = config_id

        url = self.DIALOG_OAUTH_URL.format(version=settings.meta_api_version)
        return f"{url}?{urlencode(params)}"

    async def exchange_code_for_token(self, code: str) -> Dict[str, Any]:
        """
        Exchange authorization code for access token via Meta Graph API.
        1. Exchange code for access token with graph.facebook.com/oauth/access_token
        2. Discover assigned Instagram Professional account from business asset edges
        """
        clean_code = code.split("#_")[0].strip() if code else ""
        if not clean_code:
            raise ValueError("Invalid or empty authorization code")

        app_id = settings.effective_app_id
        app_secret = settings.effective_app_secret
        if not app_id or not app_secret:
            raise ValueError("META_APP_ID and META_APP_SECRET must be configured.")

        async with httpx.AsyncClient() as client:
            # 1. Exchange code for access token
            token_url = self.TOKEN_URL.format(version=settings.meta_api_version)
            resp = await client.get(
                token_url,
                params={
                    "client_id": app_id,
                    "client_secret": app_secret,
                    "redirect_uri": settings.effective_redirect_uri,
                    "code": clean_code,
                },
            )
            resp.raise_for_status()
            token_data = resp.json()

            access_token = token_data.get("access_token")
            if not access_token:
                raise ValueError("No access token returned by Meta OAuth exchange")

            # Check for token expiration (SUAT tokens have no expiration / null expires_in)
            expires_in: Optional[int] = token_data.get("expires_in")
            if expires_in is not None:
                try:
                    expires_in = int(expires_in)
                    if expires_in <= 0:
                        expires_in = None
                except (ValueError, TypeError):
                    expires_in = None

            # 2. Discover assigned Instagram Professional account
            ig_account = await self._discover_instagram_account(client, access_token)
            if not ig_account or not ig_account.get("id"):
                raise ValueError(
                    "No assigned Instagram Professional account found. "
                    "Please ensure an Instagram Business/Creator account is assigned to your "
                    "Facebook Login for Business configuration in Meta Business Settings."
                )

            return {
                "access_token": access_token,
                "user_data": ig_account,
                "user_id": ig_account["id"],
                "expires_in": expires_in,  # None for non-expiring SUAT
            }

    async def _discover_instagram_account(self, client: httpx.AsyncClient, access_token: str) -> Optional[Dict[str, Any]]:
        """
        Discover the assigned Instagram account from Meta Business Assets.
        Tries /me/assigned_instagram_accounts first (standard for Business Login / SUAT),
        then falls back to /me/accounts (pages with linked instagram_business_account).
        """
        graph_base = self.GRAPH_BASE_URL.format(version=settings.meta_api_version)

        # Primary: /me/assigned_instagram_accounts
        try:
            resp = await client.get(
                f"{graph_base}/me/assigned_instagram_accounts",
                params={
                    "fields": "id,username,name,profile_picture_url,followers_count",
                    "access_token": access_token,
                },
            )
            if resp.status_code == 200:
                data = resp.json().get("data", [])
                if data and len(data) > 0:
                    acc = data[0]
                    return {
                        "id": str(acc.get("id")),
                        "username": acc.get("username") or acc.get("name") or "unknown",
                        "name": acc.get("name") or acc.get("username"),
                        "account_type": "CREATOR",
                        "profile_picture_url": acc.get("profile_picture_url"),
                        "followers_count": acc.get("followers_count"),
                    }
        except Exception:
            pass

        # Fallback: /me/accounts (pages -> instagram_business_account)
        try:
            resp = await client.get(
                f"{graph_base}/me/accounts",
                params={
                    "fields": "id,name,instagram_business_account{id,username,name,profile_picture_url,followers_count}",
                    "access_token": access_token,
                },
            )
            if resp.status_code == 200:
                pages = resp.json().get("data", [])
                for page in pages:
                    ig = page.get("instagram_business_account")
                    if ig and ig.get("id"):
                        return {
                            "id": str(ig["id"]),
                            "username": ig.get("username") or ig.get("name") or "unknown",
                            "name": ig.get("name") or ig.get("username"),
                            "account_type": "CREATOR",
                            "profile_picture_url": ig.get("profile_picture_url"),
                            "followers_count": ig.get("followers_count"),
                        }
        except Exception:
            pass

        # Direct fallback: /me?fields=instagram_business_account
        try:
            resp = await client.get(
                f"{graph_base}/me",
                params={
                    "fields": "id,username,name,instagram_business_account{id,username,name,profile_picture_url,followers_count}",
                    "access_token": access_token,
                },
            )
            if resp.status_code == 200:
                me_data = resp.json()
                ig = me_data.get("instagram_business_account")
                if ig and ig.get("id"):
                    return {
                        "id": str(ig["id"]),
                        "username": ig.get("username") or ig.get("name") or "unknown",
                        "name": ig.get("name") or ig.get("username"),
                        "account_type": "CREATOR",
                        "profile_picture_url": ig.get("profile_picture_url"),
                        "followers_count": ig.get("followers_count"),
                    }
        except Exception:
            pass

        return None

    async def verify_token_and_fetch_profile(
        self,
        access_token: str,
        instagram_user_id: str,
        force_refresh: bool = False,
    ) -> Dict[str, Any]:
        """
        Verify access token health with Meta debug_token and query fresh profile from Graph API.
        Caches verification results in memory to avoid redundant Meta API calls.
        Never exposes raw access tokens or secrets.
        """
        cache_key = str(instagram_user_id or "default")
        now = datetime.now(timezone.utc)

        if not force_refresh and cache_key in self._verification_cache:
            entry = self._verification_cache[cache_key]
            if (now - entry["cached_at"]).total_seconds() < self.CACHE_TTL_SECONDS:
                return entry["data"]

        app_id = settings.effective_app_id
        app_secret = settings.effective_app_secret

        req_scopes = ["instagram_basic", "instagram_content_publish"]
        result: Dict[str, Any] = {
            "token_validity": "UNKNOWN",
            "token_type": None,
            "expires_at_raw": None,
            "data_access_expires_at": None,
            "token_status_message": "Verification pending",
            "granted_scopes": [],
            "required_scopes": req_scopes,
            "additional_scopes": [],
            "missing_scopes": list(req_scopes),
            "last_verified_at": now,
            "profile": None,
            "error": None,
        }

        if not access_token:
            result["token_validity"] = "INVALID"
            result["token_status_message"] = "No access token present"
            return result

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                # 1. Inspect token via Meta debug_token endpoint
                debug_url = "https://graph.facebook.com/debug_token"
                app_access_token = f"{app_id}|{app_secret}" if app_id and app_secret else access_token
                debug_resp = await client.get(
                    debug_url,
                    params={
                        "input_token": access_token,
                        "access_token": app_access_token,
                    },
                )

                if debug_resp.status_code == 200:
                    d_data = debug_resp.json().get("data", {})
                    is_valid = bool(d_data.get("is_valid", False))
                    result["token_validity"] = "VALID" if is_valid else "INVALID"
                    result["token_type"] = d_data.get("type", "UNKNOWN")
                    exp_raw = d_data.get("expires_at", 0)
                    result["expires_at_raw"] = exp_raw
                    result["data_access_expires_at"] = d_data.get("data_access_expires_at", 0)

                    if exp_raw == 0:
                        result["token_status_message"] = "No expiry reported by Meta"
                    elif exp_raw and exp_raw > 0:
                        exp_dt = datetime.fromtimestamp(exp_raw, tz=timezone.utc)
                        result["token_status_message"] = f"Expires {exp_dt.strftime('%b %d, %Y')}"
                    else:
                        result["token_status_message"] = "Expired or invalid"

                    scopes = [str(s) for s in d_data.get("scopes", [])]
                    result["granted_scopes"] = scopes
                    result["missing_scopes"] = [s for s in req_scopes if s not in scopes]
                    result["additional_scopes"] = [s for s in scopes if s not in req_scopes]
                else:
                    result["token_validity"] = "UNKNOWN"
                    result["token_status_message"] = f"Meta debug_token returned HTTP {debug_resp.status_code}"

                # 2. Query fresh profile from Meta Graph API
                graph_base = self.GRAPH_BASE_URL.format(version=settings.meta_api_version)
                prof_url = f"{graph_base}/{instagram_user_id}"
                prof_resp = await client.get(
                    prof_url,
                    params={
                        "fields": "id,username,name,profile_picture_url,followers_count,account_type",
                        "access_token": access_token,
                    },
                )
                if prof_resp.status_code == 200:
                    result["profile"] = prof_resp.json()

        except Exception as e:
            result["token_validity"] = "UNKNOWN"
            result["token_status_message"] = f"Verification failed: {str(e)}"
            result["error"] = str(e)

        self._verification_cache[cache_key] = {
            "data": result,
            "cached_at": now,
        }
        return result
