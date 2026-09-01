"""Create the initial persisted SpectralIndex domain.

Revision ID: 20260828_0002
Revises: 20260828_0001
Create Date: 2026-08-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260828_0002"
down_revision: str | None = "20260828_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

uuid = postgresql.UUID(as_uuid=True)
timestamp = sa.DateTime(timezone=True)


def timestamps() -> list[sa.Column[object]]:
    return [
        sa.Column("created_at", timestamp, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", timestamp, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    ]


def upgrade() -> None:
    """Create owner-scoped tables and their query indexes."""
    op.create_table(
        "users",
        sa.Column("id", uuid, primary_key=True, nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False, unique=True),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        *timestamps(),
    )
    op.create_index("ix_users_email", "users", ["email"])

    op.create_table(
        "projects",
        sa.Column("id", uuid, primary_key=True, nullable=False),
        sa.Column("owner_id", uuid, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Enum("active", "archived", name="projectstatus", native_enum=False, create_constraint=True),
            nullable=False,
        ),
        sa.Column("archived_at", timestamp, nullable=True),
        *timestamps(),
    )
    op.create_index("ix_projects_owner_id", "projects", ["owner_id"])
    op.create_index("ix_projects_status", "projects", ["status"])

    op.create_table(
        "datasets",
        sa.Column("id", uuid, primary_key=True, nullable=False),
        sa.Column("project_id", uuid, sa.ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("owner_id", uuid, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("crop", sa.String(length=160), nullable=True),
        sa.Column("experiment", sa.String(length=160), nullable=True),
        sa.Column("location", sa.String(length=300), nullable=True),
        sa.Column("sensor", sa.String(length=160), nullable=True),
        sa.Column(
            "radiometry_mode",
            sa.Enum(
                "unknown", "digital_number", "reflectance",
                name="radiometrymode", native_enum=False, create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "state",
            sa.Enum(
                "draft", "uploading", "ingesting", "needs_review", "ready", "invalid", "archived",
                name="datasetstate", native_enum=False, create_constraint=True,
            ),
            nullable=False,
        ),
        *timestamps(),
    )
    for column in ("project_id", "owner_id", "state"):
        op.create_index(f"ix_datasets_{column}", "datasets", [column])

    _create_treatments()
    _create_captures()
    _create_band_assets()
    _create_rois()
    _create_processing_jobs()
    _create_processing_items()
    _create_index_results()
    _create_export_artifacts()


def owned_columns() -> list[sa.Column[object]]:
    return [
        sa.Column("project_id", uuid, sa.ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("owner_id", uuid, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
    ]


def create_owned_indexes(table: str, extra: tuple[str, ...] = ()) -> None:
    for column in ("project_id", "owner_id", *extra):
        op.create_index(f"ix_{table}_{column}", table, [column])


def _create_treatments() -> None:
    op.create_table(
        "treatments",
        sa.Column("id", uuid, primary_key=True, nullable=False),
        *owned_columns(),
        sa.Column("dataset_id", uuid, sa.ForeignKey("datasets.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("normalized_key", sa.String(length=160), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        *timestamps(),
    )
    create_owned_indexes("treatments", ("dataset_id",))


def _create_captures() -> None:
    op.create_table(
        "captures",
        sa.Column("id", uuid, primary_key=True, nullable=False),
        *owned_columns(),
        sa.Column("dataset_id", uuid, sa.ForeignKey("datasets.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("treatment_id", uuid, sa.ForeignKey("treatments.id", ondelete="RESTRICT")),
        sa.Column("capture_key", sa.String(length=200), nullable=False),
        sa.Column("captured_at", timestamp),
        *timestamps(),
    )
    create_owned_indexes("captures", ("dataset_id", "treatment_id"))


def _create_band_assets() -> None:
    op.create_table(
        "band_assets",
        sa.Column("id", uuid, primary_key=True, nullable=False),
        *owned_columns(),
        sa.Column("dataset_id", uuid, sa.ForeignKey("datasets.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("capture_id", uuid, sa.ForeignKey("captures.id", ondelete="RESTRICT")),
        sa.Column("band_code", sa.String(length=64)),
        sa.Column("original_filename", sa.String(length=500), nullable=False),
        sa.Column("object_key", sa.String(length=1024), nullable=False),
        sa.Column("sha256", sa.String(length=64)),
        sa.Column(
            "state",
            sa.Enum(
                "validating", "accepted", "rejected", "quarantined", "archived",
                name="bandassetstate", native_enum=False, create_constraint=True,
            ),
            nullable=False,
        ),
        *timestamps(),
    )
    create_owned_indexes("band_assets", ("dataset_id", "capture_id"))


def _create_rois() -> None:
    op.create_table(
        "rois",
        sa.Column("id", uuid, primary_key=True, nullable=False),
        *owned_columns(),
        sa.Column("dataset_id", uuid, sa.ForeignKey("datasets.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("geometry_type", sa.String(length=32), nullable=False),
        sa.Column("geometry", sa.JSON(), nullable=False),
        sa.Column(
            "state", sa.Enum("active", "archived", name="roistate", native_enum=False, create_constraint=True),
            nullable=False,
        ),
        *timestamps(),
    )
    create_owned_indexes("rois", ("dataset_id",))


def _create_processing_jobs() -> None:
    op.create_table(
        "processing_jobs",
        sa.Column("id", uuid, primary_key=True, nullable=False),
        *owned_columns(),
        sa.Column("dataset_id", uuid, sa.ForeignKey("datasets.id", ondelete="RESTRICT"), nullable=False),
        sa.Column(
            "state",
            sa.Enum(
                "queued", "running", "completed", "completed_with_errors", "failed", "cancelled",
                name="processingjobstate", native_enum=False, create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("configuration_snapshot", sa.JSON(), nullable=False),
        *timestamps(),
    )
    create_owned_indexes("processing_jobs", ("dataset_id", "state"))


def _create_processing_items() -> None:
    op.create_table(
        "processing_items",
        sa.Column("id", uuid, primary_key=True, nullable=False),
        *owned_columns(),
        sa.Column("job_id", uuid, sa.ForeignKey("processing_jobs.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("dataset_id", uuid, sa.ForeignKey("datasets.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("capture_id", uuid, sa.ForeignKey("captures.id", ondelete="RESTRICT")),
        sa.Column("roi_id", uuid, sa.ForeignKey("rois.id", ondelete="RESTRICT")),
        sa.Column(
            "state",
            sa.Enum(
                "queued", "running", "succeeded", "failed", "cancelled", "skipped",
                name="processingitemstate", native_enum=False, create_constraint=True,
            ),
            nullable=False,
        ),
        *timestamps(),
    )
    create_owned_indexes("processing_items", ("job_id", "dataset_id", "state"))


def _create_index_results() -> None:
    op.create_table(
        "index_results",
        sa.Column("id", uuid, primary_key=True, nullable=False),
        *owned_columns(),
        sa.Column("dataset_id", uuid, sa.ForeignKey("datasets.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("processing_item_id", uuid, sa.ForeignKey("processing_items.id", ondelete="RESTRICT"), nullable=False, unique=True),
        sa.Column("object_key", sa.String(length=1024), nullable=False),
        *timestamps(),
    )
    create_owned_indexes("index_results", ("dataset_id", "processing_item_id"))


def _create_export_artifacts() -> None:
    op.create_table(
        "export_artifacts",
        sa.Column("id", uuid, primary_key=True, nullable=False),
        *owned_columns(),
        sa.Column("dataset_id", uuid, sa.ForeignKey("datasets.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("job_id", uuid, sa.ForeignKey("processing_jobs.id", ondelete="RESTRICT")),
        sa.Column("artifact_type", sa.String(length=64), nullable=False),
        sa.Column(
            "state",
            sa.Enum(
                "queued", "generating", "ready", "failed", "expired",
                name="exportartifactstate", native_enum=False, create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("object_key", sa.String(length=1024)),
        *timestamps(),
    )
    create_owned_indexes("export_artifacts", ("dataset_id", "job_id", "state"))


def downgrade() -> None:
    """Drop the domain schema in dependency order."""
    for table in (
        "export_artifacts", "index_results", "processing_items", "processing_jobs", "rois",
        "band_assets", "captures", "treatments", "datasets", "projects", "users",
    ):
        op.drop_table(table)
