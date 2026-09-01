"""Authorization, idempotency, progress, and cancellation for processing jobs."""

import json
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
    ExportArtifact,
    ExportArtifactState,
    IndexResult,
    ProcessingItem,
    ProcessingItemState,
    ProcessingJob,
    ProcessingJobState,
    Replicate,
    ROIVersion,
    Statistics,
)
from sqlalchemy import select

FIRST_USER = "70000000-0000-0000-0000-000000000007"
SECOND_USER = "80000000-0000-0000-0000-000000000008"


def client_for(app: FastAPI, user_id: str = FIRST_USER) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-Dev-User-Id": user_id},
    )


async def seed_ready_capture(app: FastAPI, client: AsyncClient) -> tuple[str, str]:
    project = (await client.post("/api/v1/projects", json={"name": "Processing"})).json()
    dataset_id = (
        await client.post(f"/api/v1/projects/{project['id']}/datasets", json={"name": "Batch"})
    ).json()["id"]
    async with app.state.session_factory() as session:
        dataset = await session.get(Dataset, UUID(dataset_id))
        assert dataset is not None
        replicate = Replicate(
            project_id=dataset.project_id,
            owner_id=dataset.owner_id,
            dataset_id=dataset.id,
            label="Rep 1",
            normalized_key=f"rep-{uuid4()}",
        )
        session.add(replicate)
        await session.flush()
        capture = Capture(
            project_id=dataset.project_id,
            owner_id=dataset.owner_id,
            dataset_id=dataset.id,
            replicate_id=replicate.id,
            capture_key="capture-1",
            width=2,
            height=2,
            alignment_status=AlignmentStatus.COMPATIBLE,
        )
        session.add(capture)
        await session.flush()
        for position, code in enumerate(("BLUE", "GREEN", "RED", "NIR"), 1):
            session.add(
                BandAsset(
                    project_id=dataset.project_id,
                    owner_id=dataset.owner_id,
                    dataset_id=dataset.id,
                    capture_id=capture.id,
                    band_code=code,
                    original_filename=f"{code}.tif",
                    object_key=f"originals/{capture.id}/{code}.tif",
                    sha256=f"{position:064x}",
                    width=2,
                    height=2,
                    crs="EPSG:4326",
                    transform=[1, 0, 0, 0, -1, 2, 0, 0, 1],
                    role=AssetRole.SCIENTIFIC,
                    state=BandAssetState.ACCEPTED,
                )
            )
        await session.commit()
    opened = (await client.get(f"/api/v1/captures/{capture.id}")).json()
    red = next(item for item in opened["bands"] if item["band_code"] == "RED")
    roi = await client.post(
        f"/api/v1/captures/{capture.id}/rois",
        json={
            "name": "Leaf",
            "geometry_type": "rectangle",
            "normalized_coordinates": [[0, 0], [1, 0], [1, 1], [0, 1]],
            "reference_asset_id": red["id"],
        },
    )
    assert roi.status_code == 201
    return dataset_id, str(capture.id)


async def test_job_creation_is_idempotent_and_uses_real_item_count(domain_app: FastAPI) -> None:
    async with client_for(domain_app) as client:
        dataset_id, _ = await seed_ready_capture(domain_app, client)
        payload = {"indices": [{"name": "NDVI"}, {"name": "SAVI", "parameters": {"L": 0.5}}]}
        headers = {"Idempotency-Key": "same-request"}
        created = await client.post(
            f"/api/v1/datasets/{dataset_id}/processing-jobs", json=payload, headers=headers
        )
        repeated = await client.post(
            f"/api/v1/datasets/{dataset_id}/processing-jobs", json=payload, headers=headers
        )

        assert created.status_code == 201
        assert repeated.status_code == 201
        assert repeated.json()["id"] == created.json()["id"]
        assert created.json()["total_items"] == 2
        assert created.json()["processed_items"] == 0
        assert created.json()["progress"] == 0
        assert len(domain_app.state.resources.celery.tasks) == 2


async def test_job_ownership_and_cancellation(domain_app: FastAPI) -> None:
    async with client_for(domain_app) as first:
        dataset_id, _ = await seed_ready_capture(domain_app, first)
        created = await first.post(
            f"/api/v1/datasets/{dataset_id}/processing-jobs",
            json={"indices": [{"name": "NDVI"}]},
        )
        assert created.status_code == 201
        job_id = created.json()["id"]
        cancelled = await first.post(f"/api/v1/processing-jobs/{job_id}/cancel")
        assert cancelled.status_code == 200
        assert cancelled.json()["state"] == "cancelled"
        assert cancelled.json()["processed_items"] == 1
        assert cancelled.json()["skipped_items"] == 1

    async with client_for(domain_app, SECOND_USER) as second:
        assert (await second.get(f"/api/v1/processing-jobs/{job_id}")).status_code == 404
        assert (
            await second.get(f"/api/v1/datasets/{dataset_id}/processing-jobs")
        ).status_code == 404


async def test_missing_roi_is_a_skipped_item_not_a_failed_job(domain_app: FastAPI) -> None:
    async with client_for(domain_app) as client:
        project = (await client.post("/api/v1/projects", json={"name": "No ROI"})).json()
        dataset = (
            await client.post(
                f"/api/v1/projects/{project['id']}/datasets", json={"name": "Skipped"}
            )
        ).json()
        async with domain_app.state.session_factory() as session:
            session.add(
                Capture(
                    project_id=UUID(project["id"]),
                    owner_id=UUID(FIRST_USER),
                    dataset_id=UUID(dataset["id"]),
                    capture_key="without-roi",
                    alignment_status=AlignmentStatus.COMPATIBLE,
                )
            )
            await session.commit()
        response = await client.post(
            f"/api/v1/datasets/{dataset['id']}/processing-jobs",
            json={"indices": [{"name": "NDVI"}]},
        )
        body = response.json()
        assert response.status_code == 201
        assert body["state"] == "completed_with_errors"
        assert body["processed_items"] == 1
        assert body["items"][0]["state"] == "skipped"
        assert body["items"][0]["error_code"] == "missing_roi"


async def test_results_are_owner_scoped_and_expose_individual_statistics(
    domain_app: FastAPI,
) -> None:
    async with client_for(domain_app) as client:
        dataset_id, _ = await seed_ready_capture(domain_app, client)
        created = await client.post(
            f"/api/v1/datasets/{dataset_id}/processing-jobs",
            json={"indices": [{"name": "NDVI"}]},
        )
        job_id = UUID(created.json()["id"])
        async with domain_app.state.session_factory() as session:
            job = await session.get(ProcessingJob, job_id)
            item = await session.scalar(
                select(ProcessingItem).where(ProcessingItem.job_id == job_id)
            )
            dataset = await session.get(Dataset, UUID(dataset_id))
            assert job is not None and item is not None and dataset is not None
            job.state = ProcessingJobState.COMPLETED
            job.processed_items = job.completed_items = 1
            item.state = ProcessingItemState.COMPLETED
            result = IndexResult(
                project_id=dataset.project_id,
                owner_id=dataset.owner_id,
                dataset_id=dataset.id,
                processing_item_id=item.id,
                object_key="results/test/ndvi.tif",
                index_name="NDVI",
                width=2,
                height=2,
                valid_pixel_count=4,
                invalid_pixel_count=0,
            )
            session.add(result)
            await session.flush()
            result_id = result.id
            session.add(
                Statistics(
                    project_id=dataset.project_id,
                    owner_id=dataset.owner_id,
                    dataset_id=dataset.id,
                    index_result_id=result.id,
                    count=4,
                    mean=0.5,
                    median=0.5,
                    min=0.2,
                    max=0.8,
                    std=0.1,
                    p05=0.22,
                    p25=0.3,
                    p75=0.7,
                    p95=0.78,
                )
            )
            session.add(
                ExportArtifact(
                    project_id=dataset.project_id,
                    owner_id=dataset.owner_id,
                    dataset_id=dataset.id,
                    job_id=job.id,
                    processing_item_id=item.id,
                    artifact_type="geotiff",
                    state=ExportArtifactState.READY,
                    object_key="results/test/ndvi.tif",
                    size_bytes=1234,
                    checksum_sha256="a" * 64,
                )
            )
            await session.commit()

        response = await client.get(f"/api/v1/processing-jobs/{job_id}/results?sort=mean")
        body = response.json()
        assert response.status_code == 200
        assert body["overview"]["dataset_name"] == "Batch"
        assert body["overview"]["analyzed_pixels"] == 4
        assert body["table"]["rows"][0]["mean"] == 0.5
        assert body["table"]["rows"][0]["valid_pixels"] == 4
        assert body["files"][0]["checksum_sha256"] == "a" * 64
        assert "internal_traceback" not in str(body)

    async with client_for(domain_app, SECOND_USER) as other:
        assert (await other.get(f"/api/v1/processing-jobs/{job_id}/results")).status_code == 404
        assert (await other.get(f"/api/v1/index-results/{result_id}/preview")).status_code == 404


async def test_finished_job_can_request_idempotent_exports(domain_app: FastAPI) -> None:
    async with client_for(domain_app) as client:
        dataset_id, _ = await seed_ready_capture(domain_app, client)
        created = await client.post(
            f"/api/v1/datasets/{dataset_id}/processing-jobs",
            json={"indices": [{"name": "NDVI"}]},
        )
        job_id = UUID(created.json()["id"])
        async with domain_app.state.session_factory() as session:
            job = await session.get(ProcessingJob, job_id)
            assert job is not None
            job.state = ProcessingJobState.COMPLETED
            await session.commit()

        requested = await client.post(
            f"/api/v1/processing-jobs/{job_id}/exports",
            json={"formats": ["csv", "complete"]},
        )
        repeated = await client.post(
            f"/api/v1/processing-jobs/{job_id}/exports",
            json={"formats": ["csv", "complete"]},
        )
        assert requested.status_code == 202
        assert {artifact["artifact_type"] for artifact in requested.json()} == {"csv", "bundle_zip"}
        assert {artifact["artifact_type"] for artifact in repeated.json()} == {"csv", "bundle_zip"}
        assert len(domain_app.state.resources.celery.tasks) == 2
        assert domain_app.state.resources.celery.tasks[-1]["name"] == "spectral.export.generate"

    async with client_for(domain_app, SECOND_USER) as other:
        assert (await other.get(f"/api/v1/processing-jobs/{job_id}/exports")).status_code == 404


async def test_job_freezes_formula_roi_band_mapping_and_input_checksums(
    domain_app: FastAPI,
) -> None:
    async with client_for(domain_app) as client:
        dataset_id, _ = await seed_ready_capture(domain_app, client)
        created = await client.post(
            f"/api/v1/datasets/{dataset_id}/processing-jobs",
            json={"indices": [{"name": "NDVI"}, {"name": "SAVI", "parameters": {"L": 0.5}}]},
        )
        assert created.status_code == 201
        job_id = UUID(created.json()["id"])
        async with domain_app.state.session_factory() as session:
            job = await session.get(ProcessingJob, job_id)
            items = list(
                await session.scalars(select(ProcessingItem).where(ProcessingItem.job_id == job_id))
            )
            assert job is not None
            assert job.snapshot_schema_version == "1.0.0"
            assert len(job.snapshot_checksum) == 64
            assert job.reproducibility_snapshot["software_version"] == "0.1.0"
            assert job.reproducibility_snapshot["dataset"]["radiometry_mode"] == "unknown"
            assert {
                value["formula_version"] for value in job.reproducibility_snapshot["indices"]
            } == {"1.0.0"}
            ndvi = next(item for item in items if item.index_name == "NDVI")
            assert ndvi.formula_version == "1.0.0"
            assert ndvi.formula_expression == "(NIR - RED) / (NIR + RED)"
            assert {asset["band"] for asset in ndvi.input_snapshot} == {"NIR", "RED"}
            assert all(len(str(asset["checksum_sha256"])) == 64 for asset in ndvi.input_snapshot)
            frozen = json.dumps(job.reproducibility_snapshot, sort_keys=True)
            asset = await session.scalar(
                select(BandAsset).where(BandAsset.capture_id == ndvi.capture_id)
            )
            roi_version = await session.get(ROIVersion, ndvi.roi_version_id)
            assert asset is not None and roi_version is not None
            asset.original_filename = "changed-after-submission.tif"
            roi_version.pixel_coordinates = [[0, 0], [1, 0], [0, 1], [0, 0]]
            await session.commit()
            assert json.dumps(job.reproducibility_snapshot, sort_keys=True) == frozen


async def test_recipe_is_owner_scoped_reusable_and_reprocess_creates_child_job(
    domain_app: FastAPI,
) -> None:
    async with client_for(domain_app) as client:
        dataset_id, _ = await seed_ready_capture(domain_app, client)
        recipe_response = await client.post(
            "/api/v1/processing-recipes",
            json={
                "name": "Vegetação padrão",
                "indices": [
                    {"name": "NDVI"},
                    {"name": "GNDVI"},
                    {"name": "SAVI", "parameters": {"L": 0.5}},
                ],
                "statistics": ["mean", "median", "std", "p05", "p25", "p75", "p95"],
            },
        )
        assert recipe_response.status_code == 201
        recipe = recipe_response.json()
        assert {value["formula_version"] for value in recipe["indices"]} == {"1.0.0"}
        created = await client.post(
            f"/api/v1/datasets/{dataset_id}/processing-jobs",
            json={"recipe_id": recipe["id"]},
        )
        assert created.status_code == 201
        body = created.json()
        assert body["recipe_name"] == "Vegetação padrão"
        assert set(body["indices"]) == {"NDVI", "GNDVI", "SAVI"}
        source_id = UUID(body["id"])
        async with domain_app.state.session_factory() as session:
            source = await session.get(ProcessingJob, source_id)
            assert source is not None
            source.state = ProcessingJobState.COMPLETED
            source.finished_at = source.created_at
            for item in await session.scalars(
                select(ProcessingItem).where(ProcessingItem.job_id == source.id)
            ):
                item.state = ProcessingItemState.COMPLETED
            await session.commit()
        reprocessed = await client.post(f"/api/v1/processing-jobs/{source_id}/reprocess")
        assert reprocessed.status_code == 200
        child = reprocessed.json()
        assert child["id"] != str(source_id)
        assert child["parent_job_id"] == str(source_id)
        assert child["snapshot_checksum"] == body["snapshot_checksum"]
        assert {item["id"] for item in child["items"]}.isdisjoint(
            {item["id"] for item in body["items"]}
        )

    async with client_for(domain_app, SECOND_USER) as other:
        assert (await other.get("/api/v1/processing-recipes")).json() == []
        assert (
            await other.post(
                f"/api/v1/datasets/{dataset_id}/processing-jobs",
                json={"recipe_id": recipe["id"]},
            )
        ).status_code == 404
