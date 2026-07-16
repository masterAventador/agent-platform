"""C09 Tool/MCP 生命周期：连接状态、版本化、同步报告。

down_revision 暂指 20260716_0024：0025/0026 由并行分支占用，
主代理合并时统一重链（见 core-capability-roadmap C09 开工说明）。
"""

import json
from collections.abc import Sequence
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "20260716_0028"
down_revision: str | None = "20260716_0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("mcp_servers") as batch_op:
        batch_op.add_column(
            sa.Column(
                "connection_status",
                sa.String(length=16),
                nullable=False,
                server_default="unknown",
            )
        )
        batch_op.add_column(
            sa.Column("connection_tested_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("connection_error_code", sa.String(length=64), nullable=True)
        )
        batch_op.add_column(
            sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True)
        )

    with op.batch_alter_table("tools") as batch_op:
        batch_op.add_column(
            sa.Column(
                "origin", sa.String(length=16), nullable=False, server_default="manual"
            )
        )
        batch_op.add_column(
            sa.Column(
                "approval_policy",
                sa.String(length=16),
                nullable=False,
                server_default="risk_based",
            )
        )
        batch_op.add_column(
            sa.Column(
                "upstream_missing",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.add_column(
            sa.Column("version", sa.Integer(), nullable=False, server_default="1")
        )

    op.create_table(
        "tool_versions",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "tool_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("tools.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("description", sa.String(length=2000), nullable=False),
        sa.Column("input_schema", sa.JSON(), nullable=False),
        sa.Column("risk_level", sa.String(length=16), nullable=False),
        sa.Column("approval_policy", sa.String(length=16), nullable=False),
        sa.Column("change_source", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "uq_tool_versions_number", "tool_versions", ["tool_id", "version"], unique=True
    )

    op.create_table(
        "mcp_sync_reports",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "server_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("mcp_servers.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("added", sa.JSON(), nullable=False),
        sa.Column("updated", sa.JSON(), nullable=False),
        sa.Column("removed", sa.JSON(), nullable=False),
        sa.Column("unchanged", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(length=64), nullable=True),
    )

    # 为既有工具回填初始版本快照，保证回滚入口对存量数据可用。
    # PostgreSQL 的 JSON 列读出来是 Python dict，必须以 JSON 类型绑定回写；
    # 裸 text() 参数在 asyncpg/psycopg 上会因 dict 无法适配而失败。
    connection = op.get_bind()
    existing_tools = connection.execute(
        sa.text(
            "SELECT id, tenant_id, description, input_schema, risk_level FROM tools"
        )
    ).fetchall()
    backfill_statement = sa.text(
        "INSERT INTO tool_versions "
        "(id, tenant_id, tool_id, version, description, input_schema, "
        "risk_level, approval_policy, change_source, created_at) "
        "VALUES (:id, :tenant_id, :tool_id, 1, :description, :input_schema, "
        ":risk_level, 'risk_based', 'initial', CURRENT_TIMESTAMP)"
    ).bindparams(sa.bindparam("input_schema", type_=sa.JSON()))
    for row in existing_tools:
        raw_schema = row[3]
        input_schema = json.loads(raw_schema) if isinstance(raw_schema, str) else raw_schema
        connection.execute(
            backfill_statement,
            {
                "id": str(uuid4()) if isinstance(row[0], str) else uuid4(),
                "tenant_id": row[1],
                "tool_id": row[0],
                "description": row[2],
                "input_schema": input_schema,
                "risk_level": row[4],
            },
        )


def downgrade() -> None:
    op.drop_table("mcp_sync_reports")
    op.drop_index("uq_tool_versions_number", table_name="tool_versions")
    op.drop_table("tool_versions")
    with op.batch_alter_table("tools") as batch_op:
        batch_op.drop_column("version")
        batch_op.drop_column("upstream_missing")
        batch_op.drop_column("approval_policy")
        batch_op.drop_column("origin")
    with op.batch_alter_table("mcp_servers") as batch_op:
        batch_op.drop_column("last_synced_at")
        batch_op.drop_column("connection_error_code")
        batch_op.drop_column("connection_tested_at")
        batch_op.drop_column("connection_status")
