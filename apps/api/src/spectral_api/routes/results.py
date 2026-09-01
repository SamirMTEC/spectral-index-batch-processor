"""Owner-scoped result browsing and non-scientific preview rendering."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Annotated, Any
from uuid import UUID

import numpy as np
import rasterio
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from PIL import Image
from rasterio.enums import Resampling
from spectral_common import get_settings
from sqlalchemy import false, select
from sqlalchemy.ext.asyncio import AsyncSession

from spectral_api.dependencies import CurrentUser, get_session
from spectral_api.domain.models import (
    AssetRole,
    BandAsset,
    BandAssetState,
    Capture,
    Dataset,
    ExportArtifact,
    IndexResult,
    ProcessingItem,
    ProcessingItemState,
    ProcessingJob,
    ProcessingRecipe,
    Replicate,
    Statistics,
    Treatment,
)
from spectral_api.schemas.results import (
    ProcessingError,
    ProcessingResults,
    ReproducibilityInput,
    ReproducibilityManifest,
    ResultFile,
    ResultImage,
    ResultOverview,
    ResultSort,
    ResultTable,
    ResultTableRow,
    TreatmentSummary,
)

router = APIRouter(tags=["results"])
Session = Annotated[AsyncSession, Depends(get_session)]


async def _owned_job(session: AsyncSession, job_id: UUID, owner_id: UUID) -> ProcessingJob:
    job = await session.scalar(
        select(ProcessingJob).where(ProcessingJob.id == job_id, ProcessingJob.owner_id == owner_id)
    )
    if job is None:
        raise HTTPException(status_code=404, detail="Processing job not found")
    return job


async def _presigned_download(request: Request, key: str | None, version: str | None) -> str | None:
    if key is None:
        return None
    resources = getattr(request.app.state, "resources", None)
    if resources is None:
        raise RuntimeError("Application resources are not initialized")
    params: dict[str, Any] = {"Bucket": get_settings().s3_bucket, "Key": key}
    if version:
        params["VersionId"] = version
    return await run_in_threadpool(
        resources.s3_presign.generate_presigned_url,
        "get_object",
        Params=params,
        ExpiresIn=get_settings().upload_url_expiry_seconds,
    )


def _recommendation(code: str | None) -> str:
    return {
        "missing_band": "Revise a associação de bandas e inclua a banda exigida pelo índice.",
        "incompatible_dimensions": "Use bandas com a mesma largura, altura e grade espacial.",
        "spatial_mismatch": "Alinhe CRS e transformação das bandas antes de processar.",
        "missing_roi": "Desenhe e confirme uma ROI para esta repetição e repita o item.",
        "invalid_parameters": "Revise os parâmetros configurados para o índice.",
        "job_cancelled": "Inicie um novo lote se desejar retomar o processamento.",
        "queue_unavailable": (
            "Verifique os workers e repita o item quando a fila estiver disponível."
        ),
        "processing_failed": (
            "Verifique o TIFF de origem e tente novamente; contate o suporte se persistir."
        ),
    }.get(code or "", "Revise o arquivo de origem e tente repetir este item.")


def _sort_value(row: ResultTableRow, sort: ResultSort) -> str | float | int:
    if sort == "treatment":
        return row.treatment or ""
    if sort == "repetition":
        return row.repetition or ""
    if sort == "index":
        return row.index_name
    if sort == "mean":
        return row.mean if row.mean is not None else float("-inf")
    return row.valid_pixels


async def _results_payload(
    request: Request,
    session: AsyncSession,
    job: ProcessingJob,
    owner_id: UUID,
    *,
    query: str | None,
    treatment_filter: str | None,
    index_filter: str | None,
    sort: ResultSort,
    descending: bool,
    offset: int,
    limit: int,
) -> ProcessingResults:
    dataset = await session.scalar(
        select(Dataset).where(Dataset.id == job.dataset_id, Dataset.owner_id == owner_id)
    )
    if dataset is None:
        raise HTTPException(status_code=404, detail="Dataset not found")

    items = list(
        await session.scalars(
            select(ProcessingItem)
            .where(ProcessingItem.job_id == job.id, ProcessingItem.owner_id == owner_id)
            .order_by(ProcessingItem.created_at, ProcessingItem.index_name)
        )
    )
    item_ids = [item.id for item in items]
    results = list(
        await session.scalars(
            select(IndexResult).where(IndexResult.processing_item_id.in_(item_ids))
            if item_ids
            else select(IndexResult).where(false())
        )
    )
    result_by_item = {result.processing_item_id: result for result in results}
    statistics = list(
        await session.scalars(
            select(Statistics).where(
                Statistics.index_result_id.in_([result.id for result in results])
            )
            if results
            else select(Statistics).where(false())
        )
    )
    statistics_by_result = {statistic.index_result_id: statistic for statistic in statistics}

    captures = list(
        await session.scalars(
            select(Capture).where(
                Capture.id.in_([item.capture_id for item in items if item.capture_id])
            )
            if any(item.capture_id for item in items)
            else select(Capture).where(false())
        )
    )
    capture_by_id = {capture.id: capture for capture in captures}
    replicates = list(
        await session.scalars(
            select(Replicate).where(
                Replicate.id.in_(
                    [capture.replicate_id for capture in captures if capture.replicate_id]
                )
            )
            if any(capture.replicate_id for capture in captures)
            else select(Replicate).where(false())
        )
    )
    replicate_by_id = {replicate.id: replicate for replicate in replicates}
    treatments = list(
        await session.scalars(
            select(Treatment).where(
                Treatment.dataset_id == dataset.id, Treatment.owner_id == owner_id
            )
        )
    )
    treatment_by_id = {treatment.id: treatment for treatment in treatments}
    asset_rows = list(
        await session.scalars(
            select(BandAsset).where(
                BandAsset.capture_id.in_([capture.id for capture in captures]),
                BandAsset.owner_id == owner_id,
                BandAsset.role == AssetRole.SCIENTIFIC,
                BandAsset.state == BandAssetState.ACCEPTED,
            )
            if captures
            else select(BandAsset).where(false())
        )
    )
    assets_by_capture: dict[UUID, list[BandAsset]] = defaultdict(list)
    for asset in asset_rows:
        if asset.capture_id:
            assets_by_capture[asset.capture_id].append(asset)

    def identity(item: ProcessingItem) -> tuple[str | None, str | None, Capture | None]:
        capture = capture_by_id.get(item.capture_id) if item.capture_id else None
        replicate = (
            replicate_by_id.get(capture.replicate_id) if capture and capture.replicate_id else None
        )
        treatment_id = (capture.treatment_id if capture else None) or (
            replicate.treatment_id if replicate else None
        )
        treatment = treatment_by_id.get(treatment_id) if treatment_id else None
        return (
            treatment.name if treatment else None,
            replicate.label if replicate else None,
            capture,
        )

    rows: list[ResultTableRow] = []
    images: list[ResultImage] = []
    reproducibility_inputs: list[ReproducibilityInput] = []
    for item in items:
        result = result_by_item.get(item.id)
        treatment_name, repetition, capture = identity(item)
        capture_assets = assets_by_capture.get(capture.id, []) if capture else []
        source_bands = (
            [
                {
                    "band_code": str(asset.get("band") or "") or None,
                    "filename": str(asset.get("filename") or "") or None,
                    "sha256": str(asset.get("checksum_sha256") or "") or None,
                    "object_version_id": (
                        str(asset.get("object_version_id"))
                        if asset.get("object_version_id")
                        else None
                    ),
                }
                for asset in item.input_snapshot
            ]
            if item.input_snapshot
            else [
                {
                    "band_code": asset.band_code,
                    "filename": asset.original_filename,
                    "sha256": asset.sha256,
                    "object_version_id": asset.object_version_id,
                }
                for asset in capture_assets
            ]
        )
        reproducibility_inputs.append(
            ReproducibilityInput(
                item_id=item.id,
                capture=capture.capture_key if capture else item.error_capture,
                repetition=repetition,
                index_name=item.index_name,
                formula_version=item.formula_version,
                formula=item.formula_expression,
                parameters=item.parameters_snapshot,
                roi_version_id=item.roi_version_id,
                source_bands=source_bands,
                result_checksum_sha256=None,
                result_object_version_id=result.object_version_id if result else None,
            )
        )
        if result is None:
            continue
        stat = statistics_by_result.get(result.id)
        source_preview = next(
            (
                asset
                for asset in capture_assets
                if (asset.band_code or "").upper() == "RED" and asset.preview_object_key
            ),
            next(
                (asset for asset in capture_assets if asset.preview_object_key),
                None,
            ),
        )
        preview_url = await _presigned_download(
            request,
            source_preview.preview_object_key if source_preview else None,
            source_preview.preview_object_version_id if source_preview else None,
        )
        row = ResultTableRow(
            result_id=result.id,
            item_id=item.id,
            treatment=treatment_name,
            repetition=repetition,
            capture=capture.capture_key if capture else item.error_capture,
            index_name=item.index_name,
            mean=stat.mean if stat else None,
            median=stat.median if stat else None,
            min=stat.min if stat else None,
            max=stat.max if stat else None,
            std=stat.std if stat else None,
            p05=stat.p05 if stat else None,
            p25=stat.p25 if stat else None,
            p75=stat.p75 if stat else None,
            p95=stat.p95 if stat else None,
            valid_pixels=result.valid_pixel_count,
            original_preview_url=preview_url,
            original_label=source_preview.original_filename if source_preview else None,
        )
        rows.append(row)
        images.append(
            ResultImage(
                result_id=result.id,
                item_id=item.id,
                treatment=treatment_name,
                repetition=repetition,
                capture=row.capture,
                index_name=item.index_name,
                original_preview_url=preview_url,
                original_label=row.original_label,
            )
        )

    all_treatments = sorted({row.treatment for row in rows if row.treatment})
    all_indices = sorted({row.index_name for row in rows})
    filtered = [
        row
        for row in rows
        if (not treatment_filter or row.treatment == treatment_filter)
        and (not index_filter or row.index_name == index_filter)
        and (
            not query
            or query.casefold()
            in " ".join(
                value
                for value in (row.treatment, row.repetition, row.capture, row.index_name)
                if value
            ).casefold()
        )
    ]
    filtered.sort(key=lambda row: _sort_value(row, sort), reverse=descending)
    visible_rows = filtered[offset : offset + limit]
    grouped: dict[tuple[str | None, str], list[float]] = defaultdict(list)
    for row in rows:
        if row.mean is not None:
            grouped[(row.treatment, row.index_name)].append(row.mean)
    summaries = [
        TreatmentSummary(
            treatment=treatment_name,
            index_name=index_name,
            n=len(values),
            mean=float(np.mean(values)) if values else None,
            std=float(np.std(values, ddof=1)) if len(values) > 1 else None,
        )
        for (treatment_name, index_name), values in sorted(
            grouped.items(), key=lambda group: ((group[0][0] or ""), group[0][1])
        )
    ]
    errors = [
        ProcessingError(
            item_id=item.id,
            file=item.error_file,
            repetition=identity(item)[1] or item.error_capture,
            problem=item.error_message or "O item não foi processado.",
            error_code=item.error_code,
            recommended_action=_recommendation(item.error_code),
        )
        for item in items
        if item.state in {ProcessingItemState.FAILED, ProcessingItemState.SKIPPED}
    ]
    artifacts = list(
        await session.scalars(
            select(ExportArtifact)
            .where(ExportArtifact.job_id == job.id, ExportArtifact.owner_id == owner_id)
            .order_by(ExportArtifact.created_at.desc())
        )
    )
    artifact_by_item = {
        artifact.processing_item_id: artifact
        for artifact in artifacts
        if artifact.processing_item_id
    }
    for item_input in reproducibility_inputs:
        artifact = artifact_by_item.get(item_input.item_id)
        if artifact:
            item_input.result_checksum_sha256 = artifact.checksum_sha256
    files = [
        ResultFile(
            id=artifact.id,
            item_id=artifact.processing_item_id,
            artifact_type=artifact.artifact_type,
            state=artifact.state,
            filename=Path(artifact.object_key).name if artifact.object_key else None,
            size_bytes=artifact.size_bytes,
            checksum_sha256=artifact.checksum_sha256,
            download_url=await _presigned_download(
                request, artifact.object_key, artifact.object_version_id
            ),
            created_at=artifact.created_at,
        )
        for artifact in artifacts
    ]
    overview = ResultOverview(
        dataset_id=dataset.id,
        dataset_name=dataset.name,
        treatment_count=len(all_treatments),
        repetition_count=len({row.repetition for row in rows if row.repetition}),
        image_count=len(asset_rows),
        indices=all_indices,
        analyzed_pixels=sum(row.valid_pixels for row in rows),
        error_count=len(errors),
        analyzed_at=job.finished_at or job.updated_at,
    )
    recipe = await session.get(ProcessingRecipe, job.recipe_id) if job.recipe_id else None
    frozen_dataset = job.reproducibility_snapshot.get("dataset", {})
    frozen_formulas = job.reproducibility_snapshot.get("indices", [])
    frozen_captures = job.reproducibility_snapshot.get("captures", [])
    return ProcessingResults(
        overview=overview,
        table=ResultTable(
            rows=visible_rows,
            total=len(filtered),
            treatments=all_treatments,
            indices=all_indices,
        ),
        treatment_summary=summaries,
        images=images,
        errors=errors,
        files=files,
        reproducibility=ReproducibilityManifest(
            job_id=job.id,
            job_state=job.state,
            created_at=job.created_at,
            completed_at=job.finished_at,
            parent_job_id=job.parent_job_id,
            recipe_id=job.recipe_id,
            recipe_name=recipe.name if recipe else None,
            software_version=job.software_version,
            radiometry_mode=(
                str(frozen_dataset.get("radiometry_mode", dataset.radiometry_mode.value))
                if isinstance(frozen_dataset, dict)
                else dataset.radiometry_mode.value
            ),
            snapshot_schema_version=job.snapshot_schema_version,
            snapshot_checksum=job.snapshot_checksum,
            formulas=(
                [dict(value) for value in frozen_formulas]
                if isinstance(frozen_formulas, list)
                else []
            ),
            captures=(
                [dict(value) for value in frozen_captures]
                if isinstance(frozen_captures, list)
                else []
            ),
            configuration=job.configuration_snapshot,
            inputs=reproducibility_inputs,
        ),
    )


@router.get("/processing-jobs/{job_id}/results", response_model=ProcessingResults)
async def get_processing_results(
    job_id: UUID,
    request: Request,
    session: Session,
    user: CurrentUser,
    q: str | None = None,
    treatment: str | None = None,
    index: str | None = None,
    sort: ResultSort = "repetition",
    descending: bool = False,
    offset: int = 0,
    limit: int = 200,
) -> ProcessingResults:
    if offset < 0 or limit < 1 or limit > 500:
        raise HTTPException(status_code=422, detail="offset and limit are outside supported bounds")
    job = await _owned_job(session, job_id, user.id)
    return await _results_payload(
        request,
        session,
        job,
        user.id,
        query=q,
        treatment_filter=treatment,
        index_filter=index,
        sort=sort,
        descending=descending,
        offset=offset,
        limit=limit,
    )


def _pseudocolor_preview(s3: Any, bucket: str, key: str, version: str | None) -> bytes:
    """Render a bounded PNG preview; never writes or changes the source GeoTIFF."""
    with TemporaryDirectory(prefix="spectral-result-preview-") as directory:
        source = Path(directory) / "result.tif"
        extra = {"VersionId": version} if version else None
        if extra:
            s3.download_file(bucket, key, str(source), ExtraArgs=extra)
        else:
            s3.download_file(bucket, key, str(source))
        with rasterio.open(source) as raster:
            scale = min(1.0, 1024 / max(raster.width, raster.height))
            width = max(1, round(raster.width * scale))
            height = max(1, round(raster.height * scale))
            values = raster.read(
                1,
                out_shape=(height, width),
                masked=True,
                resampling=Resampling.bilinear,
            ).astype(np.float32)
    valid = ~np.ma.getmaskarray(values) & np.isfinite(values)
    rgba = np.zeros((values.shape[0], values.shape[1], 4), dtype=np.uint8)
    if np.any(valid):
        low, high = np.percentile(values.data[valid], [2, 98])
        if not np.isfinite(low) or not np.isfinite(high) or high <= low:
            low, high = float(np.min(values.data[valid])), float(np.max(values.data[valid]))
        normalized = np.clip((values.data - low) / max(high - low, 1e-12), 0, 1)
        rgba[..., 0] = (255 * normalized).astype(np.uint8)
        rgba[..., 1] = (255 * np.sqrt(normalized)).astype(np.uint8)
        rgba[..., 2] = (255 * (1 - normalized)).astype(np.uint8)
        rgba[..., 3] = np.where(valid, 255, 0).astype(np.uint8)
    from io import BytesIO

    target = BytesIO()
    Image.fromarray(rgba, mode="RGBA").save(target, format="PNG", optimize=True)
    return target.getvalue()


@router.get("/index-results/{result_id}/preview", response_class=Response)
async def preview_index_result(
    result_id: UUID, request: Request, session: Session, user: CurrentUser
) -> Response:
    result = await session.scalar(
        select(IndexResult).where(IndexResult.id == result_id, IndexResult.owner_id == user.id)
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Index result not found")
    resources = getattr(request.app.state, "resources", None)
    if resources is None:
        raise RuntimeError("Application resources are not initialized")
    png = await asyncio.to_thread(
        _pseudocolor_preview,
        resources.s3,
        get_settings().s3_bucket,
        result.object_key,
        result.object_version_id,
    )
    return Response(
        content=png,
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=300"},
    )
