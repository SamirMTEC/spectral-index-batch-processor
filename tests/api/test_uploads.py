"""Direct upload planning, recovery, scale, and ownership tests."""

from typing import Any
from uuid import UUID

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from spectral_api.domain.models import (
    BandAsset,
    BandAssetState,
    Capture,
    Dataset,
    DatasetState,
    UploadObject,
    UploadObjectState,
    UploadSession,
    UploadSessionState,
)

FIRST_USER = "30000000-0000-0000-0000-000000000003"
SECOND_USER = "40000000-0000-0000-0000-000000000004"


def client_for(app: FastAPI, user_id: str = FIRST_USER) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-Dev-User-Id": user_id},
    )


async def create_dataset(client: AsyncClient) -> str:
    project = (await client.post("/api/v1/projects", json={"name": "Ingestion"})).json()
    response = await client.post(
        f"/api/v1/projects/{project['id']}/datasets",
        json={"name": "Multispectral batch"},
    )
    return str(response.json()["id"])


async def test_valid_files_and_invalid_extension_are_isolated(domain_app: FastAPI) -> None:
    async with client_for(domain_app) as client:
        dataset_id = await create_dataset(client)
        response = await client.post(
            f"/api/v1/datasets/{dataset_id}/uploads",
            json={
                "mode": "files",
                "files": [
                    {"relative_path": "plot/B01.tif", "size_bytes": 100},
                    {"relative_path": "plot/context.jpg", "size_bytes": 100},
                    {"relative_path": "plot/readme.txt", "size_bytes": 20},
                ],
            },
        )
        assert response.status_code == 201
        objects = response.json()["objects"]
        assert objects[0]["role"] == "scientific"
        assert objects[0]["state"] == "uploading"
        assert objects[0]["method"] == "single"
        assert objects[1]["role"] == "auxiliary"
        assert objects[1]["state"] == "uploading"
        assert objects[2]["state"] == "invalid"
        assert objects[2]["failure_code"] == "invalid_extension"


async def test_interrupted_upload_can_retry_and_cancel(domain_app: FastAPI) -> None:
    async with client_for(domain_app) as client:
        dataset_id = await create_dataset(client)
        created = (
            await client.post(
                f"/api/v1/datasets/{dataset_id}/uploads",
                json={
                    "mode": "files",
                    "files": [{"relative_path": "broken.tif", "size_bytes": 100}],
                },
            )
        ).json()
        upload_id = created["id"]
        object_id = created["objects"][0]["id"]
        domain_app.state.resources.s3.raise_on_head = True
        interrupted = await client.post(
            f"/api/v1/uploads/{upload_id}/objects/{object_id}/complete", json={}
        )
        assert interrupted.status_code == 409

        domain_app.state.resources.s3.raise_on_head = False
        retried = await client.post(f"/api/v1/uploads/{upload_id}/objects/{object_id}/retry")
        assert retried.status_code == 200
        assert retried.json()["state"] == "uploading"

        cancelled = await client.post(f"/api/v1/uploads/{upload_id}/cancel")
        assert cancelled.status_code == 200
        assert cancelled.json()["state"] == "cancelled"
        assert cancelled.json()["objects"][0]["failure_code"] == "upload_cancelled"


async def test_duplicate_manifest_path_and_cross_owner_access(domain_app: FastAPI) -> None:
    async with client_for(domain_app) as first:
        dataset_id = await create_dataset(first)
        duplicate_path = await first.post(
            f"/api/v1/datasets/{dataset_id}/uploads",
            json={
                "mode": "directory",
                "files": [
                    {"relative_path": "a/B1.tif", "size_bytes": 100},
                    {"relative_path": "a/b1.tif", "size_bytes": 100},
                ],
            },
        )
        assert duplicate_path.status_code == 422

    async with client_for(domain_app, SECOND_USER) as second:
        denied = await second.post(
            f"/api/v1/datasets/{dataset_id}/uploads",
            json={
                "mode": "files",
                "files": [{"relative_path": "stolen.tif", "size_bytes": 100}],
            },
        )
        assert denied.status_code == 404


async def test_duplicate_checksum_is_rejected_before_transfer(domain_app: FastAPI) -> None:
    checksum = "a" * 64
    async with client_for(domain_app) as client:
        dataset_id = await create_dataset(client)
        async with domain_app.state.session_factory() as session:
            dataset = await session.get(Dataset, UUID(dataset_id))
            assert dataset is not None
            session.add(
                BandAsset(
                    project_id=dataset.project_id,
                    owner_id=dataset.owner_id,
                    dataset_id=dataset.id,
                    original_filename="existing.tif",
                    object_key=f"originals/{dataset.id}/existing.tif",
                    sha256=checksum,
                )
            )
            await session.commit()

        response = await client.post(
            f"/api/v1/datasets/{dataset_id}/uploads",
            json={
                "mode": "files",
                "files": [
                    {
                        "relative_path": "duplicate.tif",
                        "size_bytes": 100,
                        "sha256": checksum,
                    }
                ],
            },
        )
        assert response.status_code == 201
        duplicate = response.json()["objects"][0]
        assert duplicate["state"] == "invalid"
        assert duplicate["failure_code"] == "duplicate"
        assert duplicate["upload_url"] is None


async def test_large_dataset_manifest_is_planned_in_one_request(domain_app: FastAPI) -> None:
    async with client_for(domain_app) as client:
        dataset_id = await create_dataset(client)
        files: list[dict[str, Any]] = [
            {"relative_path": f"treatment-{index // 10}/capture-{index}.tif", "size_bytes": 100}
            for index in range(1_000)
        ]
        response = await client.post(
            f"/api/v1/datasets/{dataset_id}/uploads",
            json={"mode": "directory", "files": files},
        )
        assert response.status_code == 201
        assert response.json()["total_files"] == 1_000
        assert len(response.json()["objects"]) == 1_000
        assert all(UUID(item["id"]) for item in response.json()["objects"])


async def test_large_file_uses_batched_multipart_urls(domain_app: FastAPI) -> None:
    async with client_for(domain_app) as client:
        dataset_id = await create_dataset(client)
        created = (
            await client.post(
                f"/api/v1/datasets/{dataset_id}/uploads",
                json={
                    "mode": "files",
                    "files": [
                        {
                            "relative_path": "large.tif",
                            "size_bytes": 20 * 1024 * 1024,
                        }
                    ],
                },
            )
        ).json()
        item = created["objects"][0]
        assert item["method"] == "multipart"
        assert item["part_size"] == 8 * 1024 * 1024
        assert item["part_count"] == 3

        parts = await client.post(
            f"/api/v1/uploads/{created['id']}/objects/{item['id']}/parts",
            json={"part_numbers": [1, 2, 3]},
        )
        assert parts.status_code == 200
        assert [value["part_number"] for value in parts.json()["parts"]] == [1, 2, 3]


async def test_desktop_removes_and_restores_an_uploaded_band(
    domain_app: FastAPI, monkeypatch: Any
) -> None:
    monkeypatch.setenv("SPECTRAL_DESKTOP_MODE", "true")
    async with client_for(domain_app) as client:
        dataset_id = await create_dataset(client)
        created = (
            await client.post(
                f"/api/v1/datasets/{dataset_id}/uploads",
                json={
                    "mode": "files",
                    "files": [{"relative_path": "R1/DJI_0301.TIF", "size_bytes": 100}],
                },
            )
        ).json()
        object_id = created["objects"][0]["id"]
        async with domain_app.state.session_factory() as session:
            dataset = await session.get(Dataset, UUID(dataset_id))
            upload = await session.get(UploadSession, UUID(created["id"]))
            upload_object = await session.get(UploadObject, UUID(object_id))
            assert dataset and upload and upload_object
            dataset.state = DatasetState.NEEDS_REVIEW
            upload.state = UploadSessionState.COMPLETED
            upload_object.state = UploadObjectState.VALID
            capture = Capture(
                project_id=dataset.project_id,
                owner_id=dataset.owner_id,
                dataset_id=dataset.id,
                capture_key="DJI_030",
            )
            session.add(capture)
            await session.flush()
            asset = BandAsset(
                project_id=dataset.project_id,
                owner_id=dataset.owner_id,
                dataset_id=dataset.id,
                upload_object_id=upload_object.id,
                capture_id=capture.id,
                band_code="BLUE",
                original_filename="DJI_0301.TIF",
                object_key="originals/test/DJI_0301.TIF",
                sha256="b" * 64,
                state=BandAssetState.ACCEPTED,
            )
            session.add(asset)
            await session.commit()
            asset_id = asset.id
            capture_id = capture.id

        endpoint = f"/api/v1/uploads/{created['id']}/objects/{object_id}"
        assert len((await client.get(f"/api/v1/datasets/{dataset_id}/captures")).json()) == 1
        removed = await client.post(f"{endpoint}/archive")
        assert removed.status_code == 200
        assert removed.json()["metadata_json"]["desktop_removed_at"]
        assert (await client.get(f"/api/v1/datasets/{dataset_id}/captures")).json() == []
        async with domain_app.state.session_factory() as session:
            archived = await session.get(BandAsset, asset_id)
            assert archived and archived.state is BandAssetState.ARCHIVED
            assert archived.object_key == "originals/test/DJI_0301.TIF"
            replacement = BandAsset(
                project_id=archived.project_id,
                owner_id=archived.owner_id,
                dataset_id=archived.dataset_id,
                capture_id=capture_id,
                band_code="BLUE",
                original_filename="DJI_0301-replacement.TIF",
                object_key="originals/test/replacement.TIF",
                sha256="c" * 64,
                state=BandAssetState.ACCEPTED,
            )
            session.add(replacement)
            await session.commit()
            replacement_id = replacement.id
        assert (await client.post(f"{endpoint}/restore")).status_code == 409
        async with domain_app.state.session_factory() as session:
            replacement = await session.get(BandAsset, replacement_id)
            assert replacement
            replacement.state = BandAssetState.ARCHIVED
            await session.commit()
        restored = await client.post(f"{endpoint}/restore")
        assert restored.status_code == 200
        assert "desktop_removed_at" not in restored.json()["metadata_json"]
        assert len((await client.get(f"/api/v1/datasets/{dataset_id}/captures")).json()) == 1

    async with client_for(domain_app, SECOND_USER) as other:
        assert (await other.post(f"{endpoint}/archive")).status_code == 404
    monkeypatch.delenv("SPECTRAL_DESKTOP_MODE")
    async with client_for(domain_app) as client:
        assert (await client.post(f"{endpoint}/archive")).status_code == 404
