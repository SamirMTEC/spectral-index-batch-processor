"""Capture viewer and versioned ROI API contracts."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from spectral_api.domain.models import AlignmentStatus, ROIGeometryType, ROIState


class CaptureSummary(BaseModel):
    id: UUID
    dataset_id: UUID
    replicate_id: UUID | None
    replicate_label: str | None
    capture_key: str
    band_count: int
    band_codes: list[str]
    roi_count: int
    created_at: datetime


class CaptureBand(BaseModel):
    id: UUID
    band_code: str
    filename: str
    width: int
    height: int
    crs: str | None
    transform: list[float] | None
    preview_url: str | None
    approximate_valid_fraction: float | None
    compatible: bool
    compatibility_error: str | None


class CaptureDetail(BaseModel):
    id: UUID
    dataset_id: UUID
    replicate_id: UUID | None
    replicate_label: str | None
    capture_key: str
    alignment_status: AlignmentStatus
    bands: list[CaptureBand]
    created_at: datetime
    updated_at: datetime


class ROIGeometryInput(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=2_000)
    geometry_type: ROIGeometryType
    normalized_coordinates: list[tuple[float, float]] = Field(min_length=3, max_length=10_000)
    reference_asset_id: UUID
    expected_version: int | None = Field(default=None, ge=1)


class ROIVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    version_number: int
    geometry_type: ROIGeometryType
    normalized_coordinates: list[list[float]]
    pixel_coordinates: list[list[float]]
    reference_capture_id: UUID
    reference_asset_id: UUID
    reference_width: int
    reference_height: int
    crs: str | None
    transform: list[float] | None
    spatial_coordinates: list[list[float]] | None
    geometry_checksum: str
    area_pixels: float
    approximate_valid_pixels: int | None
    created_at: datetime


class ROIResponse(BaseModel):
    id: UUID
    dataset_id: UUID
    replicate_id: UUID | None
    capture_id: UUID
    name: str
    description: str | None
    state: ROIState
    current_version: ROIVersionResponse
    created_at: datetime
    updated_at: datetime
