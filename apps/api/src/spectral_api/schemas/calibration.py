"""Validated inputs for panel calibration; no implicit default reflectances."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator


class CalibrationPanelCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    manufacturer: str | None = Field(default=None, max_length=160)
    model: str | None = Field(default=None, max_length=160)
    serial_number: str | None = Field(default=None, max_length=160)
    calibrated_at: datetime | None = None
    notes: str | None = Field(default=None, max_length=4000)
    source_label: str = Field(min_length=1, max_length=300)
    certified: bool = False
    values: dict[str, dict[str, float]] = Field(default_factory=dict)
    certificate_csv: str | None = Field(default=None, max_length=256_000)

    @model_validator(mode="after")
    def validate_sources(self) -> CalibrationPanelCreate:
        if not self.values and not self.certificate_csv:
            raise ValueError("Provide manual values or a CSV certificate")
        if self.certified and not self.certificate_csv:
            raise ValueError("A certificate file is required for certified status")
        for bands in self.values.values():
            for value in bands.values():
                if not 0 <= value <= 1:
                    raise ValueError("Reflectances must be between 0 and 1")
        return self


class CalibrationROI(BaseModel):
    surface: str = Field(min_length=1, max_length=80)
    band: str | None = None
    coordinates: list[tuple[float, float]] = Field(min_length=3, max_length=128)
    role: Literal["fit", "validation"] = "fit"

    @field_validator("coordinates")
    @classmethod
    def validate_coordinates(
        cls, coordinates: list[tuple[float, float]]
    ) -> list[tuple[float, float]]:
        if any(not 0 <= coordinate <= 1 for point in coordinates for coordinate in point):
            raise ValueError("ROI coordinates must be normalized to [0,1]")
        return coordinates


class CalibrationReferenceCapture(BaseModel):
    capture_id: UUID
    rois: list[CalibrationROI] = Field(min_length=1, max_length=80)


class CalibrationRunCreate(BaseModel):
    dataset_id: UUID
    capture_id: UUID
    target_capture_ids: list[UUID] = Field(default_factory=list, max_length=500)
    additional_references: list[CalibrationReferenceCapture] = Field(
        default_factory=list, max_length=50
    )
    target_reference_map: dict[UUID, UUID] = Field(default_factory=dict)
    reference_combination: Literal["per_target", "mean_coefficients"] = "per_target"
    session_name: str | None = Field(default=None, max_length=160)
    session_date: datetime | None = None
    session_timezone: str | None = Field(default=None, max_length=80)
    illumination_method: Literal["stable_panel", "not_assessed"] = "not_assessed"
    illumination_notes: str | None = Field(default=None, max_length=2000)
    panel_id: UUID
    method: Literal["proportional", "linear"]
    input_mode: Literal["p4_signal", "dn_empirical"]
    reference_band: str = Field(default="NIR", min_length=1, max_length=64)
    selected_band: str | None = Field(default=None, min_length=1, max_length=64)
    rois: list[CalibrationROI] = Field(min_length=1, max_length=80)
    acknowledge_dn_limits: bool = False
    exclude_saturated: bool = True
    edge_inset_percent: float = Field(default=5, ge=0, le=30)
    weighted: bool = False
    robust: bool = False
    max_cv: float | None = Field(default=None, gt=0, le=10)
    max_saturation_percent: float | None = Field(default=None, ge=0, le=100)
    max_validation_rmse: float | None = Field(default=None, ge=0, le=1)
    execute: bool = True

    @model_validator(mode="after")
    def validate_method(self) -> CalibrationRunCreate:
        reference_ids = {self.capture_id, *(item.capture_id for item in self.additional_references)}
        if len(reference_ids) != 1 + len(self.additional_references):
            raise ValueError("Duplicate reference captures")
        if len(set(self.target_capture_ids)) != len(self.target_capture_ids):
            raise ValueError("Duplicate target captures")
        if reference_ids.intersection(self.target_capture_ids):
            raise ValueError("Reference captures cannot also be target captures")
        if set(self.target_reference_map) - set(self.target_capture_ids):
            raise ValueError("Reference assignment contains an unselected target")
        if set(self.target_reference_map.values()) - reference_ids:
            raise ValueError("Reference assignment contains an unselected panel capture")
        if self.reference_combination == "mean_coefficients" and self.target_reference_map:
            raise ValueError("Combined references cannot have individual target assignments")
        if (
            self.reference_combination == "per_target"
            and self.additional_references
            and set(self.target_reference_map) != set(self.target_capture_ids)
        ):
            raise ValueError("Assign every target to a reference capture")
        if (
            self.target_capture_ids or self.additional_references
        ) and self.input_mode != "p4_signal":
            raise ValueError("Batch transfer requires corrected DJI camera signal")
        if (self.target_capture_ids or self.additional_references) and self.selected_band:
            raise ValueError("Batch transfer requires all five bands")
        if (
            self.target_capture_ids or self.additional_references
        ) and self.method != "proportional":
            raise ValueError("Single-target batch calibration requires the proportional method")
        if self.input_mode == "dn_empirical" and not self.acknowledge_dn_limits:
            raise ValueError("Empirical DN limitations must be acknowledged")
        fit_surfaces = {roi.surface for roi in self.rois if roi.role == "fit"}
        if (self.target_capture_ids or self.additional_references) and len(fit_surfaces) != 1:
            raise ValueError("Batch calibration requires exactly one fitting surface")
        if len(fit_surfaces) < (1 if self.method == "proportional" else 2):
            raise ValueError("Insufficient fitting surfaces")
        if len({(roi.surface, roi.band) for roi in self.rois}) != len(self.rois):
            raise ValueError("Duplicate surface/band ROI")
        for reference in self.additional_references:
            if len({(roi.surface, roi.band) for roi in reference.rois}) != len(reference.rois):
                raise ValueError("Duplicate surface/band ROI in reference capture")
            if {roi.surface for roi in reference.rois if roi.role == "fit"} != fit_surfaces:
                raise ValueError("All reference captures must use the same fitting surface")
        return self
