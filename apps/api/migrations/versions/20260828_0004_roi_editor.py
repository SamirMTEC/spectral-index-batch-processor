"""Add explicit replicates, raster previews, and immutable ROI versions.

Revision ID: 20260828_0004
Revises: 20260828_0003
Create Date: 2026-08-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260828_0004"
down_revision: str | None = "20260828_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

uuid = postgresql.UUID(as_uuid=True)
timestamp = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "replicates",
        sa.Column("id", uuid, primary_key=True, nullable=False),
        sa.Column("project_id", uuid, sa.ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("owner_id", uuid, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("dataset_id", uuid, sa.ForeignKey("datasets.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("treatment_id", uuid, sa.ForeignKey("treatments.id", ondelete="RESTRICT")),
        sa.Column("label", sa.String(length=160), nullable=False),
        sa.Column("normalized_key", sa.String(length=160), nullable=False),
        sa.Column("ordinal", sa.Integer()),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", timestamp, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", timestamp, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("dataset_id", "normalized_key", name="uq_replicate_dataset_key"),
    )
    for column in ("project_id", "owner_id", "dataset_id", "treatment_id"):
        op.create_index(f"ix_replicates_{column}", "replicates", [column])

    with op.batch_alter_table("captures") as batch:
        batch.add_column(sa.Column("replicate_id", uuid))
        batch.add_column(sa.Column("width", sa.Integer()))
        batch.add_column(sa.Column("height", sa.Integer()))
        batch.add_column(sa.Column("crs", sa.Text()))
        batch.add_column(sa.Column("transform", sa.JSON()))
        batch.add_column(
            sa.Column(
                "alignment_status",
                sa.Enum(
                    "unknown",
                    "compatible",
                    "requires_transform",
                    "incompatible",
                    name="alignmentstatus",
                    native_enum=False,
                    create_constraint=True,
                ),
                nullable=False,
                server_default="unknown",
            )
        )
        batch.create_foreign_key(
            "fk_captures_replicate_id",
            "replicates",
            ["replicate_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_index("ix_captures_replicate_id", ["replicate_id"])

    with op.batch_alter_table("band_assets") as batch:
        batch.add_column(sa.Column("preview_object_key", sa.String(length=1024)))
        batch.add_column(sa.Column("preview_object_version_id", sa.String(length=255)))
        batch.add_column(sa.Column("approximate_valid_fraction", sa.Float()))

    with op.batch_alter_table("rois") as batch:
        batch.alter_column("geometry_type", existing_type=sa.String(length=32), nullable=True)
        batch.alter_column("geometry", existing_type=sa.JSON(), nullable=True)
        batch.add_column(sa.Column("replicate_id", uuid))
        batch.add_column(sa.Column("capture_id", uuid))
        batch.add_column(sa.Column("description", sa.Text()))
        batch.add_column(sa.Column("current_version_id", uuid))
        batch.create_foreign_key(
            "fk_rois_replicate_id",
            "replicates",
            ["replicate_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_foreign_key(
            "fk_rois_capture_id",
            "captures",
            ["capture_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_index("ix_rois_replicate_id", ["replicate_id"])
        batch.create_index("ix_rois_capture_id", ["capture_id"])

    op.create_table(
        "roi_versions",
        sa.Column("id", uuid, primary_key=True, nullable=False),
        sa.Column("roi_id", uuid, sa.ForeignKey("rois.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column(
            "geometry_type",
            sa.Enum(
                "rectangle",
                "polygon",
                name="roigeometrytype",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("normalized_coordinates", sa.JSON(), nullable=False),
        sa.Column("pixel_coordinates", sa.JSON(), nullable=False),
        sa.Column("reference_capture_id", uuid, sa.ForeignKey("captures.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("reference_asset_id", uuid, sa.ForeignKey("band_assets.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("reference_width", sa.Integer(), nullable=False),
        sa.Column("reference_height", sa.Integer(), nullable=False),
        sa.Column("crs", sa.Text()),
        sa.Column("transform", sa.JSON()),
        sa.Column("spatial_coordinates", sa.JSON()),
        sa.Column("geometry_checksum", sa.String(length=64), nullable=False),
        sa.Column("area_pixels", sa.Float(), nullable=False),
        sa.Column("approximate_valid_pixels", sa.BigInteger()),
        sa.Column("created_by", uuid, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", timestamp, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("roi_id", "version_number", name="uq_roi_version"),
    )
    op.create_index("ix_roi_versions_roi_id", "roi_versions", ["roi_id"])
    op.create_foreign_key(
        "fk_rois_current_version_id",
        "rois",
        "roi_versions",
        ["current_version_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint("fk_rois_current_version_id", "rois", type_="foreignkey")
    op.drop_table("roi_versions")
    with op.batch_alter_table("rois") as batch:
        batch.drop_index("ix_rois_capture_id")
        batch.drop_index("ix_rois_replicate_id")
        batch.drop_constraint("fk_rois_capture_id", type_="foreignkey")
        batch.drop_constraint("fk_rois_replicate_id", type_="foreignkey")
        batch.drop_column("current_version_id")
        batch.drop_column("description")
        batch.drop_column("capture_id")
        batch.drop_column("replicate_id")
        batch.alter_column("geometry", existing_type=sa.JSON(), nullable=False)
        batch.alter_column("geometry_type", existing_type=sa.String(length=32), nullable=False)
    with op.batch_alter_table("band_assets") as batch:
        batch.drop_column("approximate_valid_fraction")
        batch.drop_column("preview_object_version_id")
        batch.drop_column("preview_object_key")
    with op.batch_alter_table("captures") as batch:
        batch.drop_index("ix_captures_replicate_id")
        batch.drop_constraint("fk_captures_replicate_id", type_="foreignkey")
        batch.drop_column("alignment_status")
        batch.drop_column("transform")
        batch.drop_column("crs")
        batch.drop_column("height")
        batch.drop_column("width")
        batch.drop_column("replicate_id")
    op.drop_table("replicates")
