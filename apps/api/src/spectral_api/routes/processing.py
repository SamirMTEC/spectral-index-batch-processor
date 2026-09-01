"""Owner-scoped asynchronous spectral processing endpoints."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from spectral_common import (
    DEFAULT_STATISTICS,
    SNAPSHOT_SCHEMA_VERSION,
    SOFTWARE_VERSION,
    get_formula_definition,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from spectral_api.dependencies import CurrentUser, get_session
from spectral_api.domain.models import (
    Capture,
    IndexResult,
    ProcessingItem,
    ProcessingItemState,
    ProcessingJob,
    ProcessingJobState,
    ProcessingRecipe,
    Statistics,
)
from spectral_api.routes.datasets import get_owned_dataset
from spectral_api.schemas.processing import (
    ProcessingItemResponse,
    ProcessingJobCreate,
    ProcessingJobResponse,
    StatisticsResponse,
)
from spectral_api.services.reproducibility import (
    build_reproducibility_snapshot,
    canonical_checksum,
)

router = APIRouter(tags=["processing"])
Session = Annotated[AsyncSession, Depends(get_session)]
TERMINAL_ITEMS = {
    ProcessingItemState.COMPLETED,
    ProcessingItemState.FAILED,
    ProcessingItemState.SKIPPED,
}
TERMINAL_JOBS = {
    ProcessingJobState.COMPLETED,
    ProcessingJobState.COMPLETED_WITH_ERRORS,
    ProcessingJobState.FAILED,
    ProcessingJobState.CANCELLED,
}


def _request_hash(dataset_id: UUID, payload: ProcessingJobCreate) -> str:
    canonical = {
        "dataset_id": str(dataset_id),
        "indices": [item.model_dump(mode="json") for item in payload.indices],
        "capture_ids": sorted(str(value) for value in (payload.capture_ids or [])),
        "timeout_seconds": payload.timeout_seconds,
        "max_attempts": payload.max_attempts,
        "recipe_id": str(payload.recipe_id) if payload.recipe_id else None,
    }
    return hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


async def _job_response(session: AsyncSession, job: ProcessingJob) -> ProcessingJobResponse:
    items = list(
        await session.scalars(
            select(ProcessingItem)
            .where(ProcessingItem.job_id == job.id)
            .order_by(ProcessingItem.created_at, ProcessingItem.index_name)
        )
    )
    responses: list[ProcessingItemResponse] = []
    warnings: list[str] = []
    for item in items:
        capture = await session.get(Capture, item.capture_id) if item.capture_id else None
        result = await session.scalar(
            select(IndexResult).where(IndexResult.processing_item_id == item.id)
        )
        statistics = None
        if result is not None:
            stored = await session.scalar(
                select(Statistics).where(Statistics.index_result_id == result.id)
            )
            if stored is not None:
                statistics = StatisticsResponse(
                    count=stored.count,
                    mean=stored.mean,
                    median=stored.median,
                    min=stored.min,
                    max=stored.max,
                    std=stored.std,
                    p05=stored.p05,
                    p25=stored.p25,
                    p75=stored.p75,
                    p95=stored.p95,
                )
        if item.state is ProcessingItemState.SKIPPED and item.error_message:
            warnings.append(
                f"{item.error_capture or 'Captura'} - {item.index_name}: {item.error_message}"
            )
        responses.append(
            ProcessingItemResponse(
                id=item.id,
                capture_id=item.capture_id,
                capture=item.error_capture or (capture.capture_key if capture else None),
                index_name=item.index_name,
                formula_version=item.formula_version,
                state=item.state,
                attempts=item.attempts,
                max_attempts=item.max_attempts,
                started_at=item.started_at,
                finished_at=item.finished_at,
                error_code=item.error_code,
                message=item.error_message,
                file=item.error_file,
                result_available=result is not None,
                statistics=statistics,
            )
        )
    now = datetime.now(UTC)
    start = job.started_at or job.created_at
    end = job.finished_at or now
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    if end.tzinfo is None:
        end = end.replace(tzinfo=UTC)
    configured_indices = job.configuration_snapshot.get("indices", [])
    indices = (
        [str(value) for value in configured_indices] if isinstance(configured_indices, list) else []
    )
    recipe = await session.get(ProcessingRecipe, job.recipe_id) if job.recipe_id else None
    return ProcessingJobResponse(
        id=job.id,
        dataset_id=job.dataset_id,
        parent_job_id=job.parent_job_id,
        recipe_id=job.recipe_id,
        recipe_name=recipe.name if recipe else None,
        snapshot_checksum=job.snapshot_checksum,
        software_version=job.software_version,
        state=job.state,
        indices=indices,
        total_items=job.total_items,
        processed_items=job.processed_items,
        completed_items=job.completed_items,
        failed_items=job.failed_items,
        skipped_items=job.skipped_items,
        progress=(job.processed_items / job.total_items if job.total_items else 0.0),
        started_at=job.started_at,
        finished_at=job.finished_at,
        elapsed_seconds=max(0.0, (end - start).total_seconds()),
        cancel_requested_at=job.cancel_requested_at,
        warnings=warnings,
        error_code=job.error_code,
        message=job.error_message,
        created_at=job.created_at,
        updated_at=job.updated_at,
        items=responses,
    )


async def _dispatch(request: Request, item: ProcessingItem, timeout_seconds: int) -> None:
    resources = getattr(request.app.state, "resources", None)
    celery = getattr(resources, "celery", None)
    if celery is None:
        raise RuntimeError("Celery dispatcher is unavailable")
    await run_in_threadpool(
        celery.send_task,
        "spectral.processing.process_item",
        args=[str(item.id)],
        task_id=item.celery_task_id,
        soft_time_limit=timeout_seconds,
        time_limit=timeout_seconds + 30,
    )


async def _refresh_counts(session: AsyncSession, job: ProcessingJob) -> None:
    rows = (
        await session.execute(
            select(ProcessingItem.state, func.count(ProcessingItem.id))
            .where(ProcessingItem.job_id == job.id)
            .group_by(ProcessingItem.state)
        )
    ).all()
    counts: dict[ProcessingItemState, int] = {state: int(count) for state, count in rows}
    job.completed_items = int(counts.get(ProcessingItemState.COMPLETED, 0))
    job.failed_items = int(counts.get(ProcessingItemState.FAILED, 0))
    job.skipped_items = int(counts.get(ProcessingItemState.SKIPPED, 0))
    job.processed_items = job.completed_items + job.failed_items + job.skipped_items


async def _resolve_configuration(
    session: AsyncSession, payload: ProcessingJobCreate, owner_id: UUID
) -> tuple[ProcessingRecipe | None, list[dict[str, Any]], list[str]]:
    recipe: ProcessingRecipe | None = None
    if payload.recipe_id is not None:
        recipe = await session.scalar(
            select(ProcessingRecipe).where(
                ProcessingRecipe.id == payload.recipe_id,
                ProcessingRecipe.owner_id == owner_id,
                ProcessingRecipe.archived_at.is_(None),
            )
        )
        if recipe is None:
            raise HTTPException(status_code=404, detail="Processing recipe not found")
        raw_indices = recipe.definition_snapshot.get("indices", [])
        raw_statistics = recipe.definition_snapshot.get("statistics", [])
        recipe_indices = (
            [dict(value) for value in raw_indices] if isinstance(raw_indices, list) else []
        )
        statistics = (
            [str(value) for value in raw_statistics] if isinstance(raw_statistics, list) else []
        )
        if not recipe_indices:
            raise HTTPException(status_code=409, detail="Processing recipe has no indices")
        return recipe, recipe_indices, statistics
    indices: list[dict[str, Any]] = []
    for requested in payload.indices:
        definition = get_formula_definition(requested.name)
        indices.append(
            {
                "index": definition.index,
                "formula_version": definition.formula_version,
                "formula": definition.expression,
                "required_bands": list(definition.required_bands),
                "parameters": definition.parameters_with_defaults(dict(requested.parameters)),
            }
        )
    return None, indices, list(DEFAULT_STATISTICS)


async def _clone_job(
    request: Request,
    session: AsyncSession,
    source: ProcessingJob,
    source_items: list[ProcessingItem],
) -> ProcessingJob:
    if source.snapshot_schema_version != SNAPSHOT_SCHEMA_VERSION or not source.snapshot_checksum:
        raise HTTPException(
            status_code=409,
            detail="This legacy job does not contain a complete immutable snapshot",
        )
    job = ProcessingJob(
        id=uuid4(),
        project_id=source.project_id,
        owner_id=source.owner_id,
        dataset_id=source.dataset_id,
        parent_job_id=source.id,
        recipe_id=source.recipe_id,
        state=ProcessingJobState.QUEUED,
        request_hash=hashlib.sha256(f"reprocess:{source.id}:{uuid4()}".encode()).hexdigest(),
        configuration_snapshot=deepcopy(source.configuration_snapshot),
        reproducibility_snapshot=deepcopy(source.reproducibility_snapshot),
        snapshot_schema_version=source.snapshot_schema_version,
        snapshot_checksum=source.snapshot_checksum,
        software_version=source.software_version,
    )
    session.add(job)
    await session.flush()
    pending: list[ProcessingItem] = []
    now = datetime.now(UTC)
    for source_item in source_items:
        has_roi = bool(source_item.roi_snapshot and source_item.roi_version_id)
        item = ProcessingItem(
            id=uuid4(),
            project_id=source_item.project_id,
            owner_id=source_item.owner_id,
            job_id=job.id,
            dataset_id=source_item.dataset_id,
            capture_id=source_item.capture_id,
            roi_id=source_item.roi_id,
            roi_version_id=source_item.roi_version_id,
            index_name=source_item.index_name,
            formula_version=source_item.formula_version,
            formula_expression=source_item.formula_expression,
            parameters_snapshot=deepcopy(source_item.parameters_snapshot),
            input_snapshot=deepcopy(source_item.input_snapshot),
            band_mapping_snapshot=deepcopy(source_item.band_mapping_snapshot),
            roi_snapshot=deepcopy(source_item.roi_snapshot),
            state=ProcessingItemState.PENDING if has_roi else ProcessingItemState.SKIPPED,
            max_attempts=source_item.max_attempts,
            celery_task_id=str(uuid4()) if has_roi else None,
            finished_at=None if has_roi else now,
            error_code=None if has_roi else source_item.error_code or "missing_roi",
            error_message=None if has_roi else source_item.error_message,
            error_capture=source_item.error_capture,
            error_file=source_item.error_file if not has_roi else None,
        )
        session.add(item)
        if has_roi:
            pending.append(item)
    job.total_items = len(source_items)
    job.skipped_items = len(source_items) - len(pending)
    job.processed_items = job.skipped_items
    if not pending:
        job.state = ProcessingJobState.COMPLETED_WITH_ERRORS
        job.finished_at = now
    await session.commit()
    timeout_value = source.configuration_snapshot.get("timeout_seconds", 300)
    timeout = int(timeout_value) if isinstance(timeout_value, (int, float, str)) else 300
    try:
        for item in pending:
            await _dispatch(request, item, timeout)
    except Exception as error:
        job.state = ProcessingJobState.FAILED
        job.error_code = "queue_unavailable"
        job.error_message = "Não foi possível enviar o lote reprocessado para o worker"
        job.internal_traceback = type(error).__name__
        job.finished_at = datetime.now(UTC)
        for item in pending:
            item.state = ProcessingItemState.FAILED
            item.error_code = "queue_unavailable"
            item.error_message = "Item não enviado para processamento"
            item.finished_at = job.finished_at
        await _refresh_counts(session, job)
        await session.commit()
    await session.refresh(job)
    return job


@router.post(
    "/datasets/{dataset_id}/processing-jobs",
    response_model=ProcessingJobResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_processing_job(
    dataset_id: UUID,
    payload: ProcessingJobCreate,
    request: Request,
    session: Session,
    user: CurrentUser,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ProcessingJobResponse:
    dataset = await get_owned_dataset(session, dataset_id, user.id)
    recipe, index_definitions, selected_statistics = await _resolve_configuration(
        session, payload, user.id
    )
    digest = _request_hash(dataset_id, payload)
    if idempotency_key:
        existing = await session.scalar(
            select(ProcessingJob).where(
                ProcessingJob.owner_id == user.id,
                ProcessingJob.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            if existing.request_hash != digest:
                raise HTTPException(status_code=409, detail="Idempotency key was reused")
            return await _job_response(session, existing)
    duplicate = await session.scalar(
        select(ProcessingJob).where(
            ProcessingJob.owner_id == user.id,
            ProcessingJob.dataset_id == dataset_id,
            ProcessingJob.request_hash == digest,
            ProcessingJob.state.in_({ProcessingJobState.QUEUED, ProcessingJobState.RUNNING}),
        )
    )
    if duplicate is not None:
        return await _job_response(session, duplicate)

    captures_query = select(Capture).where(
        Capture.dataset_id == dataset_id, Capture.owner_id == user.id
    )
    if payload.capture_ids:
        captures_query = captures_query.where(Capture.id.in_(payload.capture_ids))
    captures = list(await session.scalars(captures_query.order_by(Capture.created_at)))
    if not captures:
        raise HTTPException(status_code=409, detail="Dataset has no captures to process")
    if payload.capture_ids and len(captures) != len(set(payload.capture_ids)):
        raise HTTPException(status_code=404, detail="One or more captures were not found")

    indices = [str(item["index"]) for item in index_definitions]
    reproducibility_snapshot, provenance = await build_reproducibility_snapshot(
        session,
        dataset,
        captures,
        index_definitions,
        selected_statistics,
        recipe,
    )
    job = ProcessingJob(
        id=uuid4(),
        project_id=dataset.project_id,
        owner_id=user.id,
        dataset_id=dataset.id,
        state=ProcessingJobState.QUEUED,
        recipe_id=recipe.id if recipe else None,
        idempotency_key=idempotency_key,
        request_hash=digest,
        configuration_snapshot={
            "indices": indices,
            "formula_versions": {
                str(item["index"]): str(item["formula_version"]) for item in index_definitions
            },
            "parameters": {str(item["index"]): item["parameters"] for item in index_definitions},
            "statistics": selected_statistics,
            "recipe_id": str(recipe.id) if recipe else None,
            "timeout_seconds": payload.timeout_seconds,
            "max_attempts": payload.max_attempts,
        },
        reproducibility_snapshot=reproducibility_snapshot,
        snapshot_schema_version=SNAPSHOT_SCHEMA_VERSION,
        snapshot_checksum=canonical_checksum(reproducibility_snapshot),
        software_version=SOFTWARE_VERSION,
    )
    session.add(job)
    await session.flush()
    pending: list[ProcessingItem] = []
    now = datetime.now(UTC)
    for capture in captures:
        frozen = provenance[capture.id]
        for definition in index_definitions:
            index_name = str(definition["index"])
            item = ProcessingItem(
                id=uuid4(),
                project_id=dataset.project_id,
                owner_id=user.id,
                job_id=job.id,
                dataset_id=dataset.id,
                capture_id=capture.id,
                roi_id=frozen.roi_id,
                roi_version_id=frozen.roi_version_id,
                index_name=index_name,
                formula_version=str(definition["formula_version"]),
                formula_expression=str(definition["formula"]),
                parameters_snapshot=dict(definition["parameters"]),
                input_snapshot=frozen.inputs_by_index[index_name],
                band_mapping_snapshot=frozen.mappings_by_index[index_name],
                roi_snapshot=frozen.roi_snapshot,
                state=(
                    ProcessingItemState.PENDING
                    if frozen.roi_version_id
                    else ProcessingItemState.SKIPPED
                ),
                max_attempts=payload.max_attempts,
                celery_task_id=str(uuid4()) if frozen.roi_version_id else None,
                finished_at=None if frozen.roi_version_id else now,
                error_code=None if frozen.roi_version_id else "missing_roi",
                error_message=(
                    None
                    if frozen.roi_version_id
                    else "Nenhuma ROI ativa foi definida para esta captura"
                ),
                error_capture=capture.capture_key,
            )
            session.add(item)
            if frozen.roi_version_id:
                pending.append(item)
    job.total_items = len(captures) * len(index_definitions)
    job.skipped_items = job.total_items - len(pending)
    job.processed_items = job.skipped_items
    if not pending:
        job.state = ProcessingJobState.COMPLETED_WITH_ERRORS
        job.finished_at = now
    await session.commit()

    try:
        for item in pending:
            await _dispatch(request, item, payload.timeout_seconds)
    except Exception as error:
        job.state = ProcessingJobState.FAILED
        job.error_code = "queue_unavailable"
        job.error_message = "Não foi possível enviar o lote para o worker"
        job.internal_traceback = type(error).__name__
        job.finished_at = datetime.now(UTC)
        for item in pending:
            item.state = ProcessingItemState.FAILED
            item.error_code = "queue_unavailable"
            item.error_message = "Item não enviado para processamento"
            item.finished_at = job.finished_at
        await _refresh_counts(session, job)
        await session.commit()
    await session.refresh(job)
    return await _job_response(session, job)


@router.get("/datasets/{dataset_id}/processing-jobs", response_model=list[ProcessingJobResponse])
async def list_processing_jobs(
    dataset_id: UUID, session: Session, user: CurrentUser
) -> list[ProcessingJobResponse]:
    await get_owned_dataset(session, dataset_id, user.id)
    jobs = list(
        await session.scalars(
            select(ProcessingJob)
            .where(ProcessingJob.dataset_id == dataset_id, ProcessingJob.owner_id == user.id)
            .order_by(ProcessingJob.created_at.desc())
        )
    )
    return [await _job_response(session, job) for job in jobs]


@router.get("/processing-jobs/{job_id}", response_model=ProcessingJobResponse)
async def get_processing_job(
    job_id: UUID, session: Session, user: CurrentUser
) -> ProcessingJobResponse:
    job = await session.scalar(
        select(ProcessingJob).where(ProcessingJob.id == job_id, ProcessingJob.owner_id == user.id)
    )
    if job is None:
        raise HTTPException(status_code=404, detail="Processing job not found")
    return await _job_response(session, job)


@router.post("/processing-jobs/{job_id}/reprocess", response_model=ProcessingJobResponse)
async def reprocess_job(
    job_id: UUID, request: Request, session: Session, user: CurrentUser
) -> ProcessingJobResponse:
    source = await session.scalar(
        select(ProcessingJob).where(ProcessingJob.id == job_id, ProcessingJob.owner_id == user.id)
    )
    if source is None:
        raise HTTPException(status_code=404, detail="Processing job not found")
    if source.state not in TERMINAL_JOBS:
        raise HTTPException(status_code=409, detail="Only finished jobs can be reprocessed")
    source_items = list(
        await session.scalars(
            select(ProcessingItem)
            .where(ProcessingItem.job_id == source.id)
            .order_by(ProcessingItem.created_at, ProcessingItem.index_name)
        )
    )
    cloned = await _clone_job(request, session, source, source_items)
    return await _job_response(session, cloned)


@router.post("/processing-jobs/{job_id}/cancel", response_model=ProcessingJobResponse)
async def cancel_processing_job(
    job_id: UUID, request: Request, session: Session, user: CurrentUser
) -> ProcessingJobResponse:
    job = await session.scalar(
        select(ProcessingJob)
        .where(ProcessingJob.id == job_id, ProcessingJob.owner_id == user.id)
        .with_for_update()
    )
    if job is None:
        raise HTTPException(status_code=404, detail="Processing job not found")
    if job.state in {
        ProcessingJobState.COMPLETED,
        ProcessingJobState.COMPLETED_WITH_ERRORS,
        ProcessingJobState.FAILED,
    }:
        raise HTTPException(status_code=409, detail="Finished jobs cannot be cancelled")
    job.state = ProcessingJobState.CANCELLED
    job.cancel_requested_at = datetime.now(UTC)
    job.finished_at = job.cancel_requested_at
    items = list(
        await session.scalars(select(ProcessingItem).where(ProcessingItem.job_id == job.id))
    )
    task_ids: list[str] = []
    for item in items:
        if item.celery_task_id:
            task_ids.append(item.celery_task_id)
        if item.state is ProcessingItemState.PENDING:
            item.state = ProcessingItemState.SKIPPED
            item.error_code = "job_cancelled"
            item.error_message = "Processamento cancelado pelo usuário"
            item.finished_at = job.finished_at
    await _refresh_counts(session, job)
    await session.commit()
    resources = getattr(request.app.state, "resources", None)
    celery = getattr(resources, "celery", None)
    if celery is not None:
        for task_id in task_ids:
            await run_in_threadpool(celery.control.revoke, task_id, terminate=False)
    await session.refresh(job)
    return await _job_response(session, job)


@router.post("/processing-items/{item_id}/retry", response_model=ProcessingJobResponse)
async def retry_processing_item(
    item_id: UUID, request: Request, session: Session, user: CurrentUser
) -> ProcessingJobResponse:
    item = await session.scalar(
        select(ProcessingItem).where(
            ProcessingItem.id == item_id, ProcessingItem.owner_id == user.id
        )
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Processing item not found")
    if item.state not in {ProcessingItemState.FAILED, ProcessingItemState.SKIPPED}:
        raise HTTPException(status_code=409, detail="Only failed or skipped items can be retried")
    job = await session.get(ProcessingJob, item.job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Processing job not found")
    if not item.roi_snapshot or item.roi_version_id is None:
        raise HTTPException(
            status_code=409,
            detail="This item has no frozen ROI; create a new configured job instead",
        )
    cloned = await _clone_job(request, session, job, [item])
    return await _job_response(session, cloned)
