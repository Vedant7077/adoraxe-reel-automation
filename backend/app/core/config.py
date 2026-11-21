"""
Application Configuration – reads from environment variables.
Never prints or exposes secret values.
"""
from __future__ import annotations
from functools import lru_cache
from pathlib import Path
from typing import List
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator

PROJECT_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = Path(__file__).resolve().parents[2]
ROOT_ENV = PROJECT_ROOT / ".env"
BACKEND_ENV = BACKEND_ROOT / ".env"
ENV_FILE_PATH = str(ROOT_ENV if ROOT_ENV.exists() else (BACKEND_ENV if BACKEND_ENV.exists() else ".env"))
DEFAULT_TEMP_DIR = str(BACKEND_ROOT / "temp")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE_PATH,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # App
    app_name: str = "Adoraxe"
    app_version: str = "1.0.0"
    debug: bool = False
    demo_mode: bool = True
    youtube_provider: str = "demo"  # "demo", "http", or "yt_dlp"

    # Security & Administrator Authentication
    secret_key: str = "change-me-in-production"
    token_encryption_key: str = ""  # Fernet key for token encryption
    admin_api_key: str = ""  # Production administrator API key (min 16 chars)
    auth_enabled: bool = False  # Explicitly enabled in production / Render
    dev_auth_bypass: bool = False  # Explicit development-only bypass flag

    def validate_auth_configuration(self) -> None:
        """
        Fail-closed production security validator:
        If authentication is enabled, or if running in production (RENDER=true or ENVIRONMENT=production),
        ADMIN_API_KEY must be configured with a minimum of 16 characters.
        Startup aborts with a clear configuration error if this requirement is not met.
        """
        import os
        is_render = os.getenv("RENDER") == "true"
        is_production = os.getenv("ENVIRONMENT", "").lower() == "production"

        if (self.auth_enabled or is_render or is_production) and not self.dev_auth_bypass:
            if not self.admin_api_key or len(self.admin_api_key.strip()) < 16:
                raise RuntimeError(
                    "CRITICAL SECURITY CONFIGURATION ERROR: Administrator authentication is enabled "
                    "(or running in production on Render), but ADMIN_API_KEY is missing or too short (minimum 16 characters). "
                    "Application startup has failed to prevent unauthenticated public exposure. "
                    "Configure ADMIN_API_KEY in your environment variables or set DEV_AUTH_BYPASS=true for local testing."
                )

    @property
    def is_auth_enforced(self) -> bool:
        import os
        if self.dev_auth_bypass:
            return False
        return bool(self.auth_enabled or os.getenv("RENDER") == "true" or os.getenv("ENVIRONMENT", "").lower() == "production")

    # Database
    database_url: str = "postgresql+psycopg2://adoraxe_user:adoraxe_pass_2026@localhost:5432/adoraxe_db"

    # URLs
    frontend_url: str = "http://localhost:5173"
    backend_url: str = "http://localhost:8000"
    allowed_origins: str = "http://localhost:5173,http://localhost:4173,http://127.0.0.1:5173,http://127.0.0.1:4173,http://localhost:8000,http://127.0.0.1:8000"

    # Meta / Instagram Unified Login
    meta_app_id: str = ""
    meta_app_secret: str = ""
    meta_config_id: str = ""
    meta_redirect_uri: str = "http://localhost:8000/api/v1/instagram/callback"
    meta_api_version: str = "v26.0"
    meta_app_name: str = "Adoraxe Reel Automation"
    instagram_app_id: str = ""
    instagram_app_secret: str = ""
    instagram_redirect_uri: str = ""

    # Cloudinary
    cloudinary_cloud_name: str = ""
    cloudinary_api_key: str = ""
    cloudinary_api_secret: str = ""

    # yt-dlp Media Retrieval (Optional)
    ytdlp_enabled: bool = False
    ytdlp_download_dir: str = DEFAULT_TEMP_DIR
    ytdlp_max_file_size_mb: int = 2048

    @property
    def allowed_origins_list(self) -> List[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    @property
    def effective_app_id(self) -> str:
        return (self.instagram_app_id or self.meta_app_id or "").strip()

    @property
    def effective_app_secret(self) -> str:
        return (self.instagram_app_secret or self.meta_app_secret or "").strip()

    @property
    def effective_redirect_uri(self) -> str:
        return (self.instagram_redirect_uri or self.meta_redirect_uri or "http://localhost:8000/api/v1/instagram/callback").strip()

    @property
    def instagram_configured(self) -> bool:
        return bool(self.effective_app_id and self.effective_app_secret)

    @property
    def cloudinary_configured(self) -> bool:
        return bool(self.cloudinary_cloud_name and self.cloudinary_api_key and self.cloudinary_api_secret)


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
