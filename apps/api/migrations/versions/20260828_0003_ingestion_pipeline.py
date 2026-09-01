"""Add direct-upload sessions, per-file state, and extracted raster metadata.

Revision ID: 20260828_0003
Revises: 20260828_0002
Create Date: 2026-08-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260828_0003"
down_revision: str | None = "20260828_0002"
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
    op.create_table(
        "upload_sessions",
        sa.Column("id", uuid, primary_key=True, nullable=False),
        sa.Column("project_id", uuid, sa.ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("owner_id", uuid, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("dataset_id", uuid, sa.ForeignKey("datasets.id", ondelete="RESTRICT"), nullable=False),
        sa.Column(
            "mode",
            sa.Enum("files", "directory", "zip", name="uploadmode", native_enum=False, create_constraint=True),
            nullable=False,
        ),
        sa.Column(
            "state",
            sa.Enum(
                "pending", "uploading", "completed", "failed", "cancelled",
                name="uploadsessionstate", native_enum=False, create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("total_files", sa.Integer(), nullable=False),
        sa.Column("total_bytes", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", timestamp, nullable=False),
        *timestamps(),
    )
    for column in ("project_id", "owner_id", "dataset_id", "state"):
        op.create_index(f"ix_upload_sessions_{column}", "upload_sessions", [column])

    op.create_table(
        "upload_objects",
        sa.Column("id", uuid, primary_key=True, nullable=False),
        sa.Column("project_id", uuid, sa.ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("owner_id", uuid, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("upload_session_id", uuid, sa.ForeignKey("upload_sessions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("dataset_id", uuid, sa.ForeignKey("datasets.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("parent_upload_object_id", uuid, sa.ForeignKey("upload_objects.id", ondelete="RESTRICT")),
        sa.Column("client_relative_path", sa.String(length=1024), nullable=False),
        sa.Column("filename", sa.String(length=500), nullable=False),
        sa.Column("extension", sa.String(length=16), nullable=False),
        sa.Column("declared_size", sa.BigInteger(), nullable=False),
        sa.Column("observed_size", sa.BigInteger()),
        sa.Column("declared_mime", sa.String(length=255)),
        sa.Column("detected_mime", sa.String(length=255)),
        sa.Column(
            "role",
            sa.Enum("scientific", "auxiliary", "archive", name="assetrole", native_enum=False, create_constraint=True),
            nullable=False,
        ),
        sa.Column("object_key", sa.String(length=1024)),
        sa.Column("object_version_id", sa.String(length=255)),
        sa.Column("multipart_upload_id", sa.String(length=1024)),
        sa.Column("etag", sa.String(length=255)),
        sa.Column("client_checksum", sa.String(length=64)),
        sa.Column("verified_checksum", sa.String(length=64)),
        sa.Column(
            "state",
            sa.Enum(
                "pending", "uploading", "uploaded", "validating", "valid", "invalid", "failed",
                name="uploadobjectstate", native_enum=False, create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("failure_code", sa.String(length=100)),
        sa.Column("failure_message", sa.Text()),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("upload_attempts", sa.Integer(), nullable=False),
        sa.Column("uploaded_at", timestamp),
        *timestamps(),
        sa.UniqueConstraint("upload_session_id", "client_relative_path", name="uq_upload_object_path"),
    )
    for column in ("project_id", "owner_id", "upload_session_id", "dataset_id", "state"):
        op.create_index(f"ix_upload_objects_{column}", "upload_objects", [column])
    op.create_index(
        "ix_upload_objects_dataset_checksum",
        "upload_objects",
        ["dataset_id", "verified_checksum"],
    )

    with op.batch_alter_table("band_assets") as batch:
        batch.add_column(sa.Column("upload_object_id", uuid))
        batch.add_column(sa.Column("object_version_id", sa.String(length=255)))
        batch.add_column(sa.Column("size_bytes", sa.BigInteger()))
        batch.add_column(
            sa.Column(
                "role",
                sa.Enum("scientific", "auxiliary", "archive", name="bandassetrole", native_enum=False, create_constraint=True),
                nullable=False,
                server_default="scientific",
            )
        )
        batch.add_column(sa.Column("extension", sa.String(length=16)))
        batch.add_column(sa.Column("width", sa.Integer()))
        batch.add_column(sa.Column("height", sa.Integer()))
        batch.add_column(sa.Column("dtype", sa.String(length=64)))
        batch.add_column(sa.Column("band_count", sa.Integer()))
        batch.add_column(sa.Column("nodata", sa.String(length=255)))
        batch.add_column(sa.Column("crs", sa.Text()))
        batch.add_column(sa.Column("transform", sa.JSON()))
        batch.add_column(sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
        batch.add_column(sa.Column("immutable", sa.Boolean(), nullable=False, server_default=sa.true()))
        batch.create_foreign_key(
            "fk_band_assets_upload_object_id", "upload_objects", ["upload_object_id"], ["id"], ondelete="RESTRICT"
        )
        batch.create_unique_constraint("uq_band_assets_upload_object_id", ["upload_object_id"])
    op.create_unique_constraint(
        "uq_band_asset_checksum", "band_assets", ["dataset_id", "sha256"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_band_asset_checksum", "band_assets", type_="unique")
    with op.batch_alter_table("band_assets") as batch:
        batch.drop_constraint("uq_band_assets_upload_object_id", type_="unique")
        batch.drop_constraint("fk_band_assets_upload_object_id", type_="foreignkey")
        for column in (
            "immutable", "metadata_json", "transform", "crs", "nodata", "band_count", "dtype",
            "height", "width", "extension", "role", "size_bytes", "object_version_id", "upload_object_id",
        ):
            batch.drop_column(column)
    op.drop_table("upload_objects")
    op.drop_table("upload_sessions")
