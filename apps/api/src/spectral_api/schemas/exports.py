"""Contracts for reproducible scientific export generation."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

ExportFormat = Literal["csv", "xlsx", "rois", "manifest", "logs", "complete"]


class ExportCreate(BaseModel):
    formats: list[ExportFormat] = Field(min_length=1, max_length=6)

    @field_validator("formats")
    @classmethod
    def unique_formats(cls, values: list[ExportFormat]) -> list[ExportFormat]:
        if len(values) != len(set(values)):
            raise ValueError("Each export format may be requested only once")
        return values


class ExportArtifactResponse(BaseModel):
    id: UUID
    artifact_type: str
    state: str
    filename: str | None
    size_bytes: int | None
    checksum_sha256: str | None
    download_url: str | None
    created_at: datetime
    updated_at: datetime
