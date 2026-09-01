"""Dashboard summary endpoints backed by persisted resources."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from spectral_api.dependencies import CurrentUser, get_session
from spectral_api.domain.models import Dataset, Project
from spectral_api.schemas.dashboard import DashboardResponse
from spectral_api.schemas.projects import ProjectResponse

router = APIRouter(tags=["dashboard"])
Session = Annotated[AsyncSession, Depends(get_session)]


@router.get("/dashboard", response_model=DashboardResponse)
async def get_dashboard(session: Session, user: CurrentUser) -> DashboardResponse:
    project_count = await session.scalar(
        select(func.count()).select_from(Project).where(Project.owner_id == user.id)
    )
    dataset_count = await session.scalar(
        select(func.count()).select_from(Dataset).where(Dataset.owner_id == user.id)
    )
    recent_projects = list(
        await session.scalars(
            select(Project)
            .where(Project.owner_id == user.id)
            .order_by(Project.updated_at.desc())
            .limit(5)
        )
    )
    return DashboardResponse(
        project_count=project_count or 0,
        dataset_count=dataset_count or 0,
        recent_projects=[ProjectResponse.model_validate(project) for project in recent_projects],
    )
