"""Persist researcher-configured band sequence profiles.

Revision ID: 20260831_0005
Revises: 20260828_0004
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260831_0005"
down_revision: str | None = "20260828_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

uuid = postgresql.UUID(as_uuid=True)
timestamp = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "band_sequence_profiles",
        sa.Column("id", uuid, primary_key=True, nullable=False),
        sa.Column("owner_id", uuid, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("filename_prefix", sa.String(length=160), nullable=False),
        sa.Column("capture_prefix", sa.String(length=160), nullable=False),
        sa.Column("band_order", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", timestamp, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column(
            "updated_at", timestamp, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.UniqueConstraint("owner_id", "name", name="uq_band_sequence_profile_owner_name"),
    )
    op.create_index("ix_band_sequence_profiles_owner_id", "band_sequence_profiles", ["owner_id"])
    with op.batch_alter_table("datasets") as batch:
        batch.add_column(sa.Column("band_sequence_profile_id", uuid))
        batch.create_foreign_key(
            "fk_datasets_band_sequence_profile_id",
            "band_sequence_profiles",
            ["band_sequence_profile_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_index("ix_datasets_band_sequence_profile_id", ["band_sequence_profile_id"])


def downgrade() -> None:
    with op.batch_alter_table("datasets") as batch:
        batch.drop_index("ix_datasets_band_sequence_profile_id")
        batch.drop_constraint("fk_datasets_band_sequence_profile_id", type_="foreignkey")
        batch.drop_column("band_sequence_profile_id")
    op.drop_index("ix_band_sequence_profiles_owner_id", table_name="band_sequence_profiles")
    op.drop_table("band_sequence_profiles")
