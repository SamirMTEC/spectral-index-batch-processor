"""Adversarial API tests for tenant boundaries and HTTP security controls."""

from __future__ import annotations

import asyncio
from typing import cast

from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient
from pytest import MonkeyPatch
from spectral_api.security import (
    RateLimitMiddleware,
    RequestBodyLimitMiddleware,
    RequestLoggingMiddleware,
    RequestTimeoutMiddleware,
)
from spectral_common import get_settings

FIRST_USER = "50000000-0000-0000-0000-000000000005"
SECOND_USER = "60000000-0000-0000-0000-000000000006"


def client_for(app: FastAPI, user_id: str = FIRST_USER) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-Dev-User-Id": user_id},
    )


async def create_dataset(client: AsyncClient) -> str:
    project = (await client.post("/api/v1/projects", json={"name": "Security"})).json()
    dataset = (
        await client.post(
            f"/api/v1/projects/{project['id']}/datasets",
            json={"name": "Private data"},
        )
    ).json()
    return str(dataset["id"])


async def create_upload(
    client: AsyncClient, dataset_id: str, *, size: int = 100
) -> dict[str, object]:
    response = await client.post(
        f"/api/v1/datasets/{dataset_id}/uploads",
        json={"mode": "files", "files": [{"relative_path": "private.tif", "size_bytes": size}]},
    )
    assert response.status_code == 201
    return cast(dict[str, object], response.json())


async def test_upload_object_operations_are_all_owner_scoped(domain_app: FastAPI) -> None:
    async with client_for(domain_app) as owner:
        dataset_id = await create_dataset(owner)
        upload = await create_upload(owner, dataset_id, size=20 * 1024 * 1024)
    upload_id = str(upload["id"])
    upload_object = upload["objects"]
    assert isinstance(upload_object, list)
    object_id = str(upload_object[0]["id"])

    async with client_for(domain_app, SECOND_USER) as attacker:
        requests = [
            await attacker.get(f"/api/v1/datasets/{dataset_id}/uploads"),
            await attacker.post(
                f"/api/v1/uploads/{upload_id}/objects/{object_id}/parts",
                json={"part_numbers": [1]},
            ),
            await attacker.post(
                f"/api/v1/uploads/{upload_id}/objects/{object_id}/complete",
                json={"parts": []},
            ),
            await attacker.post(f"/api/v1/uploads/{upload_id}/objects/{object_id}/retry"),
            await attacker.post(f"/api/v1/uploads/{upload_id}/objects/{object_id}/failed"),
            await attacker.post(f"/api/v1/uploads/{upload_id}/cancel"),
            await attacker.delete(f"/api/v1/datasets/{dataset_id}"),
        ]
    assert [response.status_code for response in requests] == [404] * len(requests)


async def test_paths_are_rejected_or_sanitized_and_never_used_as_storage_keys(
    domain_app: FastAPI,
) -> None:
    async with client_for(domain_app) as client:
        dataset_id = await create_dataset(client)
        for unsafe in [
            "../escape.tif",
            "/absolute.tif",
            "C:\\windows.tif",
            "a/./b.tif",
            "a/\x01.tif",
        ]:
            response = await client.post(
                f"/api/v1/datasets/{dataset_id}/uploads",
                json={"mode": "files", "files": [{"relative_path": unsafe, "size_bytes": 100}]},
            )
            assert response.status_code == 422, unsafe

        response = await client.post(
            f"/api/v1/datasets/{dataset_id}/uploads",
            json={
                "mode": "files",
                "files": [
                    {
                        "relative_path": "plot/<img src=x onerror=alert(1)>.tif",
                        "size_bytes": 100,
                    }
                ],
            },
        )
    assert response.status_code == 201
    item = response.json()["objects"][0]
    assert item["client_relative_path"] == "plot/_img src_x onerror_alert(1)_.tif"
    assert item["filename"] == "_img src_x onerror_alert(1)_.tif"
    assert "img" not in str(item["upload_url"])


async def test_session_dataset_and_multipart_plan_limits(
    domain_app: FastAPI, monkeypatch: MonkeyPatch
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "upload_max_session_bytes", 150)
    monkeypatch.setattr(settings, "upload_max_dataset_bytes", 200)
    async with client_for(domain_app) as client:
        dataset_id = await create_dataset(client)
        too_large = await client.post(
            f"/api/v1/datasets/{dataset_id}/uploads",
            json={
                "mode": "files",
                "files": [
                    {"relative_path": "a.tif", "size_bytes": 100},
                    {"relative_path": "b.tif", "size_bytes": 100},
                ],
            },
        )
        assert too_large.status_code == 413

        first = await create_upload(client, dataset_id, size=120)
        quota = await client.post(
            f"/api/v1/datasets/{dataset_id}/uploads",
            json={"mode": "files", "files": [{"relative_path": "second.tif", "size_bytes": 100}]},
        )
        assert quota.status_code == 413

        monkeypatch.setattr(settings, "upload_max_session_bytes", 100 * 1024**2)
        monkeypatch.setattr(settings, "upload_max_dataset_bytes", 200 * 1024**2)
        multipart = await create_upload(client, dataset_id, size=20 * 1024 * 1024)
        item = multipart["objects"]
        assert isinstance(item, list)
        outside_plan = await client.post(
            f"/api/v1/uploads/{multipart['id']}/objects/{item[0]['id']}/parts",
            json={"part_numbers": [4]},
        )
        assert outside_plan.status_code == 422
        incomplete_completion = await client.post(
            f"/api/v1/uploads/{multipart['id']}/objects/{item[0]['id']}/complete",
            json={
                "parts": [
                    {"part_number": 1, "etag": "etag-1"},
                    {"part_number": 2, "etag": "etag-2"},
                ]
            },
        )
        assert incomplete_completion.status_code == 409
    assert first["total_bytes"] == 120


async def test_sql_and_script_payload_remains_inert_data(domain_app: FastAPI) -> None:
    payload = "'><script>alert(1)</script>'; DROP TABLE projects;--"
    async with client_for(domain_app) as client:
        created = await client.post(
            "/api/v1/projects",
            json={"name": payload, "description": payload},
        )
        assert created.status_code == 201
        assert created.json()["name"] == payload
        still_operational = await client.post("/api/v1/projects", json={"name": "Still here"})
        assert still_operational.status_code == 201
        listed = await client.get("/api/v1/projects")
    assert listed.status_code == 200
    assert len(listed.json()) == 2
    assert listed.headers["x-content-type-options"] == "nosniff"
    assert "frame-ancestors 'none'" in listed.headers["content-security-policy"]


async def test_cors_is_exact_origin_and_never_credentialed(domain_app: FastAPI) -> None:
    transport = ASGITransport(app=domain_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        trusted = await client.options(
            "/api/v1/projects",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "GET",
            },
        )
        hostile = await client.options(
            "/api/v1/projects",
            headers={
                "Origin": "https://evil.example",
                "Access-Control-Request-Method": "GET",
            },
        )
    assert trusted.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "access-control-allow-credentials" not in trusted.headers
    assert "access-control-allow-origin" not in hostile.headers


async def test_streamed_body_limit_and_rate_limit_return_413_and_429() -> None:
    app = FastAPI()
    app.add_middleware(RequestBodyLimitMiddleware, max_bytes=16)
    app.add_middleware(
        RateLimitMiddleware,
        general_limit=2,
        upload_limit=1,
        window_seconds=60,
    )

    @app.post("/echo")
    async def echo(request: Request) -> dict[str, int]:
        return {"size": len(await request.body())}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        oversized = await client.post("/echo", content=b"x" * 17)
        allowed = await client.post("/echo", content=b"ok")
        limited = await client.post("/echo", content=b"ok")
    assert oversized.status_code == 413
    assert allowed.status_code == 200
    assert limited.status_code == 429


async def test_request_id_is_propagated_and_unsafe_value_is_replaced() -> None:
    app = FastAPI()
    app.add_middleware(RequestLoggingMiddleware)

    @app.get("/probe")
    async def probe() -> dict[str, bool]:
        return {"ok": True}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        propagated = await client.get("/probe", headers={"X-Request-ID": "release-123"})
        replaced = await client.get(
            "/probe", headers={"X-Request-ID": "unsafe request id\r\ninjection"}
        )

    assert propagated.headers["x-request-id"] == "release-123"
    assert replaced.headers["x-request-id"] != "unsafe request id\r\ninjection"
    assert len(replaced.headers["x-request-id"]) == 36


async def test_control_plane_request_timeout_returns_504() -> None:
    app = FastAPI()
    app.add_middleware(RequestTimeoutMiddleware, timeout_seconds=0.01)

    @app.get("/slow")
    async def slow() -> dict[str, bool]:
        await asyncio.sleep(0.1)
        return {"ok": True}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/slow")

    assert response.status_code == 504
    assert response.json() == {"detail": "Request timed out"}
