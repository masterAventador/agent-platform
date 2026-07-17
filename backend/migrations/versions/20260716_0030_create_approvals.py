"""C13 独立审批中心：approvals 审批记录表。

down_revision 暂指 20260716_0028：0030/0031 由并行分支占用（C13/C10），
主代理合并时统一重链（见 core-capability-roadmap C13 开工说明）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260716_0030"
down_revision: str | None = "20260716_0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "approvals",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("approval_type", sa.String(length=64), nullable=False),
        sa.Column("risk_level", sa.String(length=32), nullable=False),
        sa.Column("requested_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("request_key", sa.String(length=200), nullable=True),
        sa.Column("context", sa.JSON(), nullable=False),
        sa.Column("required_role", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "run_id",
            sa.Uuid(),
            sa.ForeignKey("runs.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("invocation_id", sa.Uuid(), nullable=True),
        sa.Column("employee_id", sa.Uuid(), sa.ForeignKey("employees.id"), nullable=True),
        sa.Column("assignee_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("decision_reason", sa.String(length=2000), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_key", sa.Uuid(), nullable=True),
        sa.Column("transferred_from_id", sa.Uuid(), nullable=True),
        sa.Column("transferred_to_id", sa.Uuid(), nullable=True),
    )
    op.create_index(
        "uq_approvals_tenant_request_key",
        "approvals",
        ["tenant_id", "request_key"],
        unique=True,
    )
    op.create_index("ix_approvals_tenant_id", "approvals", ["tenant_id"])
    op.create_index("ix_approvals_status", "approvals", ["status"])
    op.create_index("ix_approvals_run_id", "approvals", ["run_id"])
    op.create_index("ix_approvals_assignee_id", "approvals", ["assignee_id"])
    op.create_index("ix_approvals_status_expires_at", "approvals", ["status", "expires_at"])


def downgrade() -> None:
    op.drop_index("ix_approvals_status_expires_at", table_name="approvals")
    op.drop_index("ix_approvals_assignee_id", table_name="approvals")
    op.drop_index("ix_approvals_run_id", table_name="approvals")
    op.drop_index("ix_approvals_status", table_name="approvals")
    op.drop_index("ix_approvals_tenant_id", table_name="approvals")
    op.drop_index("uq_approvals_tenant_request_key", table_name="approvals")
    op.drop_table("approvals")
