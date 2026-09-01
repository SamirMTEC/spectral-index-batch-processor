"""Dataset detail and deletion endpoints."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from spectral_api.dependencies import CurrentUser, get_session
from spectral_api.domain.models import Dataset, DatasetState, ProcessingJob, UploadSession
from spectral_api.schemas.datasets import DatasetResponse

router = APIRouter(tags=["datasets"])
Session = Annotated[AsyncSession, Depends(get_session)]


async def get_owned_dataset(session: AsyncSession, dataset_id: UUID, user_id: UUID) -> Dataset:
    dataset = await session.scalar(
        select(Dataset).where(Dataset.id == dataset_id, Dataset.owner_id == user_id)
    )
    if dataset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dataset not found")
    return dataset


@router.get("/datasets/{dataset_id}", response_model=DatasetResponse)
async def get_dataset(dataset_id: UUID, session: Session, user: CurrentUser) -> Dataset:
    return await get_owned_dataset(session, dataset_id, user.id)


@router.delete("/datasets/{dataset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_dataset(dataset_id: UUID, session: Session, user: CurrentUser) -> Response:
    """Delete an untouched draft dataset owned by the current user.

    Originals and populated datasets remain immutable; later lifecycle actions will
    archive them instead of deleting them.
    """
    dataset = await get_owned_dataset(session, dataset_id, user.id)
    if dataset.state is not DatasetState.DRAFT:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only draft datasets can be deleted",
        )
    has_job = await session.scalar(
        select(ProcessingJob.id).where(ProcessingJob.dataset_id == dataset.id).limit(1)
    )
    if has_job is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Datasets with processing history cannot be deleted",
        )
    has_upload = await session.scalar(
        select(UploadSession.id).where(UploadSession.dataset_id == dataset.id).limit(1)
    )
    if has_upload is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Datasets with upload history cannot be deleted",
        )
    await session.delete(dataset)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
