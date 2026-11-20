"""
Adoraxe Backend – FastAPI Application Entry Point with startup job recovery.
"""
import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.security import require_admin
from app.api.routes import health, channels, videos, instagram, publishing, history

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(application: FastAPI):
    # Safe startup recovery for interrupted publishing jobs
    try:
        from app.db.database import SessionLocal
        from app.repositories.job_repository import JobRepository
        from app.services.instagram.publisher import InstagramPublisher
        from app.models.models import JobStatus

        session = SessionLocal()
        try:
            repo = JobRepository(session)
            pending_jobs = repo.get_jobs_for_startup_recovery()
            if pending_jobs:
                logger.info("Startup: found %d pending/interrupted publishing jobs", len(pending_jobs))
                pub = InstagramPublisher()
                for j in pending_jobs:
                    j_status = str(j.status)
                    if j_status in (
                        JobStatus.PUBLISH_REQUESTED.value,
                        JobStatus.OUTCOME_UNKNOWN.value,
                        JobStatus.RECONCILIATION_REQUIRED.value,
                        "PUBLISH_REQUESTED",
                        "OUTCOME_UNKNOWN",
                        "RECONCILIATION_REQUIRED",
                    ):
                        # Safe read-only reconciliation: never blind retries
                        asyncio.create_task(pub.reconcile_job(j.id))
                    elif j_status in (
                        JobStatus.QUEUED.value,
                        JobStatus.PENDING.value,
                        JobStatus.CONTAINER_CREATING.value,
                        JobStatus.CONTAINER_PROCESSING.value,
                        JobStatus.READY_TO_PUBLISH.value,
                        "QUEUED",
                        "PENDING",
                        "CONTAINER_CREATING",
                        "CONTAINER_PROCESSING",
                        "READY_TO_PUBLISH",
                    ):
                        # Resume processing from current state
                        asyncio.create_task(pub.publish_reel(j.id, is_demo=j.is_demo, is_recovery=True))
        finally:
            session.close()
    except Exception as e:
        logger.warning("Startup job recovery encountered non-fatal error: %s", e)

    yield


def create_application() -> FastAPI:
    # Fail-closed validation on startup: ensure production cannot boot unauthenticated
    settings.validate_auth_configuration()

    # Disable interactive Swagger/Redoc endpoints in authenticated production to prevent schema leaks
    show_docs = settings.debug or not settings.is_auth_enforced
    docs_url = "/api/docs" if show_docs else None
    redoc_url = "/api/redoc" if show_docs else None
    openapi_url = "/api/openapi.json" if show_docs else None

    application = FastAPI(
        title="Adoraxe API",
        description="YouTube to Instagram Reel Automation API",
        version="1.0.0",
        docs_url=docs_url,
        redoc_url=redoc_url,
        openapi_url=openapi_url,
        lifespan=lifespan,
    )

    # CORS – explicitly accept X-Admin-Key alongside standard headers
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins_list,
        allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*", "X-Admin-Key"],
        expose_headers=["*"],
    )

    # Public health check endpoint
    application.include_router(health.router, prefix="/api/v1", tags=["health"])

    # Authentication verification endpoint for frontend login handshake
    @application.get("/api/v1/auth/verify", dependencies=[Depends(require_admin)], tags=["auth"])
    async def verify_admin_auth():
        return {"authenticated": True, "message": "Administrator session valid"}

    # Protected Application Routers
    application.include_router(
        channels.router, prefix="/api/v1/channels", tags=["channels"], dependencies=[Depends(require_admin)]
    )
    application.include_router(
        videos.router, prefix="/api/v1/videos", tags=["videos"], dependencies=[Depends(require_admin)]
    )
    # Instagram router handles public /callback internally while protecting /status, /connect, /disconnect, etc.
    application.include_router(instagram.router, prefix="/api/v1/instagram", tags=["instagram"])
    application.include_router(
        publishing.router, prefix="/api/v1/publishing", tags=["publishing"], dependencies=[Depends(require_admin)]
    )
    application.include_router(
        history.router, prefix="/api/v1/history", tags=["history"], dependencies=[Depends(require_admin)]
    )

    return application


app = create_application()
