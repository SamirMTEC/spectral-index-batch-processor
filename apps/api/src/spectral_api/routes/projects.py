"""Project and nested dataset endpoints."""

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from spectral_api.dependencies import CurrentUser, get_session
from spectral_api.domain.models import Dataset, Project, ProjectStatus
from spectral_api.schemas.datasets import DatasetCreate, DatasetResponse
from spectral_api.schemas.projects import ProjectCreate, ProjectResponse, ProjectUpdate
from spectral_api.services.projects import get_owned_project

router = APIRouter(tags=["projects"])
Session = Annotated[AsyncSession, Depends(get_session)]


@router.get("/projects", response_model=list[ProjectResponse])
async def list_projects(session: Session, user: CurrentUser) -> list[Project]:
    """List the current researcher's projects, newest first."""
    result = await session.scalars(
        select(Project).where(Project.owner_id == user.id).order_by(Project.created_at.desc())
    )
    return list(result)


@router.post("/projects", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project(payload: ProjectCreate, session: Session, user: CurrentUser) -> Project:
    """Create an active project owned by the current researcher."""
    project = Project(owner_id=user.id, **payload.model_dump())
    session.add(project)
    await session.commit()
    await session.refresh(project)
    return project


@router.get("/projects/{project_id}", response_model=ProjectResponse)
async def get_project(project_id: UUID, session: Session, user: CurrentUser) -> Project:
    return await get_owned_project(session, project_id, user)


@router.patch("/projects/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: UUID, payload: ProjectUpdate, session: Session, user: CurrentUser
) -> Project:
    project = await get_owned_project(session, project_id, user)
    if project.status is ProjectStatus.ARCHIVED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Archived projects are read-only"
        )
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(project, field, value)
    await session.commit()
    await session.refresh(project)
    return project


@router.post("/projects/{project_id}/archive", response_model=ProjectResponse)
async def archive_project(project_id: UUID, session: Session, user: CurrentUser) -> Project:
    project = await get_owned_project(session, project_id, user)
    if project.status is ProjectStatus.ACTIVE:
        project.status = ProjectStatus.ARCHIVED
        project.archived_at = datetime.now(UTC)
        await session.commit()
        await session.refresh(project)
    return project


@router.get("/projects/{project_id}/datasets", response_model=list[DatasetResponse])
async def list_datasets(project_id: UUID, session: Session, user: CurrentUser) -> list[Dataset]:
    await get_owned_project(session, project_id, user)
    result = await session.scalars(
        select(Dataset)
        .where(Dataset.project_id == project_id, Dataset.owner_id == user.id)
        .order_by(Dataset.created_at.desc())
    )
    return list(result)


@router.post(
    "/projects/{project_id}/datasets",
    response_model=DatasetResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_dataset(
    project_id: UUID, payload: DatasetCreate, session: Session, user: CurrentUser
) -> Dataset:
    project = await get_owned_project(session, project_id, user)
    if project.status is ProjectStatus.ARCHIVED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Archived projects are read-only"
        )
    dataset = Dataset(project_id=project.id, owner_id=user.id, **payload.model_dump())
    session.add(dataset)
    await session.commit()
    await session.refresh(dataset)
    return dataset
