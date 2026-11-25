"""Panel library and auditable calibration runs.

Revision ID: 20261110_0010
Revises: 20260904_0009
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261110_0010"
down_revision: str | None = "20260904_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "calibration_panels",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "owner_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("manufacturer", sa.String(160)),
        sa.Column("model", sa.String(160)),
        sa.Column("serial_number", sa.String(160)),
        sa.Column("calibrated_at", sa.DateTime(timezone=True)),
        sa.Column("certificate_key", sa.String(1024)),
        sa.Column("certificate_sha256", sa.String(64)),
        sa.Column("values_json", sa.JSON(), nullable=False),
        sa.Column("source_json", sa.JSON(), nullable=False),
        sa.Column("notes", sa.Text()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_calibration_panels_owner_id", "calibration_panels", ["owner_id"])
    op.create_table(
        "calibration_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "owner_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("projects.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "dataset_id",
            sa.Uuid(),
            sa.ForeignKey("datasets.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "capture_id",
            sa.Uuid(),
            sa.ForeignKey("captures.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "panel_id",
            sa.Uuid(),
            sa.ForeignKey("calibration_panels.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("method", sa.String(24), nullable=False),
        sa.Column("input_mode", sa.String(24), nullable=False),
        sa.Column("config_json", sa.JSON(), nullable=False),
        sa.Column("manifest_json", sa.JSON(), nullable=False),
        sa.Column("result_json", sa.JSON()),
        sa.Column("output_key", sa.String(1024)),
        sa.Column("error", sa.Text()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    for name in ("owner_id", "project_id", "dataset_id", "capture_id", "panel_id"):
        op.create_index(f"ix_calibration_runs_{name}", "calibration_runs", [name])


def downgrade() -> None:
    op.drop_table("calibration_runs")
    op.drop_table("calibration_panels")
