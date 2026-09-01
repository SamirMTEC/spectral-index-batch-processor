"""Liveness and dependency readiness endpoints."""

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from spectral_api import __version__
from spectral_api.services.readiness import ReadinessProbe

router = APIRouter(tags=["operations"])


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Report process liveness without touching external dependencies."""
    return HealthResponse(status="ok", service="api", version=__version__)


@router.get("/ready")
async def ready(request: Request) -> JSONResponse:
    """Report whether PostgreSQL, Redis, and object storage are reachable."""
    probe: ReadinessProbe = request.app.state.readiness_probe
    report = await probe.check()
    status_code = 200 if report.status == "ready" else 503
    payload: dict[str, Any] = report.model_dump(mode="json")
    return JSONResponse(status_code=status_code, content=payload)
