"""Process every index for both ROI and the complete source image.

Revision ID: 20260902_0008
Revises: 20260901_0007
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260902_0008"
down_revision: str | None = "20260901_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("processing_items") as batch:
        batch.drop_constraint("uq_job_capture_index", type_="unique")
        batch.add_column(
            sa.Column(
                "analysis_scope", sa.String(20), nullable=False, server_default="roi"
            )
        )
        batch.create_check_constraint(
            "ck_processing_items_analysis_scope",
            "analysis_scope IN ('roi', 'full_image')",
        )
        batch.create_unique_constraint(
            "uq_job_capture_index_scope",
            ["job_id", "capture_id", "index_name", "analysis_scope"],
        )
        batch.create_index("ix_processing_items_analysis_scope", ["analysis_scope"])

    with op.batch_alter_table("index_results") as batch:
        batch.add_column(
            sa.Column(
                "analysis_scope", sa.String(20), nullable=False, server_default="roi"
            )
        )
        batch.create_check_constraint(
            "ck_index_results_analysis_scope",
            "analysis_scope IN ('roi', 'full_image')",
        )
        batch.create_index("ix_index_results_analysis_scope", ["analysis_scope"])

    # analysis_scope is part of the immutable scientific definition of an item.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION spectral_prevent_item_snapshot_update() RETURNS trigger AS $$
        BEGIN
          IF NEW.index_name IS DISTINCT FROM OLD.index_name
             OR NEW.analysis_scope IS DISTINCT FROM OLD.analysis_scope
             OR NEW.formula_version IS DISTINCT FROM OLD.formula_version
             OR NEW.formula_expression IS DISTINCT FROM OLD.formula_expression
             OR NEW.parameters_snapshot::text IS DISTINCT FROM OLD.parameters_snapshot::text
             OR NEW.input_snapshot::text IS DISTINCT FROM OLD.input_snapshot::text
             OR NEW.band_mapping_snapshot::text IS DISTINCT FROM OLD.band_mapping_snapshot::text
             OR NEW.roi_snapshot::text IS DISTINCT FROM OLD.roi_snapshot::text
             OR NEW.roi_version_id IS DISTINCT FROM OLD.roi_version_id THEN
            RAISE EXCEPTION 'processing item scientific snapshot is immutable';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )


def downgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION spectral_prevent_item_snapshot_update() RETURNS trigger AS $$
        BEGIN
          IF NEW.index_name IS DISTINCT FROM OLD.index_name
             OR NEW.formula_version IS DISTINCT FROM OLD.formula_version
             OR NEW.formula_expression IS DISTINCT FROM OLD.formula_expression
             OR NEW.parameters_snapshot::text IS DISTINCT FROM OLD.parameters_snapshot::text
             OR NEW.input_snapshot::text IS DISTINCT FROM OLD.input_snapshot::text
             OR NEW.band_mapping_snapshot::text IS DISTINCT FROM OLD.band_mapping_snapshot::text
             OR NEW.roi_snapshot::text IS DISTINCT FROM OLD.roi_snapshot::text
             OR NEW.roi_version_id IS DISTINCT FROM OLD.roi_version_id THEN
            RAISE EXCEPTION 'processing item scientific snapshot is immutable';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    with op.batch_alter_table("index_results") as batch:
        batch.drop_index("ix_index_results_analysis_scope")
        batch.drop_constraint("ck_index_results_analysis_scope", type_="check")
        batch.drop_column("analysis_scope")
    with op.batch_alter_table("processing_items") as batch:
        batch.drop_index("ix_processing_items_analysis_scope")
        batch.drop_constraint("uq_job_capture_index_scope", type_="unique")
        batch.drop_constraint("ck_processing_items_analysis_scope", type_="check")
        batch.drop_column("analysis_scope")
        batch.create_unique_constraint(
            "uq_job_capture_index", ["job_id", "capture_id", "index_name"]
        )
