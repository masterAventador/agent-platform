"""创建平台级长期记忆表。

注意：合入 main 时已按「先合入者保留编号」惯例由 0031 改号为 0029（down_revision=0028 单头）；
主代理合并 C10/C13 时统一重链（C13 占用 20260716_0030）。

memories 与 LangGraph Checkpoint 职责分离：Checkpoint 保存任务线程的
运行内执行状态，memories 保存跨任务长期知识（企业/用户/员工/会话四级
命名空间，键为 tenant_id + scope + scope_ref）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260716_0029"
down_revision: str | None = "20260716_0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "memories",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("scope", sa.String(length=32), nullable=False),
        sa.Column("scope_ref", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("content", sa.String(length=4000), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("source_ref", sa.String(length=128), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_memories_namespace_source_key",
        "memories",
        ["tenant_id", "scope", "scope_ref", "source", "key"],
        unique=True,
    )
    op.create_index(op.f("ix_memories_tenant_id"), "memories", ["tenant_id"])
    op.create_index(
        "ix_memories_namespace",
        "memories",
        ["tenant_id", "scope", "scope_ref"],
    )
    op.create_index(op.f("ix_memories_updated_at"), "memories", ["updated_at"])
    op.create_index(op.f("ix_memories_expires_at"), "memories", ["expires_at"])


def downgrade() -> None:
    op.drop_index(op.f("ix_memories_expires_at"), table_name="memories")
    op.drop_index(op.f("ix_memories_updated_at"), table_name="memories")
    op.drop_index("ix_memories_namespace", table_name="memories")
    op.drop_index(op.f("ix_memories_tenant_id"), table_name="memories")
    op.drop_index("uq_memories_namespace_source_key", table_name="memories")
    op.drop_table("memories")
