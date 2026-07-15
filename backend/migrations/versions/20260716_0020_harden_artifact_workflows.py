"""强化产物工作流的幂等与有界补偿状态。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260716_0020"
down_revision: str | None = "20260715_0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("runs") as batch_op:
        batch_op.add_column(sa.Column("idempotency_key", sa.Uuid(), nullable=True))
        batch_op.create_unique_constraint(
            "uq_runs_creation_idempotency",
            ["tenant_id", "created_by", "employee_id", "idempotency_key"],
        )
    with op.batch_alter_table("artifact_storage_operations") as batch_op:
        batch_op.add_column(sa.Column("retire_after", sa.DateTime(timezone=True), nullable=True))
        batch_op.drop_constraint("ck_artifact_storage_status", type_="check")
        batch_op.create_check_constraint(
            "ck_artifact_storage_status",
            "status IN ('pending', 'completed', 'compensated', 'retired')",
        )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE artifact_storage_operations SET status = 'completed' WHERE status = 'retired'"
        )
    )
    with op.batch_alter_table("artifact_storage_operations") as batch_op:
        batch_op.drop_constraint("ck_artifact_storage_status", type_="check")
        batch_op.create_check_constraint(
            "ck_artifact_storage_status",
            "status IN ('pending', 'completed', 'compensated')",
        )
        batch_op.drop_column("retire_after")
    with op.batch_alter_table("runs") as batch_op:
        batch_op.drop_constraint("uq_runs_creation_idempotency", type_="unique")
        batch_op.drop_column("idempotency_key")
