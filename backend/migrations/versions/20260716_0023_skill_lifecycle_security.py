"""补齐 Skill 生命周期与安全审核字段。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260716_0023"
down_revision: str | None = "20260716_0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("skills") as batch_op:
        batch_op.add_column(
            sa.Column(
                "lifecycle_status",
                sa.String(length=32),
                nullable=False,
                server_default="draft",
            )
        )
        batch_op.add_column(
            sa.Column("source", sa.String(length=32), nullable=False, server_default="uploaded")
        )
        batch_op.add_column(sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.execute(
        sa.text(
            "UPDATE skills SET lifecycle_status = "
            "CASE WHEN published_version IS NULL THEN 'draft' ELSE 'published' END"
        )
    )
    with op.batch_alter_table("skill_versions") as batch_op:
        batch_op.add_column(
            sa.Column(
                "review_status",
                sa.String(length=32),
                nullable=False,
                server_default="approved",
            )
        )
        batch_op.add_column(
            sa.Column(
                "security_findings",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            )
        )
        batch_op.add_column(
            sa.Column(
                "reviewed_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("skill_versions") as batch_op:
        batch_op.drop_column("reviewed_at")
        batch_op.drop_column("security_findings")
        batch_op.drop_column("review_status")
    with op.batch_alter_table("skills") as batch_op:
        batch_op.drop_column("deleted_at")
        batch_op.drop_column("archived_at")
        batch_op.drop_column("source")
        batch_op.drop_column("lifecycle_status")
