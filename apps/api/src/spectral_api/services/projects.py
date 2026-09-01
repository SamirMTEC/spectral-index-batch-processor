"""Owner-scoped project lookup helpers."""

from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from spectral_api.domain.models import Project, User


async def get_owned_project(session: AsyncSession, project_id: UUID, user: User) -> Project:
    """Get a project only when it belongs to the requesting user.

    A non-owned resource intentionally returns 404, avoiding disclosure of IDs.
    """
    project = await session.scalar(
        select(Project).where(Project.id == project_id, Project.owner_id == user.id)
    )
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project
