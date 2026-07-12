"""创建脱敏 Tool 审计事件表。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260713_0010"
down_revision: str | None = "20260713_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tool_audit_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("employee_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("tool_id", sa.Uuid(), nullable=False),
        sa.Column("tool_name", sa.String(length=128), nullable=False),
        sa.Column("risk", sa.String(length=16), nullable=True),
        sa.Column("argument_keys", sa.JSON(), nullable=False),
        sa.Column("argument_sha256", sa.String(length=64), nullable=False),
        sa.Column("argument_size_bytes", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=200), nullable=True),
        sa.Column("succeeded", sa.Boolean(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tool_audit_events_tenant_id", "tool_audit_events", ["tenant_id"])
    op.create_index("ix_tool_audit_events_run_id", "tool_audit_events", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_tool_audit_events_run_id", table_name="tool_audit_events")
    op.drop_index("ix_tool_audit_events_tenant_id", table_name="tool_audit_events")
    op.drop_table("tool_audit_events")
