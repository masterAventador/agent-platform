"""审计完整性校验必须分块滚动读取，禁止一次性物化整租户审计表。"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, event, update
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from agent_platform.infrastructure.database.base import Base
from agent_platform.infrastructure.database.models import load_database_models
from agent_platform.infrastructure.database.repositories.audit import (
    AuditEvent,
    AuditEventCreate,
    AuditEventRecord,
    SqlAlchemyAuditEventRepository,
)


async def _make_engine() -> AsyncEngine:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    load_database_models()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine


async def _seed_events(
    sessions: async_sessionmaker[AsyncSession],
    tenant_id: UUID,
    count: int,
) -> list[AuditEvent]:
    async with sessions() as session:
        repository = SqlAlchemyAuditEventRepository(session)
        events = [
            await repository.add(
                AuditEventCreate(
                    tenant_id=tenant_id,
                    actor_user_id=None,
                    action=f"chunk.event_{index}",
                    resource_type="test",
                )
            )
            for index in range(count)
        ]
        await session.commit()
    return events


@pytest.mark.asyncio
async def test_verify_integrity_reads_audit_events_in_bounded_chunks() -> None:
    engine = await _make_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    tenant_id = uuid4()
    await _seed_events(sessions, tenant_id, 5)

    audit_event_selects: list[str] = []

    def record_statement(  # type: ignore[no-untyped-def]
        conn,
        cursor,
        statement,
        parameters,
        context,
        executemany,
    ) -> None:
        normalized = statement.lstrip().upper()
        if normalized.startswith("SELECT") and "audit_events" in statement:
            audit_event_selects.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", record_statement)
    try:
        async with sessions() as session:
            verification = await SqlAlchemyAuditEventRepository(session).verify_integrity(
                tenant_id=tenant_id,
                batch_size=2,
            )
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", record_statement)

    assert verification.valid is True
    assert verification.checked_events == 5
    assert len(audit_event_selects) == 3, (
        f"5 条事件按 batch_size=2 应分 3 次滚动查询: {audit_event_selects!r}"
    )
    assert all("LIMIT" in statement.upper() for statement in audit_event_selects), (
        f"每次滚动查询都必须携带 LIMIT: {audit_event_selects!r}"
    )
    await engine.dispose()


@pytest.mark.asyncio
async def test_chunked_verification_still_detects_tampering_across_chunk_boundary() -> None:
    engine = await _make_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    tenant_id = uuid4()
    events = await _seed_events(sessions, tenant_id, 5)

    async with sessions() as session:
        await session.execute(
            update(AuditEventRecord)
            .where(AuditEventRecord.id == events[3].id)
            .values(metadata_json={"safe": "tampered"})
        )
        await session.commit()

    async with sessions() as session:
        verification = await SqlAlchemyAuditEventRepository(session).verify_integrity(
            tenant_id=tenant_id,
            batch_size=2,
        )

    assert verification.valid is False
    assert verification.first_invalid_sequence == events[3].sequence
    assert verification.checked_events == events[3].sequence - 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_chunked_verification_still_detects_tail_deletion() -> None:
    engine = await _make_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    tenant_id = uuid4()
    events = await _seed_events(sessions, tenant_id, 3)

    async with sessions() as session:
        await session.execute(
            delete(AuditEventRecord).where(AuditEventRecord.id == events[-1].id)
        )
        await session.commit()

    async with sessions() as session:
        verification = await SqlAlchemyAuditEventRepository(session).verify_integrity(
            tenant_id=tenant_id,
            batch_size=2,
        )

    assert verification.valid is False
    assert verification.first_invalid_sequence == events[-1].sequence
    assert verification.checked_events == len(events) - 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_verify_integrity_rejects_out_of_range_batch_size() -> None:
    engine = await _make_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    tenant_id = uuid4()
    await _seed_events(sessions, tenant_id, 1)

    async with sessions() as session:
        repository = SqlAlchemyAuditEventRepository(session)
        with pytest.raises(ValueError):
            await repository.verify_integrity(tenant_id=tenant_id, batch_size=0)
        with pytest.raises(ValueError):
            await repository.verify_integrity(tenant_id=tenant_id, batch_size=10_001)
    await engine.dispose()
