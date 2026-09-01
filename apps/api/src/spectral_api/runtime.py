"""Long-lived clients owned by the API process."""

from dataclasses import dataclass
from typing import Any

import boto3
from botocore.config import Config
from celery import Celery
from redis.asyncio import Redis
from spectral_common import Settings
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


@dataclass(slots=True)
class ApplicationResources:
    database: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    redis: Redis
    s3: Any
    s3_presign: Any
    celery: Celery

    @classmethod
    def create(cls, settings: Settings) -> "ApplicationResources":
        """Create lazy network clients from validated settings."""
        database = create_async_engine(
            settings.database_dsn,
            pool_pre_ping=True,
            pool_size=settings.database_pool_size,
            max_overflow=settings.database_max_overflow,
            pool_timeout=settings.database_pool_timeout_seconds,
            pool_recycle=settings.database_pool_recycle_seconds,
        )
        redis_client = Redis.from_url(
            settings.redis_dsn,
            decode_responses=True,
            socket_connect_timeout=settings.readiness_timeout_seconds,
            socket_timeout=settings.storage_read_timeout_seconds,
            health_check_interval=30,
        )

        s3_options: dict[str, Any] = {
            "service_name": "s3",
            "region_name": settings.s3_region,
            "config": Config(
                signature_version="s3v4",
                s3={"addressing_style": "path" if settings.s3_force_path_style else "auto"},
                connect_timeout=settings.storage_connect_timeout_seconds,
                read_timeout=settings.storage_read_timeout_seconds,
                max_pool_connections=settings.storage_max_connections,
                retries={"max_attempts": 3, "mode": "standard"},
            ),
        }
        if settings.s3_endpoint_url is not None:
            s3_options["endpoint_url"] = settings.s3_endpoint_url
        if settings.s3_access_key_id is not None:
            s3_options["aws_access_key_id"] = settings.s3_access_key_id
        if settings.s3_secret is not None:
            s3_options["aws_secret_access_key"] = settings.s3_secret

        s3_client = boto3.client(**s3_options)
        presign_options = dict(s3_options)
        if settings.s3_public_endpoint_url is not None:
            presign_options["endpoint_url"] = settings.s3_public_endpoint_url

        return cls(
            database=database,
            session_factory=async_sessionmaker(database, expire_on_commit=False),
            redis=redis_client,
            s3=s3_client,
            s3_presign=boto3.client(**presign_options),
            celery=Celery(
                "spectral-api",
                broker=settings.redis_dsn,
                backend=settings.redis_dsn,
            ),
        )

    async def close(self) -> None:
        """Close all network clients gracefully."""
        await self.redis.aclose()
        await self.database.dispose()
        close = getattr(self.s3, "close", None)
        if close is not None:
            close()
        presign_close = getattr(self.s3_presign, "close", None)
        if presign_close is not None:
            presign_close()
