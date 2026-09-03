"""Read models for the completed-processing results experience."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from spectral_api.domain.models import AnalysisScope


class ResultOverview(BaseModel):
    dataset_id: UUID
    dataset_name: str
    treatment_count: int
    repetition_count: int
    image_count: int
    indices: list[str]
    analyzed_pixels: int
    analyzed_pixels_roi: int
    analyzed_pixels_full_image: int
    error_count: int
    analyzed_at: datetime | None


class ResultTableRow(BaseModel):
    result_id: UUID
    item_id: UUID
    treatment: str | None
    repetition: str | None
    capture: str | None
    index_name: str
    analysis_scope: AnalysisScope
    mean: float | None
    median: float | None
    min: float | None
    max: float | None
    std: float | None
    p05: float | None
    p25: float | None
    p75: float | None
    p95: float | None
    valid_pixels: int
    original_preview_url: str | None
    original_label: str | None


class ResultTable(BaseModel):
    rows: list[ResultTableRow]
    total: int
    treatments: list[str]
    indices: list[str]
    scopes: list[AnalysisScope]


class TreatmentSummary(BaseModel):
    treatment: str | None
    index_name: str
    analysis_scope: AnalysisScope
    n: int
    mean: float | None
    std: float | None


class ResultImage(BaseModel):
    result_id: UUID
    item_id: UUID
    treatment: str | None
    repetition: str | None
    capture: str | None
    index_name: str
    analysis_scope: AnalysisScope
    original_preview_url: str | None
    original_label: str | None


class ProcessingError(BaseModel):
    item_id: UUID
    file: str | None
    repetition: str | None
    problem: str
    error_code: str | None
    analysis_scope: AnalysisScope
    recommended_action: str


class ResultFile(BaseModel):
    id: UUID
    item_id: UUID | None
    artifact_type: str
    state: str
    filename: str | None
    size_bytes: int | None
    checksum_sha256: str | None
    download_url: str | None
    created_at: datetime


class ReproducibilityInput(BaseModel):
    item_id: UUID
    capture: str | None
    repetition: str | None
    index_name: str
    analysis_scope: AnalysisScope
    formula_version: str
    formula: str
    parameters: dict[str, object]
    roi_version_id: UUID | None
    source_bands: list[dict[str, str | None]]
    result_checksum_sha256: str | None
    result_object_version_id: str | None


class ReproducibilityManifest(BaseModel):
    job_id: UUID
    job_state: str
    created_at: datetime
    completed_at: datetime | None
    parent_job_id: UUID | None
    recipe_id: UUID | None
    recipe_name: str | None
    software_version: str
    radiometry_mode: str
    snapshot_schema_version: str
    snapshot_checksum: str
    formulas: list[dict[str, object]]
    captures: list[dict[str, object]]
    configuration: dict[str, object]
    inputs: list[ReproducibilityInput]


class ProcessingResults(BaseModel):
    overview: ResultOverview
    table: ResultTable
    treatment_summary: list[TreatmentSummary]
    images: list[ResultImage]
    errors: list[ProcessingError]
    files: list[ResultFile]
    reproducibility: ReproducibilityManifest


ResultSort = Literal["treatment", "repetition", "index", "mean", "valid_pixels"]
