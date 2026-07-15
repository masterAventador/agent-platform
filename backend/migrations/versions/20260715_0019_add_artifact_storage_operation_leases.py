"""为产物存储操作增加阶段、租约与协调时间。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260715_0019"
down_revision: str | None = "20260715_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("artifact_storage_operations") as batch_op:
        batch_op.add_column(sa.Column("phase", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("lease_owner", sa.Uuid(), nullable=True))
        batch_op.add_column(
            sa.Column("reconcile_after", sa.DateTime(timezone=True), nullable=True)
        )

    op.execute(
        sa.text(
            """
            UPDATE artifact_storage_operations
            SET phase = CASE
                WHEN status != 'pending' THEN 'storage_applied'
                WHEN action = 'delete' THEN 'metadata_applied'
                ELSE 'intent'
            END,
            reconcile_after = updated_at
            """
        )
    )

    with op.batch_alter_table("artifact_storage_operations") as batch_op:
        batch_op.alter_column(
            "phase",
            existing_type=sa.String(length=32),
            nullable=False,
        )
        batch_op.alter_column(
            "reconcile_after",
            existing_type=sa.DateTime(timezone=True),
            nullable=False,
        )
        batch_op.create_check_constraint(
            "ck_artifact_storage_phase",
            "phase IN ('intent', 'metadata_applied', 'storage_applied')",
        )

    op.create_index(
        "ix_artifact_storage_operations_reconcile_after",
        "artifact_storage_operations",
        ["reconcile_after"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_artifact_storage_operations_reconcile_after",
        table_name="artifact_storage_operations",
    )
    with op.batch_alter_table("artifact_storage_operations") as batch_op:
        batch_op.drop_constraint("ck_artifact_storage_phase", type_="check")
        batch_op.drop_column("reconcile_after")
        batch_op.drop_column("lease_owner")
        batch_op.drop_column("phase")
