"""创建任务命令耐久死信表。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260713_0012"
down_revision: str | None = "20260713_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "run_dead_letters",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_stream", sa.String(length=200), nullable=False),
        sa.Column("original_delivery_id", sa.String(length=100), nullable=False),
        sa.Column("original_command_id", sa.Uuid(), nullable=True),
        sa.Column("original_run_id", sa.Uuid(), nullable=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(length=32), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("error_type", sa.String(length=64), nullable=False),
        sa.Column("is_malformed", sa.Boolean(), nullable=False),
        sa.Column("raw_fields_summary", sa.JSON(), nullable=False),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("replayed_run_id", sa.Uuid(), nullable=True),
        sa.Column("replayed_command_id", sa.Uuid(), nullable=True),
        sa.Column("replayed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("settled_run_id", sa.Uuid(), nullable=True),
        sa.Column("mirrored_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_stream", "original_delivery_id"),
    )
    op.create_index("ix_run_dead_letters_original_run_id", "run_dead_letters", ["original_run_id"])
    op.create_index("ix_run_dead_letters_tenant_id", "run_dead_letters", ["tenant_id"])
    op.create_index("ix_run_dead_letters_failed_at", "run_dead_letters", ["failed_at"])
    op.create_index("ix_run_dead_letters_mirrored_at", "run_dead_letters", ["mirrored_at"])


def downgrade() -> None:
    op.drop_index("ix_run_dead_letters_mirrored_at", table_name="run_dead_letters")
    op.drop_index("ix_run_dead_letters_failed_at", table_name="run_dead_letters")
    op.drop_index("ix_run_dead_letters_tenant_id", table_name="run_dead_letters")
    op.drop_index("ix_run_dead_letters_original_run_id", table_name="run_dead_letters")
    op.drop_table("run_dead_letters")
