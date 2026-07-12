"""为工具审计增加确定性 invocation_id。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260713_0014"
down_revision: str | None = "20260713_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tool_audit_events",
        sa.Column("invocation_id", sa.Uuid(), nullable=True),
    )
    op.create_index(
        "ix_tool_audit_events_invocation_id",
        "tool_audit_events",
        ["invocation_id"],
        postgresql_where=sa.text("invocation_id IS NOT NULL"),
        sqlite_where=sa.text("invocation_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_tool_audit_events_invocation_id",
        table_name="tool_audit_events",
    )
    op.drop_column("tool_audit_events", "invocation_id")
