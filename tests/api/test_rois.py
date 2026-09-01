"""Capture-grid compatibility, ROI versioning, and ownership tests."""

from uuid import UUID, uuid4

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from spectral_api.domain.models import (
    AlignmentStatus,
    AssetRole,
    BandAsset,
    BandAssetState,
    Capture,
    Dataset,
    Replicate,
    ROIVersion,
)
from sqlalchemy import func, select

FIRST_USER = "50000000-0000-0000-0000-000000000005"
SECOND_USER = "60000000-0000-0000-0000-000000000006"


def client_for(app: FastAPI, user_id: str = FIRST_USER) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-Dev-User-Id": user_id},
    )


async def seed_capture(
    app: FastAPI, client: AsyncClient, *, incompatible_nir: bool = False
) -> tuple[str, str]:
    project = (await client.post("/api/v1/projects", json={"name": "ROI project"})).json()
    dataset_id = (
        await client.post(
            f"/api/v1/projects/{project['id']}/datasets",
            json={"name": "Aligned capture"},
        )
    ).json()["id"]
    async with app.state.session_factory() as session:
        dataset = await session.get(Dataset, UUID(dataset_id))
        assert dataset is not None
        replicate = Replicate(
            project_id=dataset.project_id,
            owner_id=dataset.owner_id,
            dataset_id=dataset.id,
            label="Rep 01",
            normalized_key=f"rep-{uuid4()}",
        )
        session.add(replicate)
        await session.flush()
        capture = Capture(
            project_id=dataset.project_id,
            owner_id=dataset.owner_id,
            dataset_id=dataset.id,
            replicate_id=replicate.id,
            capture_key="capture-01",
            alignment_status=AlignmentStatus.COMPATIBLE,
        )
        session.add(capture)
        await session.flush()
        for index, code in enumerate(("RED", "NIR")):
            width = 800 if incompatible_nir and code == "NIR" else 1000
            session.add(
                BandAsset(
                    project_id=dataset.project_id,
                    owner_id=dataset.owner_id,
                    dataset_id=dataset.id,
                    capture_id=capture.id,
                    band_code=code,
                    original_filename=f"{code.lower()}.tif",
                    object_key=f"originals/{capture.id}/{code}.tif",
                    preview_object_key=f"previews/{capture.id}/{code}.png",
                    sha256=f"{index + 1:064x}",
                    width=width,
                    height=600,
                    crs="EPSG:31984",
                    transform=[1, 0, 100, 0, -1, 200, 0, 0, 1],
                    role=AssetRole.SCIENTIFIC,
                    state=BandAssetState.ACCEPTED,
                    approximate_valid_fraction=0.8,
                )
            )
        await session.commit()
        return str(capture.id), dataset_id


def rectangle(reference_asset_id: str, expected_version: int | None = None) -> dict[str, object]:
    payload: dict[str, object] = {
        "name": "Central canopy",
        "geometry_type": "rectangle",
        "normalized_coordinates": [
            [0.1, 0.2],
            [0.4, 0.2],
            [0.4, 0.7],
            [0.1, 0.7],
            [0.1, 0.2],
        ],
        "reference_asset_id": reference_asset_id,
    }
    if expected_version is not None:
        payload["expected_version"] = expected_version
    return payload


async def test_roi_uses_original_grid_and_creates_immutable_versions(
    domain_app: FastAPI,
) -> None:
    async with client_for(domain_app) as client:
        capture_id, _ = await seed_capture(domain_app, client)
        opened = await client.get(f"/api/v1/captures/{capture_id}")
        assert opened.status_code == 200
        capture = opened.json()
        assert capture["replicate_label"] == "Rep 01"
        assert all(
            band["preview_url"].startswith("https://storage.test") for band in capture["bands"]
        )
        reference_id = next(band["id"] for band in capture["bands"] if band["band_code"] == "RED")

        created = await client.post(
            f"/api/v1/captures/{capture_id}/rois",
            json=rectangle(reference_id),
        )
        assert created.status_code == 201
        roi = created.json()
        version = roi["current_version"]
        assert version["reference_width"] == 1000
        assert version["reference_height"] == 600
        assert version["pixel_coordinates"][0] == [100.0, 120.0]
        assert version["area_pixels"] == 90_000
        assert version["approximate_valid_pixels"] == 72_000
        assert version["spatial_coordinates"][0] == [200.0, 80.0]

        changed_payload = rectangle(reference_id, expected_version=1)
        changed_payload["normalized_coordinates"] = [
            [0.2, 0.2],
            [0.5, 0.2],
            [0.5, 0.5],
            [0.2, 0.5],
        ]
        updated = await client.post(f"/api/v1/rois/{roi['id']}/versions", json=changed_payload)
        assert updated.status_code == 200
        assert updated.json()["current_version"]["version_number"] == 2

        stale = await client.post(f"/api/v1/rois/{roi['id']}/versions", json=changed_payload)
        assert stale.status_code == 409
        async with domain_app.state.session_factory() as session:
            count = await session.scalar(
                select(func.count(ROIVersion.id)).where(ROIVersion.roi_id == UUID(roi["id"]))
            )
            assert count == 2

    async with client_for(domain_app, SECOND_USER) as second:
        assert (await second.get(f"/api/v1/captures/{capture_id}/rois")).status_code == 404
        assert (
            await second.post(f"/api/v1/rois/{roi['id']}/versions", json=changed_payload)
        ).status_code == 404
        assert (await second.delete(f"/api/v1/rois/{roi['id']}")).status_code == 404


async def test_roi_blocks_incompatible_band_and_cross_owner_access(domain_app: FastAPI) -> None:
    async with client_for(domain_app) as first:
        capture_id, dataset_id = await seed_capture(domain_app, first, incompatible_nir=True)
        capture = (await first.get(f"/api/v1/captures/{capture_id}")).json()
        red_id = next(band["id"] for band in capture["bands"] if band["band_code"] == "RED")
        blocked = await first.post(f"/api/v1/captures/{capture_id}/rois", json=rectangle(red_id))
        assert blocked.status_code == 409
        assert "Dimensões incompatíveis" in blocked.json()["detail"]

    async with client_for(domain_app, SECOND_USER) as second:
        assert (await second.get(f"/api/v1/captures/{capture_id}")).status_code == 404
        assert (await second.get(f"/api/v1/datasets/{dataset_id}/captures")).status_code == 404
        denied = await second.post(f"/api/v1/captures/{capture_id}/rois", json=rectangle(red_id))
        assert denied.status_code == 404


async def test_self_intersecting_polygon_is_rejected(domain_app: FastAPI) -> None:
    async with client_for(domain_app) as client:
        capture_id, _ = await seed_capture(domain_app, client)
        capture = (await client.get(f"/api/v1/captures/{capture_id}")).json()
        reference_id = capture["bands"][0]["id"]
        response = await client.post(
            f"/api/v1/captures/{capture_id}/rois",
            json={
                "name": "Invalid bow tie",
                "geometry_type": "polygon",
                "normalized_coordinates": [
                    [0.1, 0.1],
                    [0.9, 0.9],
                    [0.1, 0.9],
                    [0.9, 0.1],
                ],
                "reference_asset_id": reference_id,
            },
        )
        assert response.status_code == 422
        assert "intersect" in response.json()["detail"]
