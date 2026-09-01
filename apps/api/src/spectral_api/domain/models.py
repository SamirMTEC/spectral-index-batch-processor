"""SQLAlchemy models for the initial SpectralIndex domain."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base metadata owned by the SpectralIndex control plane."""


class TimestampMixin:
    """UTC creation and update timestamps for mutable resources."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ProjectStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class DatasetState(StrEnum):
    DRAFT = "draft"
    UPLOADING = "uploading"
    INGESTING = "ingesting"
    NEEDS_REVIEW = "needs_review"
    READY = "ready"
    INVALID = "invalid"
    ARCHIVED = "archived"


class UploadMode(StrEnum):
    FILES = "files"
    DIRECTORY = "directory"
    ZIP = "zip"


class UploadSessionState(StrEnum):
    PENDING = "pending"
    UPLOADING = "uploading"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class UploadObjectState(StrEnum):
    PENDING = "pending"
    UPLOADING = "uploading"
    UPLOADED = "uploaded"
    VALIDATING = "validating"
    VALID = "valid"
    INVALID = "invalid"
    FAILED = "failed"


class AssetRole(StrEnum):
    SCIENTIFIC = "scientific"
    AUXILIARY = "auxiliary"
    ARCHIVE = "archive"


class RadiometryMode(StrEnum):
    UNKNOWN = "unknown"
    DIGITAL_NUMBER = "digital_number"
    REFLECTANCE = "reflectance"


class BandAssetState(StrEnum):
    VALIDATING = "validating"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    QUARANTINED = "quarantined"
    ARCHIVED = "archived"


class ROIState(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class AlignmentStatus(StrEnum):
    UNKNOWN = "unknown"
    COMPATIBLE = "compatible"
    REQUIRES_TRANSFORM = "requires_transform"
    INCOMPATIBLE = "incompatible"


class ROIGeometryType(StrEnum):
    RECTANGLE = "rectangle"
    POLYGON = "polygon"


class ProcessingJobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    COMPLETED_WITH_ERRORS = "completed_with_errors"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ProcessingItemState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class ExportArtifactState(StrEnum):
    QUEUED = "queued"
    GENERATING = "generating"
    READY = "ready"
    FAILED = "failed"
    EXPIRED = "expired"


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)


class Project(TimestampMixin, Base):
    __tablename__ = "projects"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        index=True,
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[ProjectStatus] = mapped_column(
        Enum(
            ProjectStatus,
            values_callable=lambda enum: [member.value for member in enum],
            native_enum=False,
            create_constraint=True,
        ),
        default=ProjectStatus.ACTIVE,
        nullable=False,
        index=True,
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BandSequenceProfile(TimestampMixin, Base):
    """A researcher-owned mapping from numeric file suffixes to spectral bands."""

    __tablename__ = "band_sequence_profiles"
    __table_args__ = (
        UniqueConstraint("owner_id", "name", name="uq_band_sequence_profile_owner_name"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    filename_prefix: Mapped[str] = mapped_column(String(160), nullable=False)
    capture_prefix: Mapped[str] = mapped_column(String(160), nullable=False)
    band_order: Mapped[list[str]] = mapped_column(JSON, nullable=False)


class Dataset(TimestampMixin, Base):
    __tablename__ = "datasets"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("projects.id", ondelete="RESTRICT"),
        index=True,
        nullable=False,
    )
    owner_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        index=True,
        nullable=False,
    )
    band_sequence_profile_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("band_sequence_profiles.id", ondelete="RESTRICT"),
        index=True,
        nullable=True,
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    crop: Mapped[str | None] = mapped_column(String(160), nullable=True)
    experiment: Mapped[str | None] = mapped_column(String(160), nullable=True)
    location: Mapped[str | None] = mapped_column(String(300), nullable=True)
    sensor: Mapped[str | None] = mapped_column(String(160), nullable=True)
    radiometry_mode: Mapped[RadiometryMode] = mapped_column(
        Enum(
            RadiometryMode,
            values_callable=lambda enum: [member.value for member in enum],
            native_enum=False,
            create_constraint=True,
        ),
        default=RadiometryMode.UNKNOWN,
        nullable=False,
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    state: Mapped[DatasetState] = mapped_column(
        Enum(
            DatasetState,
            values_callable=lambda enum: [member.value for member in enum],
            native_enum=False,
            create_constraint=True,
        ),
        default=DatasetState.DRAFT,
        nullable=False,
        index=True,
    )


class ProjectOwnedResource(TimestampMixin):
    """Common ownership and project scope for non-root domain resources."""

    project_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("projects.id", ondelete="RESTRICT"),
        index=True,
        nullable=False,
    )
    owner_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        index=True,
        nullable=False,
    )


class UploadSession(ProjectOwnedResource, Base):
    __tablename__ = "upload_sessions"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    dataset_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("datasets.id", ondelete="RESTRICT"),
        index=True,
        nullable=False,
    )
    mode: Mapped[UploadMode] = mapped_column(
        Enum(
            UploadMode,
            values_callable=lambda enum: [member.value for member in enum],
            native_enum=False,
            create_constraint=True,
        ),
        nullable=False,
    )
    state: Mapped[UploadSessionState] = mapped_column(
        Enum(
            UploadSessionState,
            values_callable=lambda enum: [member.value for member in enum],
            native_enum=False,
            create_constraint=True,
        ),
        default=UploadSessionState.PENDING,
        nullable=False,
        index=True,
    )
    total_files: Mapped[int] = mapped_column(Integer, nullable=False)
    total_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UploadObject(ProjectOwnedResource, Base):
    __tablename__ = "upload_objects"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    upload_session_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("upload_sessions.id", ondelete="RESTRICT"),
        index=True,
        nullable=False,
    )
    dataset_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("datasets.id", ondelete="RESTRICT"),
        index=True,
        nullable=False,
    )
    parent_upload_object_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("upload_objects.id", ondelete="RESTRICT"), nullable=True
    )
    client_relative_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    extension: Mapped[str] = mapped_column(String(16), nullable=False)
    declared_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    observed_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    declared_mime: Mapped[str | None] = mapped_column(String(255), nullable=True)
    detected_mime: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[AssetRole] = mapped_column(
        Enum(
            AssetRole,
            values_callable=lambda enum: [member.value for member in enum],
            native_enum=False,
            create_constraint=True,
        ),
        nullable=False,
    )
    object_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    object_version_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    multipart_upload_id: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    etag: Mapped[str | None] = mapped_column(String(255), nullable=True)
    client_checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    verified_checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    state: Mapped[UploadObjectState] = mapped_column(
        Enum(
            UploadObjectState,
            values_callable=lambda enum: [member.value for member in enum],
            native_enum=False,
            create_constraint=True,
        ),
        default=UploadObjectState.PENDING,
        nullable=False,
        index=True,
    )
    failure_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    failure_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict, nullable=False)
    upload_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Treatment(ProjectOwnedResource, Base):
    __tablename__ = "treatments"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    dataset_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("datasets.id", ondelete="RESTRICT"),
        index=True,
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    normalized_key: Mapped[str] = mapped_column(String(160), nullable=False)
    metadata_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict, nullable=False)


class Replicate(ProjectOwnedResource, Base):
    __tablename__ = "replicates"
    __table_args__ = (
        UniqueConstraint("dataset_id", "normalized_key", name="uq_replicate_dataset_key"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    dataset_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("datasets.id", ondelete="RESTRICT"),
        index=True,
        nullable=False,
    )
    treatment_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("treatments.id", ondelete="RESTRICT"),
        index=True,
        nullable=True,
    )
    label: Mapped[str] = mapped_column(String(160), nullable=False)
    normalized_key: Mapped[str] = mapped_column(String(160), nullable=False)
    ordinal: Mapped[int | None] = mapped_column(Integer, nullable=True)
    metadata_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict, nullable=False)


class Capture(ProjectOwnedResource, Base):
    __tablename__ = "captures"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    dataset_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("datasets.id", ondelete="RESTRICT"),
        index=True,
        nullable=False,
    )
    treatment_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("treatments.id", ondelete="RESTRICT"),
        index=True,
        nullable=True,
    )
    replicate_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("replicates.id", ondelete="RESTRICT"),
        index=True,
        nullable=True,
    )
    capture_key: Mapped[str] = mapped_column(String(200), nullable=False)
    captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    crs: Mapped[str | None] = mapped_column(Text, nullable=True)
    transform: Mapped[list[float] | None] = mapped_column(JSON, nullable=True)
    alignment_status: Mapped[AlignmentStatus] = mapped_column(
        Enum(
            AlignmentStatus,
            values_callable=lambda enum: [member.value for member in enum],
            native_enum=False,
            create_constraint=True,
        ),
        default=AlignmentStatus.UNKNOWN,
        nullable=False,
    )


class BandAsset(ProjectOwnedResource, Base):
    __tablename__ = "band_assets"
    __table_args__ = (UniqueConstraint("dataset_id", "sha256", name="uq_band_asset_checksum"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    upload_object_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("upload_objects.id", ondelete="RESTRICT"),
        unique=True,
        nullable=True,
    )
    dataset_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("datasets.id", ondelete="RESTRICT"),
        index=True,
        nullable=False,
    )
    capture_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("captures.id", ondelete="RESTRICT"),
        index=True,
        nullable=True,
    )
    band_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    original_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    object_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    object_version_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    role: Mapped[AssetRole] = mapped_column(
        Enum(
            AssetRole,
            values_callable=lambda enum: [member.value for member in enum],
            native_enum=False,
            create_constraint=True,
        ),
        default=AssetRole.SCIENTIFIC,
        nullable=False,
    )
    extension: Mapped[str | None] = mapped_column(String(16), nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    dtype: Mapped[str | None] = mapped_column(String(64), nullable=True)
    band_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    nodata: Mapped[str | None] = mapped_column(String(255), nullable=True)
    crs: Mapped[str | None] = mapped_column(Text, nullable=True)
    transform: Mapped[list[float] | None] = mapped_column(JSON, nullable=True)
    metadata_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict, nullable=False)
    preview_object_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    preview_object_version_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    approximate_valid_fraction: Mapped[float | None] = mapped_column(nullable=True)
    immutable: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    state: Mapped[BandAssetState] = mapped_column(
        Enum(
            BandAssetState,
            values_callable=lambda enum: [member.value for member in enum],
            native_enum=False,
            create_constraint=True,
        ),
        default=BandAssetState.QUARANTINED,
        nullable=False,
    )


class ROI(ProjectOwnedResource, Base):
    __tablename__ = "rois"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    dataset_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("datasets.id", ondelete="RESTRICT"),
        index=True,
        nullable=False,
    )
    replicate_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("replicates.id", ondelete="RESTRICT"),
        index=True,
        nullable=True,
    )
    capture_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("captures.id", ondelete="RESTRICT"),
        index=True,
        nullable=True,
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    current_version_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "roi_versions.id",
            name="fk_rois_current_version_id",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        nullable=True,
    )
    # Retained as nullable compatibility columns for pre-versioning deployments.
    geometry_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    geometry: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    state: Mapped[ROIState] = mapped_column(
        Enum(
            ROIState,
            values_callable=lambda enum: [member.value for member in enum],
            native_enum=False,
            create_constraint=True,
        ),
        default=ROIState.ACTIVE,
        nullable=False,
    )


class ROIVersion(Base):
    """Immutable ROI geometry tied to the original raster grid."""

    __tablename__ = "roi_versions"
    __table_args__ = (UniqueConstraint("roi_id", "version_number", name="uq_roi_version"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    roi_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("rois.id", ondelete="RESTRICT"),
        index=True,
        nullable=False,
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    geometry_type: Mapped[ROIGeometryType] = mapped_column(
        Enum(
            ROIGeometryType,
            values_callable=lambda enum: [member.value for member in enum],
            native_enum=False,
            create_constraint=True,
        ),
        nullable=False,
    )
    normalized_coordinates: Mapped[list[list[float]]] = mapped_column(JSON, nullable=False)
    pixel_coordinates: Mapped[list[list[float]]] = mapped_column(JSON, nullable=False)
    reference_capture_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("captures.id", ondelete="RESTRICT"),
        nullable=False,
    )
    reference_asset_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("band_assets.id", ondelete="RESTRICT"),
        nullable=False,
    )
    reference_width: Mapped[int] = mapped_column(Integer, nullable=False)
    reference_height: Mapped[int] = mapped_column(Integer, nullable=False)
    crs: Mapped[str | None] = mapped_column(Text, nullable=True)
    transform: Mapped[list[float] | None] = mapped_column(JSON, nullable=True)
    spatial_coordinates: Mapped[list[list[float]] | None] = mapped_column(JSON, nullable=True)
    geometry_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    area_pixels: Mapped[float] = mapped_column(nullable=False)
    approximate_valid_pixels: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_by: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ProcessingRecipe(TimestampMixin, Base):
    """Researcher-owned immutable scientific configuration reusable across datasets."""

    __tablename__ = "processing_recipes"
    __table_args__ = (UniqueConstraint("owner_id", "name", name="uq_processing_recipe_owner_name"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    definition_snapshot: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    definition_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ProcessingJob(ProjectOwnedResource, Base):
    __tablename__ = "processing_jobs"
    __table_args__ = (
        UniqueConstraint("owner_id", "idempotency_key", name="uq_processing_job_idempotency"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    dataset_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("datasets.id", ondelete="RESTRICT"),
        index=True,
        nullable=False,
    )
    parent_job_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("processing_jobs.id", ondelete="RESTRICT"),
        index=True,
        nullable=True,
    )
    recipe_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("processing_recipes.id", ondelete="RESTRICT"),
        index=True,
        nullable=True,
    )
    state: Mapped[ProcessingJobState] = mapped_column(
        Enum(
            ProcessingJobState,
            values_callable=lambda enum: [member.value for member in enum],
            native_enum=False,
            create_constraint=True,
        ),
        default=ProcessingJobState.QUEUED,
        nullable=False,
        index=True,
    )
    configuration_snapshot: Mapped[dict[str, object]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    reproducibility_snapshot: Mapped[dict[str, object]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    snapshot_schema_version: Mapped[str] = mapped_column(
        String(32), default="legacy", nullable=False
    )
    snapshot_checksum: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    software_version: Mapped[str] = mapped_column(String(64), default="0.1.0", nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    total_items: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    processed_items: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completed_items: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_items: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    skipped_items: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    internal_traceback: Mapped[str | None] = mapped_column(Text, nullable=True)


class ProcessingItem(ProjectOwnedResource, Base):
    __tablename__ = "processing_items"
    __table_args__ = (
        UniqueConstraint("job_id", "capture_id", "index_name", name="uq_job_capture_index"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    job_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("processing_jobs.id", ondelete="RESTRICT"),
        index=True,
        nullable=False,
    )
    dataset_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("datasets.id", ondelete="RESTRICT"),
        index=True,
        nullable=False,
    )
    capture_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("captures.id", ondelete="RESTRICT"),
        nullable=True,
    )
    roi_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("rois.id", ondelete="RESTRICT"),
        nullable=True,
    )
    roi_version_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("roi_versions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    index_name: Mapped[str] = mapped_column(String(32), nullable=False)
    formula_version: Mapped[str] = mapped_column(String(32), default="legacy", nullable=False)
    formula_expression: Mapped[str] = mapped_column(Text, default="", nullable=False)
    parameters_snapshot: Mapped[dict[str, object]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    input_snapshot: Mapped[list[dict[str, object]]] = mapped_column(
        JSON, default=list, nullable=False
    )
    band_mapping_snapshot: Mapped[dict[str, object]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    roi_snapshot: Mapped[dict[str, object]] = mapped_column(JSON, default=dict, nullable=False)
    state: Mapped[ProcessingItemState] = mapped_column(
        Enum(
            ProcessingItemState,
            values_callable=lambda enum: [member.value for member in enum],
            native_enum=False,
            create_constraint=True,
        ),
        default=ProcessingItemState.PENDING,
        nullable=False,
        index=True,
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    celery_task_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_file: Mapped[str | None] = mapped_column(String(500), nullable=True)
    error_capture: Mapped[str | None] = mapped_column(String(200), nullable=True)
    internal_traceback: Mapped[str | None] = mapped_column(Text, nullable=True)


class IndexResult(ProjectOwnedResource, Base):
    __tablename__ = "index_results"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    dataset_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("datasets.id", ondelete="RESTRICT"),
        index=True,
        nullable=False,
    )
    processing_item_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("processing_items.id", ondelete="RESTRICT"),
        unique=True,
        nullable=False,
    )
    object_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    object_version_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    index_name: Mapped[str] = mapped_column(String(32), nullable=False)
    formula_version: Mapped[str] = mapped_column(String(32), default="legacy", nullable=False)
    software_version: Mapped[str] = mapped_column(String(64), default="0.1.0", nullable=False)
    dtype: Mapped[str] = mapped_column(String(32), default="float32", nullable=False)
    nodata: Mapped[str] = mapped_column(String(64), default="nan", nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    crs: Mapped[str | None] = mapped_column(Text, nullable=True)
    transform: Mapped[list[float] | None] = mapped_column(JSON, nullable=True)
    valid_pixel_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    invalid_pixel_count: Mapped[int] = mapped_column(BigInteger, nullable=False)


class Statistics(ProjectOwnedResource, Base):
    __tablename__ = "statistics"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    dataset_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("datasets.id", ondelete="RESTRICT"), index=True
    )
    index_result_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("index_results.id", ondelete="RESTRICT"),
        unique=True,
        nullable=False,
    )
    count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    mean: Mapped[float | None] = mapped_column(Float, nullable=True)
    median: Mapped[float | None] = mapped_column(Float, nullable=True)
    min: Mapped[float | None] = mapped_column(Float, nullable=True)
    max: Mapped[float | None] = mapped_column(Float, nullable=True)
    std: Mapped[float | None] = mapped_column(Float, nullable=True)
    p05: Mapped[float | None] = mapped_column(Float, nullable=True)
    p25: Mapped[float | None] = mapped_column(Float, nullable=True)
    p75: Mapped[float | None] = mapped_column(Float, nullable=True)
    p95: Mapped[float | None] = mapped_column(Float, nullable=True)


class ExportArtifact(ProjectOwnedResource, Base):
    __tablename__ = "export_artifacts"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    dataset_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("datasets.id", ondelete="RESTRICT"),
        index=True,
        nullable=False,
    )
    job_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("processing_jobs.id", ondelete="RESTRICT"),
        index=True,
        nullable=True,
    )
    processing_item_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("processing_items.id", ondelete="RESTRICT"),
        index=True,
        nullable=True,
    )
    artifact_type: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[ExportArtifactState] = mapped_column(
        Enum(
            ExportArtifactState,
            values_callable=lambda enum: [member.value for member in enum],
            native_enum=False,
            create_constraint=True,
        ),
        default=ExportArtifactState.QUEUED,
        nullable=False,
        index=True,
    )
    object_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    object_version_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    checksum_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
