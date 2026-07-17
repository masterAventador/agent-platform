"""C12 定时与预约任务：scheduled_tasks 与 scheduled_task_executions。

(scheduled_task_id, scheduled_for) 唯一索引是「同一触发点只产生一个 Run」的
最终防线：行锁失效或多副本同时认领时，第二次插入必然被数据库拒绝。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260716_0035"
down_revision: str | None = "20260716_0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "scheduled_tasks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "employee_id",
            sa.Uuid(),
            sa.ForeignKey("employees.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("schedule_kind", sa.String(length=16), nullable=False),
        sa.Column("cron_expression", sa.String(length=200), nullable=True),
        sa.Column("run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("input_data", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("pause_reason", sa.String(length=64), nullable=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("misfire_policy", sa.String(length=16), nullable=False),
        sa.Column("concurrency_policy", sa.String(length=16), nullable=False),
        sa.Column("misfire_grace_seconds", sa.Integer(), nullable=False),
        sa.Column("misfire_backfill_window_seconds", sa.Integer(), nullable=False),
        sa.Column("max_retries", sa.Integer(), nullable=False),
        sa.Column("retry_backoff_seconds", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_scheduled_tasks_tenant_id", "scheduled_tasks", ["tenant_id"])
    op.create_index("ix_scheduled_tasks_employee_id", "scheduled_tasks", ["employee_id"])
    op.create_index(
        "ix_scheduled_tasks_enabled_next_run_at",
        "scheduled_tasks",
        ["enabled", "next_run_at"],
    )

    op.create_table(
        "scheduled_task_executions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "scheduled_task_id",
            sa.Uuid(),
            sa.ForeignKey("scheduled_tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "run_id",
            sa.Uuid(),
            sa.ForeignKey("runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("skip_reason", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.String(length=2000), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "uq_scheduled_task_executions_task_scheduled_for",
        "scheduled_task_executions",
        ["scheduled_task_id", "scheduled_for"],
        unique=True,
    )
    op.create_index(
        "ix_scheduled_task_executions_tenant_id",
        "scheduled_task_executions",
        ["tenant_id"],
    )
    op.create_index(
        "ix_scheduled_task_executions_status_updated_at",
        "scheduled_task_executions",
        ["status", "updated_at"],
    )
    op.create_index(
        "ix_scheduled_task_executions_status_next_attempt_at",
        "scheduled_task_executions",
        ["status", "next_attempt_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_scheduled_task_executions_status_next_attempt_at",
        table_name="scheduled_task_executions",
    )
    op.drop_index(
        "ix_scheduled_task_executions_status_updated_at",
        table_name="scheduled_task_executions",
    )
    op.drop_index(
        "ix_scheduled_task_executions_tenant_id", table_name="scheduled_task_executions"
    )
    op.drop_index(
        "uq_scheduled_task_executions_task_scheduled_for",
        table_name="scheduled_task_executions",
    )
    op.drop_table("scheduled_task_executions")
    op.drop_index("ix_scheduled_tasks_enabled_next_run_at", table_name="scheduled_tasks")
    op.drop_index("ix_scheduled_tasks_employee_id", table_name="scheduled_tasks")
    op.drop_index("ix_scheduled_tasks_tenant_id", table_name="scheduled_tasks")
    op.drop_table("scheduled_tasks")
