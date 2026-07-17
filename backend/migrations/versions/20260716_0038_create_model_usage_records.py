"""创建模型用量记录表（C16 阶段二，纯观测面）。

记录每次物理模型调用的 alias / token / 延迟 / 结果 / 错误分类 / 费用 / 任务归属。
tenant_id 是隔离边界（FK CASCADE）；run/employee 是观测归属标签，刻意不加 FK，
让计费历史比 run/employee 生命周期更长。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260716_0038"
down_revision: str | None = "20260716_0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "model_usage_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("employee_id", sa.Uuid(), nullable=True),
        sa.Column("model_alias", sa.String(length=128), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("error_type", sa.String(length=128), nullable=True),
        sa.Column("cost_nanousd", sa.BigInteger(), nullable=True),
        sa.Column("cost_source", sa.String(length=32), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "latency_ms >= 0", name="ck_model_usage_records_latency_non_negative"
        ),
        sa.CheckConstraint(
            "cost_nanousd IS NULL OR cost_nanousd >= 0",
            name="ck_model_usage_records_cost_non_negative",
        ),
        sa.CheckConstraint(
            "prompt_tokens IS NULL OR prompt_tokens >= 0",
            name="ck_model_usage_records_prompt_tokens_non_negative",
        ),
        sa.CheckConstraint(
            "completion_tokens IS NULL OR completion_tokens >= 0",
            name="ck_model_usage_records_completion_tokens_non_negative",
        ),
        sa.CheckConstraint(
            "total_tokens IS NULL OR total_tokens >= 0",
            name="ck_model_usage_records_total_tokens_non_negative",
        ),
        sa.CheckConstraint(
            "outcome IN ('success', 'error')",
            name="ck_model_usage_records_outcome",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_model_usage_records_tenant_recorded",
        "model_usage_records",
        ["tenant_id", "recorded_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_model_usage_records_tenant_recorded", table_name="model_usage_records"
    )
    op.drop_table("model_usage_records")
