"""Owner-scoped creation and download listing for scientific exports."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from spectral_api.dependencies import CurrentUser, get_session
from spectral_api.domain.models import ExportArtifact, ExportArtifactState
from spectral_api.routes.results import _owned_job, _presigned_download
from spectral_api.schemas.exports import ExportArtifactResponse, ExportCreate, ExportFormat

router = APIRouter(tags=["exports"])
Session = Annotated[AsyncSession, Depends(get_session)]

ARTIFACT_TYPES: dict[ExportFormat, str] = {
    "csv": "csv",
    "xlsx": "xlsx",
    "summary_csv": "summary_csv",
    "summary_xlsx": "summary_xlsx",
    "rois": "roi_json",
    "manifest": "manifest_json",
    "logs": "processing_log",
    "rasters_roi": "roi_rasters_zip",
    "rasters_full_image": "full_image_rasters_zip",
    "complete": "bundle_zip",
}


async def _response(request: Request, artifact: ExportArtifact) -> ExportArtifactResponse:
    return ExportArtifactResponse(
        id=artifact.id,
        artifact_type=artifact.artifact_type,
        state=artifact.state,
        filename=Path(artifact.object_key).name if artifact.object_key else None,
        size_bytes=artifact.size_bytes,
        checksum_sha256=artifact.checksum_sha256,
        download_url=await _presigned_download(
            request, artifact.object_key, artifact.object_version_id
        ),
        created_at=artifact.created_at,
        updated_at=artifact.updated_at,
    )


@router.get("/processing-jobs/{job_id}/exports", response_model=list[ExportArtifactResponse])
async def list_exports(
    job_id: UUID, request: Request, session: Session, user: CurrentUser
) -> list[ExportArtifactResponse]:
    await _owned_job(session, job_id, user.id)
    artifacts = list(
        await session.scalars(
            select(ExportArtifact)
            .where(
                ExportArtifact.job_id == job_id,
                ExportArtifact.owner_id == user.id,
                ExportArtifact.processing_item_id.is_(None),
            )
            .order_by(ExportArtifact.created_at.desc())
        )
    )
    return [await _response(request, artifact) for artifact in artifacts]


@router.post(
    "/processing-jobs/{job_id}/exports",
    response_model=list[ExportArtifactResponse],
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_exports(
    job_id: UUID,
    payload: ExportCreate,
    request: Request,
    session: Session,
    user: CurrentUser,
) -> list[ExportArtifactResponse]:
    job = await _owned_job(session, job_id, user.id)
    if job.state in {"queued", "running"}:
        raise HTTPException(
            status_code=409, detail="Exports are available after processing finishes"
        )
    requested_types = {ARTIFACT_TYPES[format_name] for format_name in payload.formats}
    existing = list(
        await session.scalars(
            select(ExportArtifact).where(
                ExportArtifact.job_id == job.id,
                ExportArtifact.owner_id == user.id,
                ExportArtifact.processing_item_id.is_(None),
                ExportArtifact.artifact_type.in_(requested_types),
                ExportArtifact.state.in_(
                    {
                        ExportArtifactState.QUEUED,
                        ExportArtifactState.GENERATING,
                        ExportArtifactState.READY,
                    }
                ),
            )
        )
    )
    existing_types = {artifact.artifact_type for artifact in existing}
    created: list[ExportArtifact] = []
    for artifact_type in sorted(requested_types - existing_types):
        artifact = ExportArtifact(
            id=uuid4(),
            project_id=job.project_id,
            owner_id=user.id,
            dataset_id=job.dataset_id,
            job_id=job.id,
            artifact_type=artifact_type,
            state=ExportArtifactState.QUEUED,
        )
        session.add(artifact)
        created.append(artifact)
    await session.commit()
    resources = getattr(request.app.state, "resources", None)
    celery = getattr(resources, "celery", None)
    if created and celery is None:
        raise HTTPException(status_code=503, detail="Export worker dispatcher is unavailable")
    if created:
        assert celery is not None
        await run_in_threadpool(
            celery.send_task,
            "spectral.export.generate",
            args=[[str(artifact.id) for artifact in created]],
        )
    return [await _response(request, artifact) for artifact in [*existing, *created]]
