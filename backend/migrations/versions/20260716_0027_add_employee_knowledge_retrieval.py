"""员工定义增加知识检索配置列。

编号协调：0024 为 main 已合入的审计事件迁移；0025 由 C14 HMAC 加固占用；
0026 由 C17 占用。本迁移暂以 down_revision=0024 接链，后合入者负责重链。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260716_0027"
down_revision: str | None = "20260716_0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "employees",
        sa.Column(
            "knowledge_retrieval",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("employees", "knowledge_retrieval")
