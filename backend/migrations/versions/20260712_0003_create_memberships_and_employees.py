"""创建租户成员、数字员工和发布版本表。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260712_0003"
down_revision: str | None = "20260712_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tenant_memberships",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "user_id",
            name="uq_tenant_memberships_tenant_user",
        ),
    )
    op.create_index(
        op.f("ix_tenant_memberships_tenant_id"),
        "tenant_memberships",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_tenant_memberships_user_id"),
        "tenant_memberships",
        ["user_id"],
        unique=False,
    )

    op.create_table(
        "employees",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("avatar_url", sa.String(length=2048), nullable=True),
        sa.Column("role_description", sa.String(length=2000), nullable=False),
        sa.Column("visibility", sa.String(length=32), nullable=False),
        sa.Column("runtime_type", sa.String(length=32), nullable=False),
        sa.Column("system_prompt", sa.String(length=20000), nullable=False),
        sa.Column("model_settings", sa.JSON(), nullable=False),
        sa.Column("input_schema", sa.JSON(), nullable=False),
        sa.Column("output_schema", sa.JSON(), nullable=False),
        sa.Column("capabilities", sa.JSON(), nullable=False),
        sa.Column("skill_ids", sa.JSON(), nullable=False),
        sa.Column("tool_ids", sa.JSON(), nullable=False),
        sa.Column("knowledge_base_ids", sa.JSON(), nullable=False),
        sa.Column("approval_policy", sa.JSON(), nullable=False),
        sa.Column("release_strategy", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("published_version", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_employees_tenant_id"), "employees", ["tenant_id"], unique=False)
    op.create_index(
        "uq_employees_tenant_name_lower",
        "employees",
        ["tenant_id", sa.text("lower(name)")],
        unique=True,
    )

    op.create_table(
        "employee_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("employee_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("definition", sa.JSON(), nullable=False),
        sa.Column("published_by", sa.Uuid(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["employee_id"], ["employees.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["published_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_employee_versions_employee_id"),
        "employee_versions",
        ["employee_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_employee_versions_tenant_id"),
        "employee_versions",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        "uq_employee_versions_number",
        "employee_versions",
        ["employee_id", "version"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_employee_versions_number", table_name="employee_versions")
    op.drop_index(op.f("ix_employee_versions_tenant_id"), table_name="employee_versions")
    op.drop_index(op.f("ix_employee_versions_employee_id"), table_name="employee_versions")
    op.drop_table("employee_versions")
    op.drop_index("uq_employees_tenant_name_lower", table_name="employees")
    op.drop_index(op.f("ix_employees_tenant_id"), table_name="employees")
    op.drop_table("employees")
    op.drop_index(op.f("ix_tenant_memberships_user_id"), table_name="tenant_memberships")
    op.drop_index(op.f("ix_tenant_memberships_tenant_id"), table_name="tenant_memberships")
    op.drop_table("tenant_memberships")
