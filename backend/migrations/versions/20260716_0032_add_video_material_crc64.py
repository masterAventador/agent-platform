"""为视频素材增加服务端可信内容指纹 crc64ecma（B04 crc64 抗伪造硬化）。

主代理合并说明：合入 main 时已重链——video 迁移重编为 0031（接 0030 审批），
本 crc64 迁移重编为 0032（接 0031），保持线性单头。

新增列 ``video_materials.crc64ecma``：COS 服务端计算的 ``x-cos-hash-crc64ecma``
（十进制 uint64 串）。既有行以空串回填（这些历史草稿无法再核验、会按元数据不一致
失败关闭，符合 fail-closed）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260716_0032"
down_revision: str | None = "20260716_0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "video_materials",
        sa.Column(
            "crc64ecma",
            sa.String(length=20),
            nullable=False,
            server_default="",
        ),
    )


def downgrade() -> None:
    op.drop_column("video_materials", "crc64ecma")
