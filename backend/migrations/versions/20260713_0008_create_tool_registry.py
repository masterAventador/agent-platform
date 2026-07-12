"""创建 MCP Server 与 Tool 注册表。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260713_0008"
down_revision: str | None = "20260712_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mcp_servers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("transport", sa.String(length=32), nullable=False),
        sa.Column("endpoint", sa.String(length=2048), nullable=True),
        sa.Column("command", sa.String(length=500), nullable=True),
        sa.Column("args", sa.JSON(), nullable=False),
        sa.Column("secret_reference", sa.String(length=1000), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_mcp_servers_tenant_id", "mcp_servers", ["tenant_id"])
    op.create_index(
        "uq_mcp_servers_tenant_name", "mcp_servers", ["tenant_id", "name"], unique=True
    )
    op.create_table(
        "tools",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("server_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.String(length=2000), nullable=False),
        sa.Column("input_schema", sa.JSON(), nullable=False),
        sa.Column("risk_level", sa.String(length=16), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["server_id"], ["mcp_servers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tools_tenant_id", "tools", ["tenant_id"])
    op.create_index("ix_tools_server_id", "tools", ["server_id"])
    op.create_index("uq_tools_server_name", "tools", ["server_id", "name"], unique=True)


def downgrade() -> None:
    op.drop_index("uq_tools_server_name", table_name="tools")
    op.drop_index("ix_tools_server_id", table_name="tools")
    op.drop_index("ix_tools_tenant_id", table_name="tools")
    op.drop_table("tools")
    op.drop_index("uq_mcp_servers_tenant_name", table_name="mcp_servers")
    op.drop_index("ix_mcp_servers_tenant_id", table_name="mcp_servers")
    op.drop_table("mcp_servers")
