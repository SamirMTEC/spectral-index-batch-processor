"""Add asynchronous scientific processing state and artifacts.

Revision ID: 20260831_0006
Revises: 20260831_0005
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260831_0006"
down_revision: str | None = "20260831_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

uuid = postgresql.UUID(as_uuid=True)
timestamp = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.execute("ALTER TABLE processing_items DROP CONSTRAINT IF EXISTS processingitemstate")
    op.execute("UPDATE processing_items SET state = 'pending' WHERE state = 'queued'")
    op.execute("UPDATE processing_items SET state = 'completed' WHERE state = 'succeeded'")
    op.execute("UPDATE processing_items SET state = 'skipped' WHERE state = 'cancelled'")
    op.create_check_constraint(
        "processingitemstate",
        "processing_items",
        "state IN ('pending','running','completed','skipped','failed')",
    )

    with op.batch_alter_table("processing_jobs") as batch:
        batch.add_column(sa.Column("idempotency_key", sa.String(200)))
        batch.add_column(sa.Column("request_hash", sa.String(64), nullable=False, server_default=""))
        for name in ("total_items", "processed_items", "completed_items", "failed_items", "skipped_items"):
            batch.add_column(sa.Column(name, sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("started_at", timestamp))
        batch.add_column(sa.Column("finished_at", timestamp))
        batch.add_column(sa.Column("cancel_requested_at", timestamp))
        batch.add_column(sa.Column("error_code", sa.String(100)))
        batch.add_column(sa.Column("error_message", sa.Text()))
        batch.add_column(sa.Column("internal_traceback", sa.Text()))
        batch.create_unique_constraint(
            "uq_processing_job_idempotency", ["owner_id", "idempotency_key"]
        )
        batch.create_index("ix_processing_jobs_request_hash", ["request_hash"])

    with op.batch_alter_table("processing_items") as batch:
        batch.add_column(sa.Column("roi_version_id", uuid))
        batch.add_column(sa.Column("index_name", sa.String(32), nullable=False, server_default="NDVI"))
        batch.add_column(sa.Column("parameters_snapshot", sa.JSON(), nullable=False, server_default="{}"))
        batch.add_column(sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"))
        batch.add_column(sa.Column("celery_task_id", sa.String(255)))
        batch.add_column(sa.Column("started_at", timestamp))
        batch.add_column(sa.Column("finished_at", timestamp))
        batch.add_column(sa.Column("error_code", sa.String(100)))
        batch.add_column(sa.Column("error_message", sa.Text()))
        batch.add_column(sa.Column("error_file", sa.String(500)))
        batch.add_column(sa.Column("error_capture", sa.String(200)))
        batch.add_column(sa.Column("internal_traceback", sa.Text()))
        batch.create_foreign_key(
            "fk_processing_items_roi_version_id",
            "roi_versions",
            ["roi_version_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_unique_constraint("uq_job_capture_index", ["job_id", "capture_id", "index_name"])

    with op.batch_alter_table("index_results") as batch:
        batch.add_column(sa.Column("object_version_id", sa.String(255)))
        batch.add_column(sa.Column("index_name", sa.String(32), nullable=False, server_default="NDVI"))
        batch.add_column(sa.Column("dtype", sa.String(32), nullable=False, server_default="float32"))
        batch.add_column(sa.Column("nodata", sa.String(64), nullable=False, server_default="nan"))
        batch.add_column(sa.Column("width", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("height", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("crs", sa.Text()))
        batch.add_column(sa.Column("transform", sa.JSON()))
        batch.add_column(sa.Column("valid_pixel_count", sa.BigInteger(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("invalid_pixel_count", sa.BigInteger(), nullable=False, server_default="0"))

    op.create_table(
        "statistics",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("project_id", uuid, sa.ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("owner_id", uuid, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("dataset_id", uuid, sa.ForeignKey("datasets.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("index_result_id", uuid, sa.ForeignKey("index_results.id", ondelete="RESTRICT"), nullable=False, unique=True),
        sa.Column("count", sa.BigInteger(), nullable=False),
        *[sa.Column(name, sa.Float()) for name in ("mean", "median", "min", "max", "std", "p05", "p25", "p75", "p95")],
        sa.Column("created_at", timestamp, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", timestamp, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("ix_statistics_owner_id", "statistics", ["owner_id"])
    op.create_index("ix_statistics_project_id", "statistics", ["project_id"])
    op.create_index("ix_statistics_dataset_id", "statistics", ["dataset_id"])

    with op.batch_alter_table("export_artifacts") as batch:
        batch.add_column(sa.Column("processing_item_id", uuid))
        batch.add_column(sa.Column("object_version_id", sa.String(255)))
        batch.add_column(sa.Column("size_bytes", sa.BigInteger()))
        batch.add_column(sa.Column("checksum_sha256", sa.String(64)))
        batch.create_foreign_key(
            "fk_export_artifacts_processing_item_id",
            "processing_items",
            ["processing_item_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_index("ix_export_artifacts_processing_item_id", ["processing_item_id"])


def downgrade() -> None:
    with op.batch_alter_table("export_artifacts") as batch:
        batch.drop_index("ix_export_artifacts_processing_item_id")
        batch.drop_constraint("fk_export_artifacts_processing_item_id", type_="foreignkey")
        for name in ("checksum_sha256", "size_bytes", "object_version_id", "processing_item_id"):
            batch.drop_column(name)
    op.drop_table("statistics")
    with op.batch_alter_table("index_results") as batch:
        for name in ("invalid_pixel_count", "valid_pixel_count", "transform", "crs", "height", "width", "nodata", "dtype", "index_name", "object_version_id"):
            batch.drop_column(name)
    with op.batch_alter_table("processing_items") as batch:
        batch.drop_constraint("uq_job_capture_index", type_="unique")
        batch.drop_constraint("fk_processing_items_roi_version_id", type_="foreignkey")
        for name in ("internal_traceback", "error_capture", "error_file", "error_message", "error_code", "finished_at", "started_at", "celery_task_id", "max_attempts", "attempts", "parameters_snapshot", "index_name", "roi_version_id"):
            batch.drop_column(name)
    op.execute("ALTER TABLE processing_items DROP CONSTRAINT IF EXISTS processingitemstate")
    op.create_check_constraint(
        "processingitemstate",
        "processing_items",
        "state IN ('queued','running','succeeded','failed','cancelled','skipped')",
    )
    with op.batch_alter_table("processing_jobs") as batch:
        batch.drop_index("ix_processing_jobs_request_hash")
        batch.drop_constraint("uq_processing_job_idempotency", type_="unique")
        for name in ("internal_traceback", "error_message", "error_code", "cancel_requested_at", "finished_at", "started_at", "skipped_items", "failed_items", "completed_items", "processed_items", "total_items", "request_hash", "idempotency_key"):
            batch.drop_column(name)
