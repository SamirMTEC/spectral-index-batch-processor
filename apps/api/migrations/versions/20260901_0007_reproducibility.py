"""Add immutable reproducibility snapshots, recipes, and formula versions.

Revision ID: 20260901_0007
Revises: 20260831_0006
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260901_0007"
down_revision: str | None = "20260831_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

uuid = postgresql.UUID(as_uuid=True)
timestamp = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "processing_recipes",
        sa.Column("id", uuid, primary_key=True),
        sa.Column(
            "owner_id", uuid, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("definition_snapshot", sa.JSON(), nullable=False),
        sa.Column("definition_checksum", sa.String(64), nullable=False),
        sa.Column("schema_version", sa.String(32), nullable=False),
        sa.Column("archived_at", timestamp),
        sa.Column(
            "created_at", timestamp, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column(
            "updated_at", timestamp, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.UniqueConstraint("owner_id", "name", name="uq_processing_recipe_owner_name"),
    )
    op.create_index("ix_processing_recipes_owner_id", "processing_recipes", ["owner_id"])

    with op.batch_alter_table("processing_jobs") as batch:
        batch.add_column(sa.Column("parent_job_id", uuid))
        batch.add_column(sa.Column("recipe_id", uuid))
        batch.add_column(
            sa.Column("reproducibility_snapshot", sa.JSON(), nullable=False, server_default="{}")
        )
        batch.add_column(
            sa.Column(
                "snapshot_schema_version", sa.String(32), nullable=False, server_default="legacy"
            )
        )
        batch.add_column(
            sa.Column("snapshot_checksum", sa.String(64), nullable=False, server_default="")
        )
        batch.add_column(
            sa.Column("software_version", sa.String(64), nullable=False, server_default="0.1.0")
        )
        batch.create_foreign_key(
            "fk_processing_jobs_parent_job_id",
            "processing_jobs",
            ["parent_job_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_foreign_key(
            "fk_processing_jobs_recipe_id",
            "processing_recipes",
            ["recipe_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_index("ix_processing_jobs_parent_job_id", ["parent_job_id"])
        batch.create_index("ix_processing_jobs_recipe_id", ["recipe_id"])

    with op.batch_alter_table("processing_items") as batch:
        batch.add_column(
            sa.Column("formula_version", sa.String(32), nullable=False, server_default="legacy")
        )
        batch.add_column(
            sa.Column("formula_expression", sa.Text(), nullable=False, server_default="")
        )
        batch.add_column(sa.Column("input_snapshot", sa.JSON(), nullable=False, server_default="[]"))
        batch.add_column(
            sa.Column("band_mapping_snapshot", sa.JSON(), nullable=False, server_default="{}")
        )
        batch.add_column(sa.Column("roi_snapshot", sa.JSON(), nullable=False, server_default="{}"))

    with op.batch_alter_table("index_results") as batch:
        batch.add_column(
            sa.Column("formula_version", sa.String(32), nullable=False, server_default="legacy")
        )
        batch.add_column(
            sa.Column("software_version", sa.String(64), nullable=False, server_default="0.1.0")
        )

    # PostgreSQL protects scientific snapshots even from accidental future UPDATE code.
    op.execute(
        """
        CREATE FUNCTION spectral_prevent_job_snapshot_update() RETURNS trigger AS $$
        BEGIN
          IF NEW.configuration_snapshot::text IS DISTINCT FROM OLD.configuration_snapshot::text
             OR NEW.reproducibility_snapshot::text IS DISTINCT FROM OLD.reproducibility_snapshot::text
             OR NEW.snapshot_schema_version IS DISTINCT FROM OLD.snapshot_schema_version
             OR NEW.snapshot_checksum IS DISTINCT FROM OLD.snapshot_checksum
             OR NEW.software_version IS DISTINCT FROM OLD.software_version
             OR NEW.parent_job_id IS DISTINCT FROM OLD.parent_job_id
             OR NEW.recipe_id IS DISTINCT FROM OLD.recipe_id THEN
            RAISE EXCEPTION 'processing job scientific snapshot is immutable';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_processing_job_snapshot_immutable
        BEFORE UPDATE ON processing_jobs
        FOR EACH ROW EXECUTE FUNCTION spectral_prevent_job_snapshot_update()
        """
    )
    op.execute(
        """
        CREATE FUNCTION spectral_prevent_item_snapshot_update() RETURNS trigger AS $$
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
    op.execute(
        """
        CREATE TRIGGER trg_processing_item_snapshot_immutable
        BEFORE UPDATE ON processing_items
        FOR EACH ROW EXECUTE FUNCTION spectral_prevent_item_snapshot_update()
        """
    )
    op.execute(
        """
        CREATE FUNCTION spectral_prevent_recipe_definition_update() RETURNS trigger AS $$
        BEGIN
          IF NEW.definition_snapshot::text IS DISTINCT FROM OLD.definition_snapshot::text
             OR NEW.definition_checksum IS DISTINCT FROM OLD.definition_checksum
             OR NEW.schema_version IS DISTINCT FROM OLD.schema_version THEN
            RAISE EXCEPTION 'processing recipe definition is immutable';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_processing_recipe_definition_immutable
        BEFORE UPDATE ON processing_recipes
        FOR EACH ROW EXECUTE FUNCTION spectral_prevent_recipe_definition_update()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_processing_recipe_definition_immutable ON processing_recipes")
    op.execute("DROP FUNCTION IF EXISTS spectral_prevent_recipe_definition_update()")
    op.execute("DROP TRIGGER IF EXISTS trg_processing_item_snapshot_immutable ON processing_items")
    op.execute("DROP FUNCTION IF EXISTS spectral_prevent_item_snapshot_update()")
    op.execute("DROP TRIGGER IF EXISTS trg_processing_job_snapshot_immutable ON processing_jobs")
    op.execute("DROP FUNCTION IF EXISTS spectral_prevent_job_snapshot_update()")
    with op.batch_alter_table("index_results") as batch:
        batch.drop_column("software_version")
        batch.drop_column("formula_version")
    with op.batch_alter_table("processing_items") as batch:
        for name in (
            "roi_snapshot",
            "band_mapping_snapshot",
            "input_snapshot",
            "formula_expression",
            "formula_version",
        ):
            batch.drop_column(name)
    with op.batch_alter_table("processing_jobs") as batch:
        batch.drop_index("ix_processing_jobs_recipe_id")
        batch.drop_index("ix_processing_jobs_parent_job_id")
        batch.drop_constraint("fk_processing_jobs_recipe_id", type_="foreignkey")
        batch.drop_constraint("fk_processing_jobs_parent_job_id", type_="foreignkey")
        for name in (
            "software_version",
            "snapshot_checksum",
            "snapshot_schema_version",
            "reproducibility_snapshot",
            "recipe_id",
            "parent_job_id",
        ):
            batch.drop_column(name)
    op.drop_table("processing_recipes")
