"""为已部署的沙箱租约增加 sandbox generation。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260713_0015"
down_revision: str | None = "20260713_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sandbox_leases",
        sa.Column("sandbox_epoch", sa.Integer(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    op.drop_column("sandbox_leases", "sandbox_epoch")
