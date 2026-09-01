"""Dashboard API contracts."""

from pydantic import BaseModel

from spectral_api.schemas.projects import ProjectResponse


class DashboardResponse(BaseModel):
    project_count: int
    dataset_count: int
    recent_projects: list[ProjectResponse]
