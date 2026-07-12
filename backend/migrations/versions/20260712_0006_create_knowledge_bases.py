"""创建平台知识库映射表。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260712_0006"
down_revision: str | None = "20260712_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "knowledge_bases",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.String(length=4000), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_id", sa.String(length=200), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_id"),
    )
    op.create_index(
        "uq_knowledge_bases_tenant_name",
        "knowledge_bases",
        ["tenant_id", "name"],
        unique=True,
    )
    op.create_index(op.f("ix_knowledge_bases_tenant_id"), "knowledge_bases", ["tenant_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_knowledge_bases_tenant_id"), table_name="knowledge_bases")
    op.drop_index("uq_knowledge_bases_tenant_name", table_name="knowledge_bases")
    op.drop_table("knowledge_bases")
