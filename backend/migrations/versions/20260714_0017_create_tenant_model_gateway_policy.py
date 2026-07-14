"""创建租户模型网关 desired policy 与 provisioning outbox。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260714_0017"
down_revision: str | None = "20260714_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tenant_model_gateway_policies",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("allowed_aliases", sa.JSON(), nullable=False),
        sa.Column("budget_microusd", sa.BigInteger(), nullable=False),
        sa.Column("budget_period", sa.String(length=16), nullable=False),
        sa.Column("rpm_limit", sa.Integer(), nullable=False),
        sa.Column("tpm_limit", sa.Integer(), nullable=False),
        sa.Column("max_parallel_requests", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=False),
        sa.CheckConstraint("budget_microusd > 0"),
        sa.CheckConstraint("budget_period = 'monthly'"),
        sa.CheckConstraint("rpm_limit > 0"),
        sa.CheckConstraint("tpm_limit > 0"),
        sa.CheckConstraint("max_parallel_requests > 0"),
        sa.CheckConstraint("revision > 0"),
        sa.CheckConstraint("status IN ('pending', 'active', 'disabled', 'error')"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("tenant_id"),
    )
    op.create_table(
        "model_gateway_provisioning_commands",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("desired_revision", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error_code", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("desired_revision > 0"),
        sa.CheckConstraint("attempts >= 0"),
        sa.CheckConstraint("action = 'reconcile'"),
        sa.CheckConstraint("status IN ('pending', 'processing', 'completed', 'failed')"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "desired_revision", "action"),
    )
    op.create_index(
        "ix_model_gateway_provisioning_commands_tenant_id",
        "model_gateway_provisioning_commands",
        ["tenant_id"],
    )
    op.create_index(
        "ix_model_gateway_provisioning_commands_status",
        "model_gateway_provisioning_commands",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_model_gateway_provisioning_commands_status",
        table_name="model_gateway_provisioning_commands",
    )
    op.drop_index(
        "ix_model_gateway_provisioning_commands_tenant_id",
        table_name="model_gateway_provisioning_commands",
    )
    op.drop_table("model_gateway_provisioning_commands")
    op.drop_table("tenant_model_gateway_policies")
