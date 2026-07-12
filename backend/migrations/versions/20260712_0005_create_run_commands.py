"""创建可靠任务命令 Outbox。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260712_0005"
down_revision: str | None = "20260712_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "run_commands",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.String(length=2000), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_run_commands_run_id"), "run_commands", ["run_id"])
    op.create_index(op.f("ix_run_commands_tenant_id"), "run_commands", ["tenant_id"])
    op.create_index(op.f("ix_run_commands_created_at"), "run_commands", ["created_at"])


def downgrade() -> None:
    op.drop_index(op.f("ix_run_commands_created_at"), table_name="run_commands")
    op.drop_index(op.f("ix_run_commands_tenant_id"), table_name="run_commands")
    op.drop_index(op.f("ix_run_commands_run_id"), table_name="run_commands")
    op.drop_table("run_commands")
