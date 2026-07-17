"""C11 固定/混合工作流：workflows 注册表与 workflow_versions 版本快照表。

合入 main 时已重链——B04 先合入占 0031(video)/0032(crc64)，
本 workflows 迁移重编为 0033（接 0032），保持线性单头。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260716_0033"
down_revision: str | None = "20260716_0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workflows",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.String(length=2000), nullable=False),
        sa.Column("latest_version", sa.Integer(), nullable=False),
        sa.Column("published_version", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_workflows_tenant_id", "workflows", ["tenant_id"])
    op.create_index(
        "uq_workflows_tenant_name_lower",
        "workflows",
        ["tenant_id", sa.text("lower(name)")],
        unique=True,
    )

    op.create_table(
        "workflow_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "workflow_id",
            sa.Uuid(),
            sa.ForeignKey("workflows.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("description", sa.String(length=2000), nullable=False),
        sa.Column("graph", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_workflow_versions_workflow_id", "workflow_versions", ["workflow_id"])
    op.create_index("ix_workflow_versions_tenant_id", "workflow_versions", ["tenant_id"])
    op.create_index(
        "uq_workflow_versions_number",
        "workflow_versions",
        ["workflow_id", "version"],
        unique=True,
    )

    # 流程/混合数字员工引用的工作流（应用层校验注册与发布状态，不加 DB 级 FK 以兼容 SQLite）。
    op.add_column("employees", sa.Column("workflow_id", sa.Uuid(), nullable=True))


def downgrade() -> None:
    op.drop_column("employees", "workflow_id")
    op.drop_index("uq_workflow_versions_number", table_name="workflow_versions")
    op.drop_index("ix_workflow_versions_tenant_id", table_name="workflow_versions")
    op.drop_index("ix_workflow_versions_workflow_id", table_name="workflow_versions")
    op.drop_table("workflow_versions")
    op.drop_index("uq_workflows_tenant_name_lower", table_name="workflows")
    op.drop_index("ix_workflows_tenant_id", table_name="workflows")
    op.drop_table("workflows")
