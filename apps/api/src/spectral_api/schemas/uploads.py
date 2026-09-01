"""Direct-upload API contracts."""

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from spectral_api.domain.models import AssetRole, UploadMode, UploadObjectState, UploadSessionState
from spectral_api.services.uploads import normalize_relative_path


class UploadFileManifest(BaseModel):
    relative_path: str = Field(min_length=1, max_length=1024)
    size_bytes: int = Field(ge=1)
    content_type: str | None = Field(default=None, max_length=255)
    sha256: str | None = Field(default=None, pattern=r"^[a-fA-F0-9]{64}$")

    @field_validator("relative_path")
    @classmethod
    def reject_unsafe_path(cls, value: str) -> str:
        return normalize_relative_path(value)


class UploadSessionCreate(BaseModel):
    mode: UploadMode
    files: list[UploadFileManifest] = Field(min_length=1, max_length=100_000)


class UploadPart(BaseModel):
    part_number: int = Field(ge=1, le=10_000)
    etag: str = Field(min_length=1, max_length=255)


class UploadComplete(BaseModel):
    parts: list[UploadPart] = Field(default_factory=list, max_length=10_000)
    client_sha256: str | None = Field(default=None, pattern=r"^[a-fA-F0-9]{64}$")

    @field_validator("parts")
    @classmethod
    def unique_ordered_parts(cls, values: list[UploadPart]) -> list[UploadPart]:
        numbers = [value.part_number for value in values]
        if numbers != sorted(set(numbers)):
            raise ValueError("multipart parts must be unique and ordered")
        return values


class UploadPartRequest(BaseModel):
    part_numbers: list[int] = Field(min_length=1, max_length=100)

    @field_validator("part_numbers")
    @classmethod
    def unique_part_numbers(cls, values: list[int]) -> list[int]:
        if len(values) != len(set(values)) or any(value < 1 or value > 10_000 for value in values):
            raise ValueError("part numbers must be unique and between 1 and 10000")
        return values


class UploadObjectResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    upload_session_id: UUID
    filename: str
    client_relative_path: str
    extension: str
    declared_size: int
    observed_size: int | None
    role: AssetRole
    state: UploadObjectState
    client_checksum: str | None
    verified_checksum: str | None
    failure_code: str | None
    failure_message: str | None
    metadata_json: dict[str, object]
    created_at: datetime
    updated_at: datetime


class UploadPlan(UploadObjectResponse):
    method: str | None = None
    upload_url: str | None = None
    part_size: int | None = None
    part_count: int | None = None


class UploadSessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    dataset_id: UUID
    mode: UploadMode
    state: UploadSessionState
    total_files: int
    total_bytes: int
    expires_at: datetime
    created_at: datetime
    updated_at: datetime
    objects: Sequence[UploadObjectResponse] = Field(default_factory=list)


class UploadSessionCreated(UploadSessionResponse):
    objects: Sequence[UploadPlan]


class UploadPartUrl(BaseModel):
    part_number: int
    upload_url: str


class UploadPartUrls(BaseModel):
    object_id: UUID
    expires_in: int
    parts: list[UploadPartUrl]
