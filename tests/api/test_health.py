"""Operational endpoint contract tests."""

import pytest
from httpx import ASGITransport, AsyncClient
from spectral_api.main import create_app
from spectral_api.services.readiness import DependencyStatus, ReadinessReport


class FakeReadinessProbe:
    def __init__(self, status: str) -> None:
        self._status = status

    async def check(self) -> ReadinessReport:
        dependency_status = "ok" if self._status == "ready" else "unavailable"
        return ReadinessReport(
            status=self._status,
            checks=[
                DependencyStatus(name="postgres", status=dependency_status, latency_ms=0.1),
                DependencyStatus(name="redis", status=dependency_status, latency_ms=0.1),
                DependencyStatus(name="object_storage", status=dependency_status, latency_ms=0.1),
            ],
        )


@pytest.mark.asyncio
async def test_health_is_independent_from_external_services() -> None:
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "api", "version": "0.1.0"}


@pytest.mark.asyncio
async def test_ready_returns_success_when_dependencies_are_available() -> None:
    app = create_app()
    app.state.readiness_probe = FakeReadinessProbe("ready")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/ready")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"


@pytest.mark.asyncio
async def test_ready_returns_service_unavailable_when_a_dependency_fails() -> None:
    app = create_app()
    app.state.readiness_probe = FakeReadinessProbe("not_ready")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"
