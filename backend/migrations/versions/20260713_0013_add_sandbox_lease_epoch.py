"""为已部署的沙箱租约增加状态 CAS epoch。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260713_0013"
down_revision: str | None = "20260713_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sandbox_leases",
        sa.Column("epoch", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("sandbox_leases", "epoch")
