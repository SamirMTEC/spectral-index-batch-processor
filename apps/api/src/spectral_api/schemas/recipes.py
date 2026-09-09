"""Contracts for reusable, immutable scientific processing recipes."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from spectral_api.schemas.processing import ProcessingIndexInput

StatisticName = Literal["count", "mean", "median", "min", "max", "std", "p05", "p25", "p75", "p95"]


class ProcessingRecipeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=2_000)
    indices: list[ProcessingIndexInput] = Field(min_length=1, max_length=9)
    statistics: list[StatisticName] = Field(min_length=1, max_length=10)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Recipe name cannot be blank")
        return normalized

    @field_validator("statistics")
    @classmethod
    def unique_statistics(cls, values: list[StatisticName]) -> list[StatisticName]:
        if len(values) != len(set(values)):
            raise ValueError("Statistics cannot be repeated")
        return values


class RecipeIndexResponse(BaseModel):
    index: str
    formula_version: str
    formula: str
    required_bands: list[str]
    parameters: dict[str, float]
    scientific_note: str | None = None


class ProcessingRecipeResponse(BaseModel):
    id: UUID
    name: str
    description: str | None
    schema_version: str
    definition_checksum: str
    indices: list[RecipeIndexResponse]
    statistics: list[str]
    created_at: datetime
    updated_at: datetime
