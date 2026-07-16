"""审计哈希链的 HMAC 密钥签名能力。

密钥只来自服务端配置（``AGENT_PLATFORM_AUDIT_HMAC_KEY``），绝不落数据库、
绝不写日志。算法标识随事件持久化：存量无密钥链保留 ``sha256``，新事件一律
``hmac-sha256.v1``；标识带版本号，为 C18 的密钥轮换（多密钥版本）预留空间。
"""

from __future__ import annotations

import hashlib
import hmac
import json
from uuid import UUID

LEGACY_SHA256_ALGORITHM = "sha256"
HMAC_SHA256_V1_ALGORITHM = "hmac-sha256.v1"
# 开发/测试专用的公开弱密钥，仅用于本机开箱可用；staging/production 显式拒绝。
INSECURE_DEV_AUDIT_HMAC_KEY = "agent-platform-insecure-dev-audit-hmac-key"

AUDIT_EVENT_HASH_PURPOSE = "audit-event-hash"
_DEV_ENVIRONMENTS = frozenset({"local", "development", "test"})
_CHAIN_HEAD_SEAL_PURPOSE = "audit-chain-head-seal"


class AuditHmacKeyNotConfiguredError(RuntimeError):
    """审计 HMAC 密钥缺失时 fail-closed 的受控错误（消息不含密钥材料）。"""

    def __init__(self) -> None:
        super().__init__(
            "audit HMAC key is not configured; refusing keyless audit hashing"
        )


def resolve_audit_hmac_key(*, environment: str, configured_key: str) -> str:
    """解析审计 HMAC 密钥：非开发环境缺密钥必须 fail-closed。"""

    if configured_key:
        return configured_key
    if environment in _DEV_ENVIRONMENTS:
        return INSECURE_DEV_AUDIT_HMAC_KEY
    raise AuditHmacKeyNotConfiguredError()


class AuditHasher:
    """基于服务端密钥的 HMAC-SHA256 审计哈希器。"""

    __slots__ = ("_key",)

    algorithm = HMAC_SHA256_V1_ALGORITHM

    def __init__(self, key: str) -> None:
        if not key:
            raise AuditHmacKeyNotConfiguredError()
        self._key = key.encode("utf-8")

    def __repr__(self) -> str:
        return f"AuditHasher(algorithm={self.algorithm!r})"

    def event_hash(self, payload: bytes) -> str:
        return hmac.new(self._key, payload, hashlib.sha256).hexdigest()

    def chain_head_seal(
        self,
        *,
        tenant_id: UUID,
        head_sequence: int,
        head_hash: str,
        retained_from_sequence: int,
        retention_previous_hash: str | None,
    ) -> str:
        payload = json.dumps(
            {
                "purpose": _CHAIN_HEAD_SEAL_PURPOSE,
                "algorithm": self.algorithm,
                "tenant_id": str(tenant_id),
                "head_sequence": head_sequence,
                "head_hash": head_hash,
                "retained_from_sequence": retained_from_sequence,
                "retention_previous_hash": retention_previous_hash,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hmac.new(self._key, payload, hashlib.sha256).hexdigest()

    def verify_chain_head_seal(
        self,
        *,
        seal: str | None,
        tenant_id: UUID,
        head_sequence: int,
        head_hash: str,
        retained_from_sequence: int,
        retention_previous_hash: str | None,
    ) -> bool:
        if seal is None:
            return False
        expected = self.chain_head_seal(
            tenant_id=tenant_id,
            head_sequence=head_sequence,
            head_hash=head_hash,
            retained_from_sequence=retained_from_sequence,
            retention_previous_hash=retention_previous_hash,
        )
        return hmac.compare_digest(seal, expected)


_active_audit_hasher: AuditHasher | None = None


def configure_audit_hashing(key: str | None) -> None:
    """在进程启动处装配审计 HMAC 密钥；传 None 表示显式未配置（写入将 fail-closed）。"""

    global _active_audit_hasher
    _active_audit_hasher = AuditHasher(key) if key else None


def active_audit_hasher() -> AuditHasher | None:
    return _active_audit_hasher


def require_audit_hasher() -> AuditHasher:
    if _active_audit_hasher is None:
        raise AuditHmacKeyNotConfiguredError()
    return _active_audit_hasher
