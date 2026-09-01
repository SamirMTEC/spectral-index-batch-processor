"""Dataset API contracts."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from spectral_api.domain.models import DatasetState, RadiometryMode


class DatasetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=4_000)
    crop: str | None = Field(default=None, max_length=160)
    experiment: str | None = Field(default=None, max_length=255)
    location: str | None = Field(default=None, max_length=255)
    sensor: str | None = Field(default=None, max_length=255)
    radiometry_mode: RadiometryMode = RadiometryMode.UNKNOWN
    notes: str | None = Field(default=None, max_length=10_000)


class DatasetResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    band_sequence_profile_id: UUID | None
    name: str
    description: str | None
    crop: str | None
    experiment: str | None
    location: str | None
    sensor: str | None
    radiometry_mode: RadiometryMode
    notes: str | None
    state: DatasetState
    created_at: datetime
    updated_at: datetime
