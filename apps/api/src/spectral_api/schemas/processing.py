"""Contracts for asynchronous scientific batch processing."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from spectral_api.domain.models import ProcessingItemState, ProcessingJobState

IndexName = Literal["NDVI", "GNDVI", "SAVI", "VARI", "EXG"]


class ProcessingIndexInput(BaseModel):
    name: IndexName
    parameters: dict[str, float] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_parameters(self) -> "ProcessingIndexInput":
        allowed = {"L"} if self.name == "SAVI" else set()
        unknown = set(self.parameters) - allowed
        if unknown:
            raise ValueError(f"Unknown parameters for {self.name}: {', '.join(sorted(unknown))}")
        if self.name == "SAVI" and "L" not in self.parameters:
            self.parameters["L"] = 0.5
        return self


class ProcessingJobCreate(BaseModel):
    indices: list[ProcessingIndexInput] = Field(default_factory=list, max_length=5)
    recipe_id: UUID | None = None
    capture_ids: list[UUID] | None = Field(default=None, max_length=10_000)
    timeout_seconds: int = Field(default=300, ge=30, le=3_600)
    max_attempts: int = Field(default=3, ge=1, le=5)

    @field_validator("indices")
    @classmethod
    def unique_indices(cls, values: list[ProcessingIndexInput]) -> list[ProcessingIndexInput]:
        names = [value.name for value in values]
        if len(names) != len(set(names)):
            raise ValueError("Each spectral index may be requested only once")
        return values

    @model_validator(mode="after")
    def require_recipe_or_indices(self) -> "ProcessingJobCreate":
        if not self.indices and self.recipe_id is None:
            raise ValueError("Select at least one index or a processing recipe")
        if self.indices and self.recipe_id is not None:
            raise ValueError("A recipe cannot be combined with ad-hoc indices")
        return self


class StatisticsResponse(BaseModel):
    count: int
    mean: float | None
    median: float | None
    min: float | None
    max: float | None
    std: float | None
    p05: float | None
    p25: float | None
    p75: float | None
    p95: float | None


class ProcessingItemResponse(BaseModel):
    id: UUID
    capture_id: UUID | None
    capture: str | None
    index_name: str
    formula_version: str
    state: ProcessingItemState
    attempts: int
    max_attempts: int
    started_at: datetime | None
    finished_at: datetime | None
    error_code: str | None
    message: str | None
    file: str | None
    result_available: bool
    statistics: StatisticsResponse | None


class ProcessingJobResponse(BaseModel):
    id: UUID
    dataset_id: UUID
    parent_job_id: UUID | None
    recipe_id: UUID | None
    recipe_name: str | None
    snapshot_checksum: str
    software_version: str
    state: ProcessingJobState
    indices: list[str]
    total_items: int
    processed_items: int
    completed_items: int
    failed_items: int
    skipped_items: int
    progress: float
    started_at: datetime | None
    finished_at: datetime | None
    elapsed_seconds: float
    cancel_requested_at: datetime | None
    warnings: list[str]
    error_code: str | None
    message: str | None
    created_at: datetime
    updated_at: datetime
    items: list[ProcessingItemResponse]
