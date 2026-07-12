"""创建带 fencing epoch 的 Worker runtime 所有权表。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260713_0011"
down_revision: str | None = "20260713_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "runtime_ownership",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.String(length=200), nullable=True),
        sa.Column("epoch", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("run_id"),
    )
    op.create_index("ix_runtime_ownership_tenant_id", "runtime_ownership", ["tenant_id"])
    op.create_index("ix_runtime_ownership_owner_id", "runtime_ownership", ["owner_id"])
    op.create_index("ix_runtime_ownership_expires_at", "runtime_ownership", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_runtime_ownership_expires_at", table_name="runtime_ownership")
    op.drop_index("ix_runtime_ownership_owner_id", table_name="runtime_ownership")
    op.drop_index("ix_runtime_ownership_tenant_id", table_name="runtime_ownership")
    op.drop_table("runtime_ownership")
