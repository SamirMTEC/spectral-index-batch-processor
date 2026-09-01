"""Database-backed fixtures for domain endpoint tests."""

from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest_asyncio
from fastapi import FastAPI
from spectral_api.domain.models import Base
from spectral_api.main import create_app
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


class FakeObjectStorage:
    def __init__(self) -> None:
        self.multipart_counter = 0
        self.raise_on_head = False
        self.content_length = 100

    def generate_presigned_url(self, operation: str, **kwargs: Any) -> str:
        params = kwargs["Params"]
        return f"https://storage.test/{operation}/{params['Key']}"

    def create_multipart_upload(self, **kwargs: Any) -> dict[str, str]:
        self.multipart_counter += 1
        return {"UploadId": f"multipart-{self.multipart_counter}"}

    def complete_multipart_upload(self, **kwargs: Any) -> dict[str, str]:
        return {"ETag": "completed"}

    def head_object(self, **kwargs: Any) -> dict[str, object]:
        if self.raise_on_head:
            raise OSError("interrupted")
        return {"ContentLength": self.content_length, "ETag": '"test-etag"'}

    def abort_multipart_upload(self, **kwargs: Any) -> None:
        return None


class FakeRedis:
    def __init__(self) -> None:
        self.messages: list[tuple[str, dict[str, str]]] = []

    async def xadd(self, stream: str, fields: dict[str, str]) -> str:
        self.messages.append((stream, fields))
        return "1-0"


class FakeCeleryControl:
    def __init__(self) -> None:
        self.revoked: list[str] = []

    def revoke(self, task_id: str, *, terminate: bool = False) -> None:
        assert terminate is False
        self.revoked.append(task_id)


class FakeCelery:
    def __init__(self) -> None:
        self.tasks: list[dict[str, Any]] = []
        self.control = FakeCeleryControl()

    def send_task(self, name: str, **options: Any) -> None:
        self.tasks.append({"name": name, **options})


@pytest_asyncio.fixture
async def domain_app() -> AsyncIterator[FastAPI]:
    """Provide an isolated database without starting external dependencies."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    app = create_app()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    storage = FakeObjectStorage()
    app.state.session_factory = factory
    app.state.resources = SimpleNamespace(
        session_factory=factory,
        s3=storage,
        s3_presign=storage,
        redis=FakeRedis(),
        celery=FakeCelery(),
    )
    try:
        yield app
    finally:
        await engine.dispose()
