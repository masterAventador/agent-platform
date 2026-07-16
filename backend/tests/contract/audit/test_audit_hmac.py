"""审计哈希链 HMAC 密钥签名契约。

覆盖：新事件必须为 HMAC-SHA256、错误密钥校验失败、无密钥全量重写攻击被检出、
legacy+HMAC 混合链正确校验、HMAC 后禁止降级回 legacy、密钥缺失 fail-closed、
保留清扫后链头封印保持有效、保留边界篡改被检出。
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from pydantic import JsonValue
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from agent_platform.api.app import create_app
from agent_platform.config import AppSettings
from agent_platform.infrastructure.database.base import Base
from agent_platform.infrastructure.database.models import load_database_models
from agent_platform.infrastructure.database.repositories.audit import (
    AuditChainStateRecord,
    AuditEventCreate,
    AuditEventRecord,
    SqlAlchemyAuditEventRepository,
)
from agent_platform.platform.audit.hashing import (
    HMAC_SHA256_V1_ALGORITHM,
    LEGACY_SHA256_ALGORITHM,
    AuditHasher,
    AuditHmacKeyNotConfiguredError,
    active_audit_hasher,
    configure_audit_hashing,
)

KEY_A = "contract-audit-hmac-key-alpha-0001"
KEY_B = "contract-audit-hmac-key-bravo-0002"


async def _make_engine() -> AsyncEngine:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    load_database_models()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine


def _canonical_payload(
    *,
    event_id: UUID,
    tenant_id: UUID,
    actor_user_id: UUID | None,
    sequence: int,
    action: str,
    resource_type: str,
    resource_id: UUID | None,
    outcome: str,
    occurred_at: datetime,
    correlation_id: str | None,
    previous_hash: str | None,
    metadata: Mapping[str, JsonValue],
    hash_algorithm: str | None,
) -> bytes:
    payload: dict[str, JsonValue] = {
        "id": str(event_id),
        "tenant_id": str(tenant_id),
        "actor_user_id": str(actor_user_id) if actor_user_id is not None else None,
        "sequence": sequence,
        "action": action,
        "resource_type": resource_type,
        "resource_id": str(resource_id) if resource_id is not None else None,
        "outcome": outcome,
        "occurred_at": occurred_at.astimezone(UTC).isoformat(),
        "correlation_id": correlation_id,
        "previous_hash": previous_hash,
        "metadata": dict(metadata),
    }
    if hash_algorithm is not None:
        payload["hash_algorithm"] = hash_algorithm
        payload["purpose"] = "audit-event-hash"
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _legacy_hash(**fields: object) -> str:
    payload = _canonical_payload(hash_algorithm=None, **fields)  # type: ignore[arg-type]
    return hashlib.sha256(payload).hexdigest()


def _hmac_hash(key: str, **fields: object) -> str:
    payload = _canonical_payload(
        hash_algorithm=HMAC_SHA256_V1_ALGORITHM,
        **fields,  # type: ignore[arg-type]
    )
    return hmac.new(key.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def _legacy_record(
    *,
    tenant_id: UUID,
    sequence: int,
    previous_hash: str | None,
    occurred_at: datetime,
    action: str = "legacy.event",
) -> AuditEventRecord:
    event_id = uuid4()
    event_hash = _legacy_hash(
        event_id=event_id,
        tenant_id=tenant_id,
        actor_user_id=None,
        sequence=sequence,
        action=action,
        resource_type="test",
        resource_id=None,
        outcome="succeeded",
        occurred_at=occurred_at,
        correlation_id=None,
        previous_hash=previous_hash,
        metadata={},
    )
    return AuditEventRecord(
        id=event_id,
        tenant_id=tenant_id,
        actor_user_id=None,
        sequence=sequence,
        action=action,
        resource_type="test",
        resource_id=None,
        outcome="succeeded",
        occurred_at=occurred_at,
        correlation_id=None,
        previous_hash=previous_hash,
        event_hash=event_hash,
        hash_algorithm=LEGACY_SHA256_ALGORITHM,
        metadata_json={},
    )


def _sealed_state(
    *,
    hasher: AuditHasher,
    tenant_id: UUID,
    head_sequence: int,
    head_hash: str,
    retained_from_sequence: int = 1,
    retention_previous_hash: str | None = None,
) -> AuditChainStateRecord:
    return AuditChainStateRecord(
        tenant_id=tenant_id,
        head_sequence=head_sequence,
        head_hash=head_hash,
        retained_from_sequence=retained_from_sequence,
        retention_previous_hash=retention_previous_hash,
        head_seal=hasher.chain_head_seal(
            tenant_id=tenant_id,
            head_sequence=head_sequence,
            head_hash=head_hash,
            retained_from_sequence=retained_from_sequence,
            retention_previous_hash=retention_previous_hash,
        ),
        head_seal_algorithm=hasher.algorithm,
        updated_at=datetime.now(UTC),
    )


async def _add_events(
    sessions: async_sessionmaker[AsyncSession],
    tenant_id: UUID,
    count: int,
    *,
    action_prefix: str = "hmac.event",
) -> None:
    async with sessions() as session:
        repository = SqlAlchemyAuditEventRepository(session)
        for index in range(count):
            await repository.add(
                AuditEventCreate(
                    tenant_id=tenant_id,
                    actor_user_id=None,
                    action=f"{action_prefix}_{index}",
                    resource_type="test",
                )
            )
        await session.commit()


@pytest.mark.asyncio
async def test_new_events_are_hmac_signed_with_configured_key() -> None:
    configure_audit_hashing(KEY_A)
    engine = await _make_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    tenant_id = uuid4()
    await _add_events(sessions, tenant_id, 2)

    async with sessions() as session:
        records = list(
            (
                await session.execute(
                    select(AuditEventRecord)
                    .where(AuditEventRecord.tenant_id == tenant_id)
                    .order_by(AuditEventRecord.sequence)
                )
            )
            .scalars()
            .all()
        )
        state = await session.get(AuditChainStateRecord, tenant_id)
        assert state is not None
        for record in records:
            assert record.hash_algorithm == HMAC_SHA256_V1_ALGORITHM
            expected = _hmac_hash(
                KEY_A,
                event_id=record.id,
                tenant_id=record.tenant_id,
                actor_user_id=record.actor_user_id,
                sequence=record.sequence,
                action=record.action,
                resource_type=record.resource_type,
                resource_id=record.resource_id,
                outcome=record.outcome,
                occurred_at=record.occurred_at.replace(tzinfo=UTC),
                correlation_id=record.correlation_id,
                previous_hash=record.previous_hash,
                metadata=record.metadata_json,
            )
            assert record.event_hash == expected
        assert AuditHasher(KEY_A).verify_chain_head_seal(
            seal=state.head_seal,
            tenant_id=tenant_id,
            head_sequence=state.head_sequence,
            head_hash=state.head_hash,
            retained_from_sequence=state.retained_from_sequence,
            retention_previous_hash=state.retention_previous_hash,
        )
        verification = await SqlAlchemyAuditEventRepository(session).verify_integrity(
            tenant_id=tenant_id
        )
        assert verification.valid
        assert verification.checked_events == 2
    await engine.dispose()


@pytest.mark.asyncio
async def test_verify_integrity_fails_with_wrong_key() -> None:
    configure_audit_hashing(KEY_A)
    engine = await _make_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    tenant_id = uuid4()
    await _add_events(sessions, tenant_id, 3)

    async with sessions() as session:
        verification = await SqlAlchemyAuditEventRepository(
            session, hasher=AuditHasher(KEY_B)
        ).verify_integrity(tenant_id=tenant_id)
    assert not verification.valid
    await engine.dispose()


@pytest.mark.asyncio
async def test_full_database_rewrite_with_keyless_hashes_is_detected() -> None:
    """能全量重写数据库（含链头）的攻击者用无密钥 SHA-256 伪造自洽链，必须被检出。"""

    configure_audit_hashing(KEY_A)
    engine = await _make_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    tenant_id = uuid4()
    await _add_events(sessions, tenant_id, 3)

    async with sessions() as session:
        records = list(
            (
                await session.execute(
                    select(AuditEventRecord)
                    .where(AuditEventRecord.tenant_id == tenant_id)
                    .order_by(AuditEventRecord.sequence)
                )
            )
            .scalars()
            .all()
        )
        # 攻击者重写第 2 条事件并用无密钥哈希重算整条链与链头，链自身完全自洽。
        records[1].action = "attacker.hidden"
        previous_hash: str | None = records[0].previous_hash
        for record in records:
            record.previous_hash = previous_hash
            record.event_hash = _legacy_hash(
                event_id=record.id,
                tenant_id=record.tenant_id,
                actor_user_id=record.actor_user_id,
                sequence=record.sequence,
                action=record.action,
                resource_type=record.resource_type,
                resource_id=record.resource_id,
                outcome=record.outcome,
                occurred_at=record.occurred_at.replace(tzinfo=UTC),
                correlation_id=record.correlation_id,
                previous_hash=record.previous_hash,
                metadata=record.metadata_json,
            )
            record.hash_algorithm = LEGACY_SHA256_ALGORITHM
            previous_hash = record.event_hash
        state = await session.get(AuditChainStateRecord, tenant_id)
        assert state is not None
        state.head_hash = records[-1].event_hash
        state.head_sequence = records[-1].sequence
        # 攻击者没有服务端密钥，最多只能抹掉或伪造封印。
        state.head_seal = None
        state.head_seal_algorithm = None
        await session.commit()

    async with sessions() as session:
        verification = await SqlAlchemyAuditEventRepository(session).verify_integrity(
            tenant_id=tenant_id
        )
    assert not verification.valid
    await engine.dispose()


@pytest.mark.asyncio
async def test_mixed_legacy_prefix_then_hmac_chain_verifies() -> None:
    """存量无密钥链（迁移封印后）+ 新 HMAC 事件的混合链必须校验通过。"""

    configure_audit_hashing(KEY_A)
    engine = await _make_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    tenant_id = uuid4()
    occurred_at = datetime.now(UTC)
    first = _legacy_record(
        tenant_id=tenant_id,
        sequence=1,
        previous_hash=None,
        occurred_at=occurred_at,
    )
    second = _legacy_record(
        tenant_id=tenant_id,
        sequence=2,
        previous_hash=first.event_hash,
        occurred_at=occurred_at,
    )
    async with sessions() as session:
        session.add_all(
            [
                first,
                second,
                _sealed_state(
                    hasher=AuditHasher(KEY_A),
                    tenant_id=tenant_id,
                    head_sequence=2,
                    head_hash=second.event_hash,
                ),
            ]
        )
        await session.commit()

    await _add_events(sessions, tenant_id, 2)

    async with sessions() as session:
        verification = await SqlAlchemyAuditEventRepository(session).verify_integrity(
            tenant_id=tenant_id
        )
        algorithms = [
            row[0]
            for row in (
                await session.execute(
                    select(AuditEventRecord.hash_algorithm)
                    .where(AuditEventRecord.tenant_id == tenant_id)
                    .order_by(AuditEventRecord.sequence)
                )
            ).all()
        ]
    assert verification.valid
    assert verification.checked_events == 4
    assert algorithms == [
        LEGACY_SHA256_ALGORITHM,
        LEGACY_SHA256_ALGORITHM,
        HMAC_SHA256_V1_ALGORITHM,
        HMAC_SHA256_V1_ALGORITHM,
    ]
    await engine.dispose()


@pytest.mark.asyncio
async def test_legacy_algorithm_after_hmac_event_is_rejected() -> None:
    """HMAC 事件之后再出现 legacy 算法事件即为降级，必须判定失败（不依赖封印）。"""

    configure_audit_hashing(KEY_A)
    engine = await _make_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    tenant_id = uuid4()
    await _add_events(sessions, tenant_id, 1)

    async with sessions() as session:
        head = (
            await session.execute(
                select(AuditEventRecord).where(AuditEventRecord.tenant_id == tenant_id)
            )
        ).scalar_one()
        downgraded = _legacy_record(
            tenant_id=tenant_id,
            sequence=2,
            previous_hash=head.event_hash,
            occurred_at=datetime.now(UTC),
            action="downgraded.event",
        )
        session.add(downgraded)
        state = await session.get(AuditChainStateRecord, tenant_id)
        assert state is not None
        state.head_sequence = 2
        state.head_hash = downgraded.event_hash
        # 即便封印被（假设性地）用真实密钥重算，降级事件本身也必须被拒绝。
        state.head_seal = AuditHasher(KEY_A).chain_head_seal(
            tenant_id=tenant_id,
            head_sequence=2,
            head_hash=downgraded.event_hash,
            retained_from_sequence=state.retained_from_sequence,
            retention_previous_hash=state.retention_previous_hash,
        )
        await session.commit()

    async with sessions() as session:
        verification = await SqlAlchemyAuditEventRepository(session).verify_integrity(
            tenant_id=tenant_id
        )
    assert not verification.valid
    assert verification.first_invalid_sequence == 2
    await engine.dispose()


@pytest.mark.asyncio
async def test_audit_write_and_verify_fail_closed_without_key() -> None:
    configure_audit_hashing(None)
    engine = await _make_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    tenant_id = uuid4()

    async with sessions() as session:
        repository = SqlAlchemyAuditEventRepository(session)
        with pytest.raises(AuditHmacKeyNotConfiguredError):
            await repository.add(
                AuditEventCreate(
                    tenant_id=tenant_id,
                    actor_user_id=None,
                    action="fail.closed",
                    resource_type="test",
                )
            )
        with pytest.raises(AuditHmacKeyNotConfiguredError):
            await repository.verify_integrity(tenant_id=tenant_id)
    await engine.dispose()


@pytest.mark.asyncio
async def test_retention_purge_keeps_chain_seal_valid() -> None:
    configure_audit_hashing(KEY_A)
    engine = await _make_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    tenant_id = uuid4()
    await _add_events(sessions, tenant_id, 4)

    async with sessions() as session:
        repository = SqlAlchemyAuditEventRepository(session)
        purged = await repository.purge_before(
            tenant_id=tenant_id,
            cutoff=datetime.now(UTC) + timedelta(days=1),
            limit=2,
        )
        await session.commit()
    assert purged == 2

    async with sessions() as session:
        verification = await SqlAlchemyAuditEventRepository(session).verify_integrity(
            tenant_id=tenant_id
        )
    assert verification.valid
    assert verification.checked_events == 2
    await engine.dispose()


@pytest.mark.asyncio
async def test_tampered_retention_boundary_is_detected() -> None:
    """攻击者移动保留边界并删除前缀事件来隐藏记录，必须被封印检出。"""

    configure_audit_hashing(KEY_A)
    engine = await _make_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    tenant_id = uuid4()
    await _add_events(sessions, tenant_id, 3)

    async with sessions() as session:
        first = (
            await session.execute(
                select(AuditEventRecord)
                .where(
                    AuditEventRecord.tenant_id == tenant_id,
                    AuditEventRecord.sequence == 1,
                )
            )
        ).scalar_one()
        state = await session.get(AuditChainStateRecord, tenant_id)
        assert state is not None
        state.retained_from_sequence = 2
        state.retention_previous_hash = first.event_hash
        await session.delete(first)
        await session.commit()

    async with sessions() as session:
        verification = await SqlAlchemyAuditEventRepository(session).verify_integrity(
            tenant_id=tenant_id
        )
    assert not verification.valid
    await engine.dispose()


def test_create_app_configures_audit_hashing_from_settings() -> None:
    class AllowAllRateLimiter:
        async def ensure_allowed(self, *, scope: str, key: str) -> None:
            del scope, key

    class NullKnowledgeProvider:
        provider_name = "null-knowledge"

        async def create_dataset(self, **kwargs: object) -> None:
            raise NotImplementedError

        async def delete_dataset(self, provider_id: str) -> None:
            del provider_id

    class NullSkillStorage:
        async def put(self, *, key: str, content: bytes) -> None:
            del key, content

        async def get(self, *, key: str) -> bytes:
            raise KeyError(key)

        async def delete(self, *, key: str) -> None:
            del key

    configure_audit_hashing(None)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    create_app(
        settings=AppSettings(
            auth_cookie_secure=False,
            audit_hmac_key="contract-create-app-audit-key-0001",
        ),
        session_factory=async_sessionmaker(engine, expire_on_commit=False),
        auth_rate_limiter=AllowAllRateLimiter(),
        knowledge_provider=NullKnowledgeProvider(),
        skill_storage=NullSkillStorage(),
    )

    hasher = active_audit_hasher()
    assert hasher is not None
    assert hasher.event_hash(b"probe") == hmac.new(
        b"contract-create-app-audit-key-0001", b"probe", hashlib.sha256
    ).hexdigest()
