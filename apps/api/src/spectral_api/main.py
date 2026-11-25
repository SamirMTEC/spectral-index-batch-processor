"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from spectral_common import get_settings
from spectral_common.logging import configure_logging

from spectral_api import __version__
from spectral_api.routes.band_sequences import router as band_sequences_router
from spectral_api.routes.calibration import router as calibration_router
from spectral_api.routes.dashboard import router as dashboard_router
from spectral_api.routes.datasets import router as datasets_router
from spectral_api.routes.exports import router as exports_router
from spectral_api.routes.health import router as health_router
from spectral_api.routes.processing import router as processing_router
from spectral_api.routes.projects import router as projects_router
from spectral_api.routes.recipes import router as recipes_router
from spectral_api.routes.results import router as results_router
from spectral_api.routes.rois import router as rois_router
from spectral_api.routes.uploads import router as uploads_router
from spectral_api.runtime import ApplicationResources
from spectral_api.security import (
    RateLimitMiddleware,
    RequestBodyLimitMiddleware,
    RequestLoggingMiddleware,
    RequestTimeoutMiddleware,
    SecurityHeadersMiddleware,
)
from spectral_api.services.readiness import ReadinessProbe


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create and release process-level clients."""
    settings = get_settings()
    logger = structlog.get_logger("spectral_api.lifecycle")
    resources = ApplicationResources.create(settings)
    app.state.resources = resources
    app.state.readiness_probe = ReadinessProbe(
        resources, settings.s3_bucket, settings.readiness_timeout_seconds
    )
    logger.info("api_started", environment=settings.environment, version=__version__)
    try:
        yield
    finally:
        logger.info("api_stopping")
        await resources.close()
        logger.info("api_stopped")


def create_app() -> FastAPI:
    """Build the HTTP application without connecting to dependencies."""
    settings = get_settings()
    configure_logging(settings.log_level)
    application = FastAPI(
        title="SpectralIndex API",
        summary="Control plane for reproducible spectral image batch processing.",
        version=__version__,
        lifespan=lifespan,
        docs_url=None if settings.environment in {"staging", "production"} else "/docs",
        redoc_url=None if settings.environment in {"staging", "production"} else "/redoc",
        openapi_url=(
            None if settings.environment in {"staging", "production"} else "/openapi.json"
        ),
    )
    application.add_middleware(
        SecurityHeadersMiddleware,
        deployed=settings.environment in {"staging", "production"},
    )
    application.add_middleware(RequestLoggingMiddleware)
    application.add_middleware(
        RequestTimeoutMiddleware, timeout_seconds=settings.api_request_timeout_seconds
    )
    application.add_middleware(RequestBodyLimitMiddleware, max_bytes=settings.api_max_request_bytes)
    application.add_middleware(
        RateLimitMiddleware,
        general_limit=settings.api_rate_limit_requests,
        upload_limit=settings.upload_rate_limit_requests,
        window_seconds=settings.rate_limit_window_seconds,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "Idempotency-Key",
            "If-Match",
            "X-Request-ID",
            *(["X-Dev-User-Id"] if settings.development_auth_enabled else []),
        ],
        expose_headers=["X-Request-ID"],
    )
    application.include_router(health_router)
    application.include_router(dashboard_router, prefix="/api/v1")
    application.include_router(band_sequences_router, prefix="/api/v1")
    application.include_router(calibration_router, prefix="/api/v1")
    application.include_router(projects_router, prefix="/api/v1")
    application.include_router(recipes_router, prefix="/api/v1")
    application.include_router(processing_router, prefix="/api/v1")
    application.include_router(exports_router, prefix="/api/v1")
    application.include_router(results_router, prefix="/api/v1")
    application.include_router(datasets_router, prefix="/api/v1")
    application.include_router(uploads_router, prefix="/api/v1")
    application.include_router(rois_router, prefix="/api/v1")
    return application


app = create_app()
