"""创建文件、任务附件与产物元数据。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260715_0018"
down_revision: str | None = "20260714_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("runs") as batch_op:
        batch_op.create_unique_constraint("uq_runs_tenant_id_id", ["tenant_id", "id"])
    op.create_table(
        "files",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("media_type", sa.String(length=255), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_key", sa.String(length=500), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_files_tenant_id_id"),
        sa.UniqueConstraint("storage_key"),
    )
    op.create_index("ix_files_tenant_id", "files", ["tenant_id"])
    op.create_table(
        "task_attachments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("file_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_path", sa.String(length=500), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "file_id"], ["files.tenant_id", "files.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "run_id"], ["runs.tenant_id", "runs.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "run_id", "workspace_path", name="uq_task_attachment_workspace_path"
        ),
    )
    op.create_index("ix_task_attachments_tenant_id", "task_attachments", ["tenant_id"])
    op.create_index("ix_task_attachments_run_id", "task_attachments", ["run_id"])
    op.create_index("ix_task_attachments_file_id", "task_attachments", ["file_id"])
    op.create_table(
        "artifacts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("media_type", sa.String(length=255), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_key", sa.String(length=500), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "run_id"], ["runs.tenant_id", "runs.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("storage_key"),
    )
    op.create_index("ix_artifacts_tenant_id", "artifacts", ["tenant_id"])
    op.create_index("ix_artifacts_run_id", "artifacts", ["run_id"])
    op.create_table(
        "artifact_storage_operations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("entity_kind", sa.String(length=16), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("storage_key", sa.String(length=500), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("action IN ('put', 'delete')", name="ck_artifact_storage_action"),
        sa.CheckConstraint(
            "entity_kind IN ('file', 'artifact')", name="ck_artifact_storage_entity_kind"
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'completed', 'compensated')",
            name="ck_artifact_storage_status",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_artifact_storage_operations_tenant_id",
        "artifact_storage_operations",
        ["tenant_id"],
    )
    op.create_index(
        "ix_artifact_storage_operations_entity_id",
        "artifact_storage_operations",
        ["entity_id"],
    )
    op.create_index(
        "ix_artifact_storage_operations_status",
        "artifact_storage_operations",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_artifact_storage_operations_status",
        table_name="artifact_storage_operations",
    )
    op.drop_index(
        "ix_artifact_storage_operations_entity_id",
        table_name="artifact_storage_operations",
    )
    op.drop_index(
        "ix_artifact_storage_operations_tenant_id",
        table_name="artifact_storage_operations",
    )
    op.drop_table("artifact_storage_operations")
    op.drop_index("ix_artifacts_run_id", table_name="artifacts")
    op.drop_index("ix_artifacts_tenant_id", table_name="artifacts")
    op.drop_table("artifacts")
    op.drop_index("ix_task_attachments_file_id", table_name="task_attachments")
    op.drop_index("ix_task_attachments_run_id", table_name="task_attachments")
    op.drop_index("ix_task_attachments_tenant_id", table_name="task_attachments")
    op.drop_table("task_attachments")
    op.drop_index("ix_files_tenant_id", table_name="files")
    op.drop_table("files")
    with op.batch_alter_table("runs") as batch_op:
        batch_op.drop_constraint("uq_runs_tenant_id_id", type_="unique")
