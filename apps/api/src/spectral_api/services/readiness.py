"""Readiness checks for infrastructure dependencies."""

import asyncio
from time import perf_counter

from pydantic import BaseModel
from sqlalchemy import text

from spectral_api.runtime import ApplicationResources


class DependencyStatus(BaseModel):
    name: str
    status: str
    latency_ms: float


class ReadinessReport(BaseModel):
    status: str
    checks: list[DependencyStatus]


class ReadinessProbe:
    """Probe external systems without exposing exception details."""

    def __init__(
        self, resources: ApplicationResources, bucket: str, timeout_seconds: float = 5.0
    ) -> None:
        self._resources = resources
        self._bucket = bucket
        self._timeout_seconds = timeout_seconds

    async def check(self) -> ReadinessReport:
        checks = await asyncio.gather(
            self._bounded(self._check_postgres(), "postgres"),
            self._bounded(self._check_redis(), "redis"),
            self._bounded(self._check_object_storage(), "object_storage"),
        )
        status = "ready" if all(check.status == "ok" for check in checks) else "not_ready"
        return ReadinessReport(status=status, checks=list(checks))

    async def _bounded(self, check: object, name: str) -> DependencyStatus:
        started = perf_counter()
        try:
            return await asyncio.wait_for(check, timeout=self._timeout_seconds)  # type: ignore[arg-type]
        except TimeoutError:
            return self._status(name, "timeout", started)

    async def _check_postgres(self) -> DependencyStatus:
        started = perf_counter()
        try:
            async with self._resources.database.connect() as connection:
                await connection.execute(text("SELECT 1"))
            status = "ok"
        except Exception:
            status = "unavailable"
        return self._status("postgres", status, started)

    async def _check_redis(self) -> DependencyStatus:
        started = perf_counter()
        try:
            await self._resources.redis.ping()
            status = "ok"
        except Exception:
            status = "unavailable"
        return self._status("redis", status, started)

    async def _check_object_storage(self) -> DependencyStatus:
        started = perf_counter()
        try:
            await asyncio.to_thread(self._resources.s3.head_bucket, Bucket=self._bucket)
            status = "ok"
        except Exception:
            status = "unavailable"
        return self._status("object_storage", status, started)

    @staticmethod
    def _status(name: str, status: str, started: float) -> DependencyStatus:
        return DependencyStatus(
            name=name,
            status=status,
            latency_ms=round((perf_counter() - started) * 1000, 2),
        )
