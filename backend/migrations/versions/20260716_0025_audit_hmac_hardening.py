"""审计哈希链 HMAC 加固：事件算法标识 + 链头封印。

- ``audit_events.hash_algorithm``：存量事件保留 ``sha256``（无密钥 legacy），
  新事件由仓储写入 ``hmac-sha256.v1``；
- ``audit_chain_states.head_seal`` / ``head_seal_algorithm``：链头 HMAC 封印，
  没有服务端密钥的攻击者无法为伪造链头重算封印；
- 对存量链头做一次性封印回填（TOFU：以迁移时刻的数据库状态为信任起点）。
  回填密钥来自环境变量 ``AGENT_PLATFORM_AUDIT_HMAC_KEY``；非开发环境缺失密钥
  且存在存量链头时 fail-closed，拒绝完成迁移。
"""

import os
from collections.abc import Sequence
from uuid import UUID

import sqlalchemy as sa
from alembic import op

from agent_platform.platform.audit.hashing import (
    LEGACY_SHA256_ALGORITHM,
    AuditHasher,
    resolve_audit_hmac_key,
)

revision: str = "20260716_0025"
down_revision: str | None = "20260716_0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_chain_states = sa.table(
    "audit_chain_states",
    sa.column("tenant_id", sa.Uuid()),
    sa.column("head_sequence", sa.Integer()),
    sa.column("head_hash", sa.String(length=64)),
    sa.column("retained_from_sequence", sa.Integer()),
    sa.column("retention_previous_hash", sa.String(length=64)),
    sa.column("head_seal", sa.String(length=64)),
    sa.column("head_seal_algorithm", sa.String(length=32)),
)


def upgrade() -> None:
    op.add_column(
        "audit_events",
        sa.Column(
            "hash_algorithm",
            sa.String(length=32),
            nullable=False,
            server_default=LEGACY_SHA256_ALGORITHM,
        ),
    )
    op.add_column(
        "audit_chain_states",
        sa.Column("head_seal", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "audit_chain_states",
        sa.Column("head_seal_algorithm", sa.String(length=32), nullable=True),
    )

    connection = op.get_bind()
    states = connection.execute(
        sa.select(
            _chain_states.c.tenant_id,
            _chain_states.c.head_sequence,
            _chain_states.c.head_hash,
            _chain_states.c.retained_from_sequence,
            _chain_states.c.retention_previous_hash,
        )
    ).fetchall()
    if not states:
        return
    hasher = AuditHasher(
        resolve_audit_hmac_key(
            environment=os.getenv("AGENT_PLATFORM_APP_ENVIRONMENT", "development"),
            configured_key=os.getenv("AGENT_PLATFORM_AUDIT_HMAC_KEY", ""),
        )
    )
    for state in states:
        tenant_id = state.tenant_id if isinstance(state.tenant_id, UUID) else UUID(
            str(state.tenant_id)
        )
        seal = hasher.chain_head_seal(
            tenant_id=tenant_id,
            head_sequence=state.head_sequence,
            head_hash=state.head_hash,
            retained_from_sequence=state.retained_from_sequence,
            retention_previous_hash=state.retention_previous_hash,
        )
        connection.execute(
            sa.update(_chain_states)
            .where(_chain_states.c.tenant_id == state.tenant_id)
            .values(head_seal=seal, head_seal_algorithm=hasher.algorithm)
        )


def downgrade() -> None:
    op.drop_column("audit_chain_states", "head_seal_algorithm")
    op.drop_column("audit_chain_states", "head_seal")
    op.drop_column("audit_events", "hash_algorithm")
