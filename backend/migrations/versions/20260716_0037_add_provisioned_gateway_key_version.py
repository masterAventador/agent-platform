"""把「网关侧真实存在的 Key 版本」与「对账进度」解耦，并移除废弃的 processing 状态。

`policy.status` 此前同时承担对账进度与凭据可用性两种语义：前者未完成会被读成后者不可用，
导致任何一次策略变更都在对账窗口内打死并发 Run。新增 `provisioned_key_version` 作为
「网关侧真实存在且可用的 Key 版本」的唯一真相源——它只由 Controller 在真实网关确认后写入，
因此不可能被 Seed 或测试伪造成终态。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260716_0037"
down_revision: str | None = "20260716_0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COMMANDS_TABLE = "model_gateway_provisioning_commands"


def _commands_table() -> sa.Table:
    """整表重建的蓝图：原 status CHECK 是匿名的，无法按名字 drop。

    这里刻意不含 status CHECK——重建后的表只保留蓝图里声明的约束，旧的匿名 CHECK
    因此被丢弃，再由 batch op 建立带名字的新 CHECK。
    """

    return sa.Table(
        _COMMANDS_TABLE,
        sa.MetaData(),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("desired_revision", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error_code", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("desired_revision > 0"),
        sa.CheckConstraint("attempts >= 0"),
        sa.CheckConstraint("action = 'reconcile'"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "desired_revision", "action"),
        # 整表重建会按蓝图重建索引：漏掉它们等于把 0017 建的索引悄悄删掉。
        sa.Index("ix_model_gateway_provisioning_commands_tenant_id", "tenant_id"),
        sa.Index("ix_model_gateway_provisioning_commands_status", "status"),
    )


def upgrade() -> None:
    with op.batch_alter_table("tenant_model_gateway_keys") as batch_op:
        batch_op.add_column(sa.Column("provisioned_key_version", sa.Integer(), nullable=True))
        batch_op.create_check_constraint(
            "ck_tenant_model_gateway_keys_provisioned_version",
            "provisioned_key_version IS NULL OR "
            "(provisioned_key_version > 0 AND provisioned_key_version <= key_version)",
        )
    with op.batch_alter_table(
        _COMMANDS_TABLE,
        copy_from=_commands_table(),
        recreate="always",
    ) as batch_op:
        batch_op.create_check_constraint(
            "ck_model_gateway_provisioning_commands_status",
            "status IN ('pending', 'completed', 'failed')",
        )


def downgrade() -> None:
    with op.batch_alter_table(
        _COMMANDS_TABLE,
        copy_from=_commands_table(),
        recreate="always",
    ) as batch_op:
        batch_op.create_check_constraint(
            "ck_model_gateway_provisioning_commands_status",
            "status IN ('pending', 'processing', 'completed', 'failed')",
        )
    with op.batch_alter_table("tenant_model_gateway_keys") as batch_op:
        batch_op.drop_constraint(
            "ck_tenant_model_gateway_keys_provisioned_version", type_="check"
        )
        batch_op.drop_column("provisioned_key_version")
