"""
Health check API route.
"""
import shutil
from fastapi import APIRouter
from app.schemas.schemas import HealthResponse
from app.core.config import settings
from app.db.database import check_db_connection

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health_check():
    """System health check endpoint."""
    return HealthResponse(
        status="ok",
        version=settings.app_version,
        demo_mode=settings.demo_mode,
        database=check_db_connection(),
        ffmpeg=shutil.which("ffmpeg") is not None,
        cloudinary_configured=settings.cloudinary_configured,
        instagram_configured=settings.instagram_configured,
    )
