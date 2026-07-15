"""创建多轮会话与消息表。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260716_0021"
down_revision: str | None = "20260716_0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _runs_thread_unique_constraint_name() -> str:
    for constraint in sa.inspect(op.get_bind()).get_unique_constraints("runs"):
        if tuple(constraint.get("column_names") or ()) == ("thread_id",):
            name = constraint.get("name")
            if name:
                return name
    return "uq_runs_thread_id"


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("employee_id", sa.Uuid(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("thread_id", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["employee_id"], ["employees.id"]),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_conversations_tenant_id_id"),
        sa.UniqueConstraint("thread_id"),
    )
    op.create_index(op.f("ix_conversations_tenant_id"), "conversations", ["tenant_id"])
    op.create_index(op.f("ix_conversations_employee_id"), "conversations", ["employee_id"])
    op.create_index(op.f("ix_conversations_updated_at"), "conversations", ["updated_at"])
    op.create_index(
        op.f("ix_conversations_last_message_at"),
        "conversations",
        ["last_message_at"],
    )

    thread_constraint_name = _runs_thread_unique_constraint_name()
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table(
            "runs",
            recreate="always",
            naming_convention={"uq": "uq_%(table_name)s_%(column_0_name)s"},
        ) as batch_op:
            batch_op.drop_constraint(thread_constraint_name, type_="unique")
            batch_op.add_column(sa.Column("conversation_id", sa.Uuid(), nullable=True))
            batch_op.create_foreign_key(
                "fk_runs_conversation_id",
                "conversations",
                ["conversation_id"],
                ["id"],
            )
    else:
        op.drop_constraint(thread_constraint_name, "runs", type_="unique")
        op.add_column("runs", sa.Column("conversation_id", sa.Uuid(), nullable=True))
        op.create_foreign_key(
            "fk_runs_conversation_id",
            "runs",
            "conversations",
            ["conversation_id"],
            ["id"],
        )
    op.create_index(op.f("ix_runs_conversation_id"), "runs", ["conversation_id"])

    op.create_table(
        "conversation_messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("content", sa.String(length=12000), nullable=False),
        sa.Column("attachment_ids", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "conversation_id"],
            ["conversations.tenant_id", "conversations.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "conversation_id",
            "sequence",
            name="uq_conversation_messages_sequence",
        ),
    )
    op.create_index(
        op.f("ix_conversation_messages_tenant_id"),
        "conversation_messages",
        ["tenant_id"],
    )
    op.create_index(
        op.f("ix_conversation_messages_conversation_id"),
        "conversation_messages",
        ["conversation_id"],
    )
    op.create_index(
        op.f("ix_conversation_messages_run_id"),
        "conversation_messages",
        ["run_id"],
    )
    op.create_index(
        op.f("ix_conversation_messages_created_at"),
        "conversation_messages",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_conversation_messages_created_at"), table_name="conversation_messages")
    op.drop_index(op.f("ix_conversation_messages_run_id"), table_name="conversation_messages")
    op.drop_index(
        op.f("ix_conversation_messages_conversation_id"),
        table_name="conversation_messages",
    )
    op.drop_index(op.f("ix_conversation_messages_tenant_id"), table_name="conversation_messages")
    op.drop_table("conversation_messages")
    op.drop_index(op.f("ix_runs_conversation_id"), table_name="runs")
    op.execute(
        sa.text(
            "UPDATE runs SET thread_id = CAST(id AS VARCHAR) WHERE conversation_id IS NOT NULL"
        )
    )
    with op.batch_alter_table("runs") as batch_op:
        batch_op.drop_constraint("fk_runs_conversation_id", type_="foreignkey")
        batch_op.drop_column("conversation_id")
        batch_op.create_unique_constraint("uq_runs_thread_id", ["thread_id"])
    op.drop_index(op.f("ix_conversations_last_message_at"), table_name="conversations")
    op.drop_index(op.f("ix_conversations_updated_at"), table_name="conversations")
    op.drop_index(op.f("ix_conversations_employee_id"), table_name="conversations")
    op.drop_index(op.f("ix_conversations_tenant_id"), table_name="conversations")
    op.drop_table("conversations")
