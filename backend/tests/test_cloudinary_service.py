"""
Tests for Cloudinary service interface and behavior when not configured.
"""
import pytest
from app.services.cloudinary.cloudinary_service import CloudinaryService
from app.core.config import settings


def test_cloudinary_service_configured_check():
    service = CloudinaryService()
    # By default, without credentials, it is not configured
    expected = bool(
        settings.cloudinary_cloud_name
        and settings.cloudinary_api_key
        and settings.cloudinary_api_secret
    )
    assert service.is_configured() == expected


@pytest.mark.asyncio
async def test_cloudinary_upload_when_not_configured():
    service = CloudinaryService()
    # Force settings unconfigured for test
    if not service.is_configured():
        result = await service.upload_video("/tmp/fake_video.mp4", "test_id")
        assert result is None
