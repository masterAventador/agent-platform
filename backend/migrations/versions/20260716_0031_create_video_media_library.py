"""创建视频素材库与下载任务表。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260716_0031"
down_revision: str | None = "20260716_0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "video_material_folders",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "parent_id"],
            ["video_material_folders.tenant_id", "video_material_folders.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_video_material_folders_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "parent_id",
            "name",
            name="uq_video_material_folders_sibling_name",
        ),
    )
    op.create_index("ix_video_material_folders_tenant_id", "video_material_folders", ["tenant_id"])
    op.create_table(
        "video_materials",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("folder_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("media_type", sa.String(length=255), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_key", sa.String(length=700), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("tag_names", sa.String(length=2000), nullable=False, server_default="[]"),
        sa.Column("upload_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cleanup_required", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("artifact_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("kind IN ('video', 'image', 'music')", name="ck_video_material_kind"),
        sa.CheckConstraint(
            "status IN ('pending_upload', 'available', 'upload_failed', 'deleted')",
            name="ck_video_material_status",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "folder_id"],
            ["video_material_folders.tenant_id", "video_material_folders.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["artifact_id"], ["artifacts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_video_materials_tenant_id_id"),
        sa.UniqueConstraint("storage_key"),
    )
    op.create_index("ix_video_materials_tenant_id", "video_materials", ["tenant_id"])
    op.create_index("ix_video_materials_status", "video_materials", ["status"])
    op.create_table(
        "video_material_references",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("material_id", sa.Uuid(), nullable=False),
        sa.Column("reference_type", sa.String(length=64), nullable=False),
        sa.Column("reference_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "material_id"],
            ["video_materials.tenant_id", "video_materials.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "material_id",
            "reference_type",
            "reference_id",
            name="uq_video_material_reference_target",
        ),
    )
    op.create_index(
        "ix_video_material_references_tenant_id",
        "video_material_references",
        ["tenant_id"],
    )
    op.create_index(
        "ix_video_material_references_material_id",
        "video_material_references",
        ["material_id"],
    )
    op.create_index(
        "ix_video_material_references_reference_id",
        "video_material_references",
        ["reference_id"],
    )
    op.create_table(
        "video_download_tasks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("requested_by", sa.Uuid(), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("progress", sa.BigInteger(), nullable=False),
        sa.Column("downloaded_bytes", sa.BigInteger(), nullable=False),
        sa.Column("total_bytes", sa.BigInteger(), nullable=False),
        sa.Column("resume_token", sa.String(length=500), nullable=True),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("retryable", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("retry_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("revision", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "source_type IN ('material', 'artifact')",
            name="ck_video_download_task_source_type",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_video_download_task_status",
        ),
        sa.CheckConstraint("revision >= 0", name="ck_video_download_task_revision"),
        sa.ForeignKeyConstraint(["requested_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_video_download_tasks_tenant_id_id"),
    )
    op.create_index("ix_video_download_tasks_tenant_id", "video_download_tasks", ["tenant_id"])
    op.create_index("ix_video_download_tasks_source_id", "video_download_tasks", ["source_id"])
    op.create_index("ix_video_download_tasks_status", "video_download_tasks", ["status"])


def downgrade() -> None:
    op.drop_index("ix_video_download_tasks_status", table_name="video_download_tasks")
    op.drop_index("ix_video_download_tasks_source_id", table_name="video_download_tasks")
    op.drop_index("ix_video_download_tasks_tenant_id", table_name="video_download_tasks")
    op.drop_table("video_download_tasks")
    op.drop_index(
        "ix_video_material_references_reference_id",
        table_name="video_material_references",
    )
    op.drop_index(
        "ix_video_material_references_material_id",
        table_name="video_material_references",
    )
    op.drop_index("ix_video_material_references_tenant_id", table_name="video_material_references")
    op.drop_table("video_material_references")
    op.drop_index("ix_video_materials_status", table_name="video_materials")
    op.drop_index("ix_video_materials_tenant_id", table_name="video_materials")
    op.drop_table("video_materials")
    op.drop_index("ix_video_material_folders_tenant_id", table_name="video_material_folders")
    op.drop_table("video_material_folders")
