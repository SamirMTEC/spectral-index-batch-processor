"""Persisted band-sequence profile contracts."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class BandSequenceProfileCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    filename_prefix: str = Field(min_length=1, max_length=160)
    capture_prefix: str = Field(min_length=1, max_length=160)
    band_order: list[str] = Field(min_length=1, max_length=16)


class BandSequenceProfileResponse(BandSequenceProfileCreate):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime
    updated_at: datetime


class DatasetBandSequenceProfileUpdate(BaseModel):
    profile_id: UUID
