"""审计 HMAC 哈希器与进程级密钥配置的单元契约。"""

from __future__ import annotations

import hashlib
import hmac
from uuid import uuid4

import pytest

from agent_platform.platform.audit.hashing import (
    HMAC_SHA256_V1_ALGORITHM,
    INSECURE_DEV_AUDIT_HMAC_KEY,
    AuditHasher,
    AuditHmacKeyNotConfiguredError,
    active_audit_hasher,
    configure_audit_hashing,
    require_audit_hasher,
    resolve_audit_hmac_key,
)


def test_event_hash_is_keyed_hmac_sha256() -> None:
    hasher = AuditHasher("unit-test-audit-hmac-key-0001")
    payload = b'{"action":"auth.registered"}'

    assert hasher.algorithm == HMAC_SHA256_V1_ALGORITHM
    assert hasher.event_hash(payload) == hmac.new(
        b"unit-test-audit-hmac-key-0001", payload, hashlib.sha256
    ).hexdigest()
    assert hasher.event_hash(payload) != hashlib.sha256(payload).hexdigest()
    assert AuditHasher("unit-test-audit-hmac-key-0002").event_hash(payload) != hasher.event_hash(
        payload
    )


def test_empty_key_is_rejected() -> None:
    with pytest.raises(AuditHmacKeyNotConfiguredError):
        AuditHasher("")


def test_chain_head_seal_covers_head_and_retention_fields() -> None:
    hasher = AuditHasher("unit-test-audit-hmac-key-0001")
    tenant_id = uuid4()
    seal = hasher.chain_head_seal(
        tenant_id=tenant_id,
        head_sequence=7,
        head_hash="a" * 64,
        retained_from_sequence=3,
        retention_previous_hash="b" * 64,
    )

    assert hasher.verify_chain_head_seal(
        seal=seal,
        tenant_id=tenant_id,
        head_sequence=7,
        head_hash="a" * 64,
        retained_from_sequence=3,
        retention_previous_hash="b" * 64,
    )
    # 篡改保留字段必须导致封印失效（否则攻击者可移动保留边界隐藏前缀删除）。
    assert not hasher.verify_chain_head_seal(
        seal=seal,
        tenant_id=tenant_id,
        head_sequence=7,
        head_hash="a" * 64,
        retained_from_sequence=4,
        retention_previous_hash="b" * 64,
    )
    assert not hasher.verify_chain_head_seal(
        seal=None,
        tenant_id=tenant_id,
        head_sequence=7,
        head_hash="a" * 64,
        retained_from_sequence=3,
        retention_previous_hash="b" * 64,
    )
    assert not AuditHasher("unit-test-audit-hmac-key-0002").verify_chain_head_seal(
        seal=seal,
        tenant_id=tenant_id,
        head_sequence=7,
        head_hash="a" * 64,
        retained_from_sequence=3,
        retention_previous_hash="b" * 64,
    )


def test_resolve_audit_hmac_key_fails_closed_outside_development() -> None:
    assert (
        resolve_audit_hmac_key(environment="development", configured_key="")
        == INSECURE_DEV_AUDIT_HMAC_KEY
    )
    assert (
        resolve_audit_hmac_key(environment="test", configured_key="")
        == INSECURE_DEV_AUDIT_HMAC_KEY
    )
    assert (
        resolve_audit_hmac_key(environment="production", configured_key="explicit-key")
        == "explicit-key"
    )
    with pytest.raises(AuditHmacKeyNotConfiguredError):
        resolve_audit_hmac_key(environment="production", configured_key="")
    with pytest.raises(AuditHmacKeyNotConfiguredError):
        resolve_audit_hmac_key(environment="staging", configured_key="")


def test_require_audit_hasher_fails_closed_when_unconfigured() -> None:
    configure_audit_hashing(None)
    assert active_audit_hasher() is None
    with pytest.raises(AuditHmacKeyNotConfiguredError):
        require_audit_hasher()

    configure_audit_hashing("unit-test-audit-hmac-key-0001")
    hasher = require_audit_hasher()
    assert hasher.event_hash(b"x") == hmac.new(
        b"unit-test-audit-hmac-key-0001", b"x", hashlib.sha256
    ).hexdigest()


def test_hasher_repr_never_leaks_key_material() -> None:
    hasher = AuditHasher("unit-test-audit-hmac-key-0001")
    assert "unit-test-audit-hmac-key-0001" not in repr(hasher)
    assert "unit-test-audit-hmac-key-0001" not in str(hasher)
