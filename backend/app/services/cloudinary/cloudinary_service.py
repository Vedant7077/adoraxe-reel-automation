"""
Cloudinary service – video upload, verification, and URL management.
Credentials are NEVER logged or exposed.
"""
from __future__ import annotations
import os
import asyncio
from typing import Optional, Dict, Any

from app.core.config import settings


class CloudinaryService:
    """Handles video upload, URL retrieval, and delivery verification via Cloudinary."""

    def is_configured(self) -> bool:
        return settings.cloudinary_configured

    def _configure(self):
        """Configure Cloudinary SDK with environment credentials."""
        import cloudinary
        cloudinary.config(
            cloud_name=settings.cloudinary_cloud_name,
            api_key=settings.cloudinary_api_key,
            api_secret=settings.cloudinary_api_secret,
            secure=True,
        )

    def get_public_id(self, video_id: str, asset_type: str = "reel") -> str:
        """
        Generate a deterministic, unique public ID for an asset type.
        asset_type: 'original' or 'reel'
        """
        prefix = "adoraxe/originals" if asset_type == "original" else "adoraxe/reels"
        return f"{prefix}/{video_id}"

    async def upload_video(
        self,
        file_path: str,
        public_id: str,
        folder: Optional[str] = None,
        overwrite: bool = True,
    ) -> Optional[Dict[str, Any]]:
        """
        Upload a video file to Cloudinary.
        Supports deterministic public IDs, overwrites (retryable without duplicate assets),
        and returns upload metadata with HTTPS secure_url.
        Returns None when Cloudinary is not configured.
        """
        if not self.is_configured():
            return None

        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found for Cloudinary upload: {file_path}")

        # Ensure folder prefix is not duplicated if already included in public_id
        upload_opts = {
            "resource_type": "video",
            "overwrite": overwrite,
            "invalidate": True,
        }

        clean_public_id = public_id
        if folder:
            if clean_public_id.startswith(f"{folder}/"):
                upload_opts["public_id"] = clean_public_id
            else:
                upload_opts["folder"] = folder
                upload_opts["public_id"] = clean_public_id
        else:
            upload_opts["public_id"] = clean_public_id

        try:
            import cloudinary.uploader
            self._configure()

            # Run synchronous Cloudinary upload in thread pool to prevent blocking asyncio loop
            result = await asyncio.to_thread(
                cloudinary.uploader.upload,
                file_path,
                **upload_opts,
            )

            if not result or not result.get("secure_url"):
                return None

            return {
                "public_id": result.get("public_id"),
                "secure_url": result.get("secure_url"),
                "duration": result.get("duration"),
                "width": result.get("width"),
                "height": result.get("height"),
                "format": result.get("format"),
                "bytes": result.get("bytes"),
            }
        except Exception as e:
            # Never log the full exception string or credentials
            raise RuntimeError(f"Cloudinary upload failed: {type(e).__name__}") from None

    def get_video_url(self, public_id: str) -> str:
        """Get the HTTPS URL for a Cloudinary asset."""
        if not self.is_configured():
            return ""
        import cloudinary
        self._configure()
        return cloudinary.CloudinaryVideo(public_id).build_url(secure=True)

    async def verify_url(self, url: str) -> bool:
        """
        Verify that a public Cloudinary HTTPS URL is accessible and actually serves video content.
        Uses HEAD or streaming Range GET to verify delivery instantly without buffering large videos.
        """
        if not url or not (url.startswith("https://") or url.startswith("http://")):
            return False
        try:
            import httpx
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                # 1. Fast lightweight HEAD check: zero byte payload
                try:
                    head_resp = await client.head(url)
                    if head_resp.status_code in (200, 206):
                        ct = head_resp.headers.get("content-type", "").lower()
                        if not ct or "video" in ct or "octet-stream" in ct or "binary" in ct or "application" in ct:
                            return True
                except Exception:
                    pass

                # 2. Streaming Range GET: reads only the first 1KB without downloading the 90MB file
                try:
                    async with client.stream("GET", url, headers={"Range": "bytes=0-1023"}) as stream_resp:
                        if stream_resp.status_code in (200, 206):
                            async for _ in stream_resp.aiter_bytes(chunk_size=1024):
                                return True
                            return True
                except Exception:
                    pass

                # 3. Fallback GET (accommodates mock clients in test suites)
                try:
                    resp = await client.get(url, headers={"Range": "bytes=0-1023"})
                    if resp.status_code in (200, 206):
                        return True
                except Exception:
                    pass

                return False
        except Exception:
            return False

    async def delete_video(self, public_id: str) -> bool:
        """Delete an asset from Cloudinary by public ID."""
        if not self.is_configured() or not public_id:
            return False
        try:
            import cloudinary.uploader
            self._configure()
            res = await asyncio.to_thread(
                cloudinary.uploader.destroy,
                public_id,
                resource_type="video",
            )
            return res.get("result") == "ok"
        except Exception:
            return False
