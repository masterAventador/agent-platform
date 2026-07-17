"""创建租户网关虚拟 Key 生命周期表，并为 provisioning 命令补有界退避时间。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260716_0036"
down_revision: str | None = "20260716_0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tenant_model_gateway_keys",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        # 只保存版本号：Key 明文与其摘要都由服务端密钥 + tenant_id + key_version 现场派生，
        # 本表不含任何由 Key 派生的材料。
        sa.Column("key_version", sa.Integer(), nullable=False),
        sa.Column("retired_key_version", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("key_version > 0"),
        sa.CheckConstraint(
            "retired_key_version IS NULL OR "
            "(retired_key_version > 0 AND retired_key_version < key_version)"
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("tenant_id"),
    )
    with op.batch_alter_table("model_gateway_provisioning_commands") as batch_op:
        batch_op.add_column(
            sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("model_gateway_provisioning_commands") as batch_op:
        batch_op.drop_column("next_attempt_at")
    op.drop_table("tenant_model_gateway_keys")
