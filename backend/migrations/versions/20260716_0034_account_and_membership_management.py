"""C15 企业、成员与账号体系：邀请、账号一次性 token、用户资料与会话设备列。

编号协调：本迁移开工时 main 单头为 20260716_0030（C13 审批中心）。down_revision
暂指 20260716_0030；与 C11（同期并行）合并时由主代理统一重链，保持迁移单头。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260716_0034"
down_revision: str | None = "20260716_0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("display_name", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "auth_sessions",
        sa.Column("user_agent", sa.String(length=200), nullable=True),
    )

    op.create_table(
        "tenant_invitations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("token_digest", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("invited_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
    )
    op.create_index(
        "uq_tenant_invitations_token_digest",
        "tenant_invitations",
        ["token_digest"],
        unique=True,
    )
    op.create_index(
        "ix_tenant_invitations_tenant_id",
        "tenant_invitations",
        ["tenant_id"],
    )
    op.create_index(
        "ix_tenant_invitations_tenant_status",
        "tenant_invitations",
        ["tenant_id", "status"],
    )
    op.create_index(
        "ix_tenant_invitations_email",
        "tenant_invitations",
        ["email"],
    )

    op.create_table(
        "account_tokens",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("token_digest", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        # 仅开发/演示受控通道使用：expose_dev_account_tokens 关闭时恒为 NULL，
        # staging/production 由配置校验强制关闭，明文永不落库。
        sa.Column("dev_plaintext", sa.String(length=256), nullable=True),
    )
    op.create_index(
        "uq_account_tokens_token_digest",
        "account_tokens",
        ["token_digest"],
        unique=True,
    )
    op.create_index(
        "ix_account_tokens_user_id",
        "account_tokens",
        ["user_id"],
    )
    op.create_index(
        "ix_account_tokens_user_purpose",
        "account_tokens",
        ["user_id", "purpose"],
    )


def downgrade() -> None:
    op.drop_index("ix_account_tokens_user_purpose", table_name="account_tokens")
    op.drop_index("ix_account_tokens_user_id", table_name="account_tokens")
    op.drop_index("uq_account_tokens_token_digest", table_name="account_tokens")
    op.drop_table("account_tokens")

    op.drop_index("ix_tenant_invitations_email", table_name="tenant_invitations")
    op.drop_index("ix_tenant_invitations_tenant_status", table_name="tenant_invitations")
    op.drop_index("ix_tenant_invitations_tenant_id", table_name="tenant_invitations")
    op.drop_index("uq_tenant_invitations_token_digest", table_name="tenant_invitations")
    op.drop_table("tenant_invitations")

    op.drop_column("auth_sessions", "user_agent")
    op.drop_column("users", "display_name")
