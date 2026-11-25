"""Authorization, idempotency, progress, and cancellation for processing jobs."""

import json
from uuid import UUID, uuid4

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from spectral_api.domain.models import (
    AlignmentStatus,
    AnalysisScope,
    AssetRole,
    BandAsset,
    BandAssetState,
    CalibrationRun,
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
        for position, code in enumerate(("BLUE", "GREEN", "RED", "RED_EDGE", "NIR"), 1):
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


async def test_calibrated_index_job_freezes_calibrated_tiffs_and_rejects_missing_bands(
    domain_app: FastAPI,
) -> None:
    async with client_for(domain_app) as client:
        dataset_id, capture_id = await seed_ready_capture(domain_app, client)
        async with domain_app.state.session_factory() as session:
            dataset = await session.get(Dataset, UUID(dataset_id))
            assert dataset is not None
            run = CalibrationRun(
                id=uuid4(),
                owner_id=dataset.owner_id,
                project_id=dataset.project_id,
                dataset_id=dataset.id,
                capture_id=UUID(capture_id),
                panel_id=uuid4(),
                state="completed",
                method="proportional",
                input_mode="p4_signal",
                config_json={},
                manifest_json={},
                result_json={
                    "reference_captures": [{
                        "capture_id": capture_id,
                        "bands": {
                            "RED": {"output": "RED_calibrated.tif"},
                            "NIR": {"output": "NIR_calibrated.tif"},
                        },
                    }],
                    "targets": [],
                },
                output_key="calibration/results/test/calibration-export.zip",
            )
            session.add(run)
            await session.commit()
            run_id = str(run.id)
        response = await client.post(
            f"/api/v1/datasets/{dataset_id}/processing-jobs",
            json={
                "indices": [{"name": "NDVI"}],
                "analysis_scopes": ["full_image"],
                "calibration_run_id": run_id,
            },
        )
        assert response.status_code == 201, response.text
        async with domain_app.state.session_factory() as session:
            item = await session.scalar(
                select(ProcessingItem).where(ProcessingItem.job_id == UUID(response.json()["id"]))
            )
            assert item is not None
            assert {band["calibration_member"] for band in item.input_snapshot} == {
                "RED_calibrated.tif", "NIR_calibrated.tif"
            }
            assert all(
                band["calibration_archive_key"] == "calibration/results/test/calibration-export.zip"
                for band in item.input_snapshot
            )
        missing = await client.post(
            f"/api/v1/datasets/{dataset_id}/processing-jobs",
            json={
                "indices": [{"name": "VARI"}],
                "analysis_scopes": ["full_image"],
                "calibration_run_id": run_id,
            },
        )
        assert missing.status_code == 409
        async with domain_app.state.session_factory() as session:
            stored = await session.get(CalibrationRun, UUID(run_id))
            assert stored is not None and isinstance(stored.result_json, dict)
            result = dict(stored.result_json)
            references = list(result["reference_captures"])
            first = dict(references[0])
            first["bands"] = {
                code: {
                    "output": f"{code}_calibrated.tif",
                    "object_key": f"calibration/results/test/bands/{code}.tif",
                    "checksum_sha256": f"{number:064x}",
                }
                for number, code in enumerate(("RED", "NIR"), 1)
            }
            result["reference_captures"] = [first]
            stored.result_json = result
            await session.commit()
        direct = await client.post(
            f"/api/v1/datasets/{dataset_id}/processing-jobs",
            json={
                "indices": [{"name": "NDVI"}],
                "analysis_scopes": ["roi"],
                "calibration_run_id": run_id,
            },
        )
        assert direct.status_code == 201, direct.text
        async with domain_app.state.session_factory() as session:
            item = await session.scalar(
                select(ProcessingItem).where(ProcessingItem.job_id == UUID(direct.json()["id"]))
            )
            assert item is not None
            assert all(
                band["object_key"].startswith("calibration/results/test/bands/")
                for band in item.input_snapshot
            )
            assert all("calibration_archive_key" not in band for band in item.input_snapshot)


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
        assert created.json()["total_items"] == 4
        assert created.json()["processed_items"] == 0
        assert created.json()["progress"] == 0
        assert {item["analysis_scope"] for item in created.json()["items"]} == {
            "roi",
            "full_image",
        }
        assert len(domain_app.state.resources.celery.tasks) == 4


async def test_spectral_index_catalog_is_complete_versioned_and_five_band_only(
    domain_app: FastAPI,
) -> None:
    async with client_for(domain_app) as client:
        response = await client.get("/api/v1/spectral-indices")

    assert response.status_code == 200
    catalog = response.json()
    assert len(catalog) == 39
    assert {item["index"] for item in catalog} >= {
        "NDVI",
        "NDRE",
        "EVI",
        "MCARI2",
        "PSRI",
    }
    assert all(
        set(item["required_bands"]) <= {"BLUE", "GREEN", "RED", "RED_EDGE", "NIR"}
        for item in catalog
    )
    rvi = next(item for item in catalog if item["index"] == "RVI")
    exg = next(item for item in catalog if item["index"] == "EXG")
    assert set(rvi["aliases"]) == {"Simple Ratio", "SR"}
    assert exg["formula_version"] == "2.0.0"
    assert exg["default_parameters"] == {"reflectance_scale": 1.0}
    assert {item["index"] for item in catalog if item["approximate"]} == {
        "ARI",
        "MARI",
        "MCARI",
        "PSRI",
        "TGI",
    }


async def test_alias_is_canonicalized_once_and_reflectance_scale_is_frozen(
    domain_app: FastAPI,
) -> None:
    async with client_for(domain_app) as client:
        dataset_id, _ = await seed_ready_capture(domain_app, client)
        captures = await client.get(f"/api/v1/datasets/{dataset_id}/captures")
        created = await client.post(
            f"/api/v1/datasets/{dataset_id}/processing-jobs",
            json={
                "indices": [
                    {
                        "name": "Simple Ratio",
                        "parameters": {"reflectance_scale": 10_000},
                    }
                ],
                "analysis_scopes": ["full_image"],
            },
        )
        duplicate = await client.post(
            f"/api/v1/datasets/{dataset_id}/processing-jobs",
            json={"indices": [{"name": "RVI"}, {"name": "SR"}]},
        )

    assert captures.status_code == 200
    assert set(captures.json()[0]["band_codes"]) == {
        "BLUE",
        "GREEN",
        "RED",
        "RED_EDGE",
        "NIR",
    }
    assert created.status_code == 201
    assert created.json()["indices"] == ["RVI"]
    assert created.json()["total_items"] == 1
    assert duplicate.status_code == 422
    async with domain_app.state.session_factory() as session:
        item = await session.scalar(
            select(ProcessingItem).where(ProcessingItem.job_id == UUID(created.json()["id"]))
        )
        assert item is not None
        assert item.index_name == "RVI"
        assert item.parameters_snapshot == {"reflectance_scale": 10_000.0}


async def test_job_creation_respects_selected_analysis_scopes(domain_app: FastAPI) -> None:
    async with client_for(domain_app) as client:
        dataset_id, _ = await seed_ready_capture(domain_app, client)
        created = await client.post(
            f"/api/v1/datasets/{dataset_id}/processing-jobs",
            json={
                "indices": [{"name": "NDVI"}],
                "analysis_scopes": ["full_image"],
            },
        )

        assert created.status_code == 201
        body = created.json()
        assert body["total_items"] == 1
        assert body["processed_items"] == 0
        assert [item["analysis_scope"] for item in body["items"]] == ["full_image"]
        assert len(domain_app.state.resources.celery.tasks) == 1


async def test_job_creation_rejects_empty_or_duplicate_analysis_scopes(
    domain_app: FastAPI,
) -> None:
    async with client_for(domain_app) as client:
        dataset_id, _ = await seed_ready_capture(domain_app, client)
        endpoint = f"/api/v1/datasets/{dataset_id}/processing-jobs"

        empty = await client.post(
            endpoint,
            json={"indices": [{"name": "NDVI"}], "analysis_scopes": []},
        )
        duplicate = await client.post(
            endpoint,
            json={
                "indices": [{"name": "NDVI"}],
                "analysis_scopes": ["roi", "roi"],
            },
        )

        assert empty.status_code == 422
        assert duplicate.status_code == 422


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
        assert cancelled.json()["processed_items"] == 2
        assert cancelled.json()["skipped_items"] == 2

    async with client_for(domain_app, SECOND_USER) as second:
        assert (await second.get(f"/api/v1/processing-jobs/{job_id}")).status_code == 404
        assert (
            await second.get(f"/api/v1/datasets/{dataset_id}/processing-jobs")
        ).status_code == 404


async def test_missing_roi_skips_crop_but_keeps_full_image_runnable(domain_app: FastAPI) -> None:
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
        assert body["state"] == "queued"
        assert body["processed_items"] == 1
        assert body["total_items"] == 2
        roi_item = next(item for item in body["items"] if item["analysis_scope"] == "roi")
        full_item = next(item for item in body["items"] if item["analysis_scope"] == "full_image")
        assert roi_item["state"] == "skipped"
        assert roi_item["error_code"] == "missing_roi"
        assert full_item["state"] == "pending"


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
                select(ProcessingItem).where(
                    ProcessingItem.job_id == job_id,
                    ProcessingItem.analysis_scope == AnalysisScope.ROI,
                )
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
                analysis_scope=AnalysisScope.ROI,
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
        assert body["table"]["rows"][0]["analysis_scope"] == "roi"
        assert body["overview"]["analyzed_pixels_roi"] == 4
        assert body["overview"]["analyzed_pixels_full_image"] == 0
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
            json={
                "formats": [
                    "csv",
                    "summary_xlsx",
                    "rasters_roi",
                    "rasters_full_image",
                    "complete",
                ]
            },
        )
        repeated = await client.post(
            f"/api/v1/processing-jobs/{job_id}/exports",
            json={
                "formats": [
                    "csv",
                    "summary_xlsx",
                    "rasters_roi",
                    "rasters_full_image",
                    "complete",
                ]
            },
        )
        assert requested.status_code == 202
        assert {artifact["artifact_type"] for artifact in requested.json()} == {
            "csv",
            "summary_xlsx",
            "roi_rasters_zip",
            "full_image_rasters_zip",
            "bundle_zip",
        }
        assert {artifact["artifact_type"] for artifact in repeated.json()} == {
            "csv",
            "summary_xlsx",
            "roi_rasters_zip",
            "full_image_rasters_zip",
            "bundle_zip",
        }
        assert len(domain_app.state.resources.celery.tasks) == 3
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
            ndvi = next(
                item
                for item in items
                if item.index_name == "NDVI" and item.analysis_scope is AnalysisScope.ROI
            )
            full_ndvi = next(
                item
                for item in items
                if item.index_name == "NDVI" and item.analysis_scope is AnalysisScope.FULL_IMAGE
            )
            assert ndvi.formula_version == "1.0.0"
            assert ndvi.formula_expression == "(NIR - RED) / (NIR + RED)"
            assert {asset["band"] for asset in ndvi.input_snapshot} == {"NIR", "RED"}
            assert all(len(str(asset["checksum_sha256"])) == 64 for asset in ndvi.input_snapshot)
            assert full_ndvi.roi_version_id is None
            assert full_ndvi.roi_snapshot == {"scope": "full_image", "roi_applied": False}
            assert job.configuration_snapshot["analysis_scopes"] == ["roi", "full_image"]
            assert job.reproducibility_snapshot["analysis_scopes"] == ["roi", "full_image"]
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
                    {"name": "CIGREEN"},
                    {"name": "CIRED_EDGE"},
                    {"name": "NDRE"},
                    {"name": "MCARI"},
                    {"name": "VARI"},
                    {"name": "EXG"},
                ],
                "statistics": ["mean", "median", "std", "p05", "p25", "p75", "p95"],
            },
        )
        assert recipe_response.status_code == 201
        recipe = recipe_response.json()
        versions = {value["index"]: value["formula_version"] for value in recipe["indices"]}
        assert versions["EXG"] == "2.0.0"
        assert set(versions.values()) == {"1.0.0", "2.0.0"}
        created = await client.post(
            f"/api/v1/datasets/{dataset_id}/processing-jobs",
            json={"recipe_id": recipe["id"]},
        )
        assert created.status_code == 201
        body = created.json()
        assert body["recipe_name"] == "Vegetação padrão"
        assert set(body["indices"]) == {
            "NDVI",
            "GNDVI",
            "SAVI",
            "CIGREEN",
            "CIRED_EDGE",
            "NDRE",
            "MCARI",
            "VARI",
            "EXG",
        }
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
