"""创建 provider-neutral 沙盒租约表。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260713_0009"
down_revision: str | None = "20260713_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sandbox_leases",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("thread_id", sa.String(length=200), nullable=False),
        sa.Column("provider", sa.String(length=100), nullable=False),
        sa.Column("sandbox_id", sa.String(length=500), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "user_id",
            "run_id",
            "thread_id",
            "provider",
            name="uq_sandbox_leases_scope_provider",
        ),
        sa.UniqueConstraint(
            "provider", "sandbox_id", name="uq_sandbox_leases_provider_sandbox"
        ),
    )
    op.create_index("ix_sandbox_leases_tenant_id", "sandbox_leases", ["tenant_id"])
    op.create_index("ix_sandbox_leases_user_id", "sandbox_leases", ["user_id"])
    op.create_index("ix_sandbox_leases_run_id", "sandbox_leases", ["run_id"])
    op.create_index("ix_sandbox_leases_status", "sandbox_leases", ["status"])
    op.create_index("ix_sandbox_leases_expires_at", "sandbox_leases", ["expires_at"])
    op.create_index(
        "ix_sandbox_leases_expiry", "sandbox_leases", ["status", "expires_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_sandbox_leases_expiry", table_name="sandbox_leases")
    op.drop_index("ix_sandbox_leases_expires_at", table_name="sandbox_leases")
    op.drop_index("ix_sandbox_leases_status", table_name="sandbox_leases")
    op.drop_index("ix_sandbox_leases_run_id", table_name="sandbox_leases")
    op.drop_index("ix_sandbox_leases_user_id", table_name="sandbox_leases")
    op.drop_index("ix_sandbox_leases_tenant_id", table_name="sandbox_leases")
    op.drop_table("sandbox_leases")
