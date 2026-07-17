"""审计事件保留清扫：配置驱动、固定间隔节流、随 API 生命周期自动运行。"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agent_platform.api.app import create_app
from agent_platform.config import AppSettings
from agent_platform.infrastructure.database.base import Base
from agent_platform.infrastructure.database.models import load_database_models
from agent_platform.infrastructure.database.repositories.audit import (
    AuditEventCreate,
    AuditEventRecord,
    SqlAlchemyAuditEventRepository,
    purge_expired_audit_events,
)
from agent_platform.platform.knowledge.models import KnowledgeDataset


class AllowAllRateLimiter:
    async def ensure_allowed(self, *, scope: str, key: str) -> None:
        del scope, key


class FakeKnowledgeProvider:
    provider_name = "fake-knowledge"

    async def create_dataset(
        self,
        *,
        name: str,
        description: str = "",
        chunk_method: str = "naive",
    ) -> KnowledgeDataset:
        del description, chunk_method
        return KnowledgeDataset(provider_id=f"dataset-{name}", name=name)

    async def delete_dataset(self, provider_id: str) -> None:
        del provider_id


class InMemorySkillStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def put(self, *, key: str, content: bytes) -> None:
        self.objects[key] = content

    async def get(self, *, key: str) -> bytes:
        return self.objects[key]

    async def delete(self, *, key: str) -> None:
        self.objects.pop(key, None)


async def _seed_tenant_events(
    sessions: async_sessionmaker[AsyncSession],
    tenant_id: UUID,
    *,
    expired: int,
    fresh: int,
    expired_age: timedelta,
) -> None:
    async with sessions() as session:
        repository = SqlAlchemyAuditEventRepository(session)
        expired_ids = []
        for index in range(expired):
            event = await repository.add(
                AuditEventCreate(
                    tenant_id=tenant_id,
                    actor_user_id=None,
                    action=f"retention.expired_{index}",
                    resource_type="test",
                )
            )
            expired_ids.append(event.id)
        for index in range(fresh):
            await repository.add(
                AuditEventCreate(
                    tenant_id=tenant_id,
                    actor_user_id=None,
                    action=f"retention.fresh_{index}",
                    resource_type="test",
                )
            )
        if expired_ids:
            await session.execute(
                update(AuditEventRecord)
                .where(AuditEventRecord.id.in_(expired_ids))
                .values(occurred_at=datetime.now(UTC) - expired_age)
            )
        await session.commit()


async def _count_events(
    sessions: async_sessionmaker[AsyncSession],
    tenant_id: UUID,
) -> int:
    async with sessions() as session:
        return (
            await session.scalar(
                select(func.count())
                .select_from(AuditEventRecord)
                .where(AuditEventRecord.tenant_id == tenant_id)
            )
        ) or 0


@pytest.mark.asyncio
async def test_purge_expired_audit_events_sweeps_every_tenant_and_keeps_chain_valid() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    load_database_models()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    first_tenant = uuid4()
    second_tenant = uuid4()
    await _seed_tenant_events(
        sessions,
        first_tenant,
        expired=2,
        fresh=1,
        expired_age=timedelta(days=10),
    )
    await _seed_tenant_events(
        sessions,
        second_tenant,
        expired=1,
        fresh=2,
        expired_age=timedelta(days=10),
    )

    result = await purge_expired_audit_events(
        sessions,
        cutoff=datetime.now(UTC) - timedelta(days=7),
        limit=100,
    )

    assert result.purged_events == 3
    assert result.failed_tenants == 0
    assert await _count_events(sessions, first_tenant) == 1
    assert await _count_events(sessions, second_tenant) == 2
    async with sessions() as session:
        repository = SqlAlchemyAuditEventRepository(session)
        assert (await repository.verify_integrity(tenant_id=first_tenant)).valid is True
        assert (await repository.verify_integrity(tenant_id=second_tenant)).valid is True
    await engine.dispose()


@pytest.mark.asyncio
async def test_purge_commits_each_tenant_before_processing_the_next(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """第一个租户的链锁必须在处理后续租户前随事务提交释放，禁止跨租户持锁。"""

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    load_database_models()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    call_log: list[str] = []

    class CommitLoggingSession(AsyncSession):
        async def commit(self) -> None:
            call_log.append("commit")
            await super().commit()

    sessions = async_sessionmaker(engine, expire_on_commit=False, class_=CommitLoggingSession)
    first_tenant = uuid4()
    second_tenant = uuid4()
    await _seed_tenant_events(
        sessions,
        first_tenant,
        expired=1,
        fresh=1,
        expired_age=timedelta(days=10),
    )
    await _seed_tenant_events(
        sessions,
        second_tenant,
        expired=1,
        fresh=1,
        expired_age=timedelta(days=10),
    )

    original_purge_before = SqlAlchemyAuditEventRepository.purge_before

    async def logging_purge_before(  # type: ignore[no-untyped-def]
        self,
        *,
        tenant_id,
        cutoff,
        limit,
    ):
        call_log.append(f"purge:{tenant_id}")
        return await original_purge_before(self, tenant_id=tenant_id, cutoff=cutoff, limit=limit)

    monkeypatch.setattr(SqlAlchemyAuditEventRepository, "purge_before", logging_purge_before)
    call_log.clear()

    result = await purge_expired_audit_events(
        sessions,
        cutoff=datetime.now(UTC) - timedelta(days=7),
        limit=100,
    )

    assert result.purged_events == 2
    assert result.failed_tenants == 0
    purge_indexes = [index for index, entry in enumerate(call_log) if entry.startswith("purge:")]
    assert len(purge_indexes) == 2
    assert "commit" in call_log[purge_indexes[0] + 1 : purge_indexes[1]], (
        f"第一个租户的清理必须在处理第二个租户前提交，链锁不得跨租户持有: {call_log!r}"
    )
    assert "commit" in call_log[purge_indexes[1] + 1 :], (
        f"第二个租户的清理也必须在自己的事务内提交: {call_log!r}"
    )
    await engine.dispose()


@pytest.mark.asyncio
async def test_purge_failure_in_one_tenant_does_not_affect_other_tenants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """单租户清理失败只影响该租户自身，其余租户照常清理，部分成功语义明确。"""

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    load_database_models()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    first_tenant = uuid4()
    second_tenant = uuid4()
    await _seed_tenant_events(
        sessions,
        first_tenant,
        expired=1,
        fresh=1,
        expired_age=timedelta(days=10),
    )
    await _seed_tenant_events(
        sessions,
        second_tenant,
        expired=1,
        fresh=1,
        expired_age=timedelta(days=10),
    )

    original_purge_before = SqlAlchemyAuditEventRepository.purge_before

    async def failing_purge_before(  # type: ignore[no-untyped-def]
        self,
        *,
        tenant_id,
        cutoff,
        limit,
    ):
        if tenant_id == first_tenant:
            raise RuntimeError("simulated tenant purge failure")
        return await original_purge_before(self, tenant_id=tenant_id, cutoff=cutoff, limit=limit)

    monkeypatch.setattr(SqlAlchemyAuditEventRepository, "purge_before", failing_purge_before)

    result = await purge_expired_audit_events(
        sessions,
        cutoff=datetime.now(UTC) - timedelta(days=7),
        limit=100,
    )

    assert result.purged_events == 1
    assert result.failed_tenants == 1
    assert await _count_events(sessions, first_tenant) == 2
    assert await _count_events(sessions, second_tenant) == 1
    async with sessions() as session:
        repository = SqlAlchemyAuditEventRepository(session)
        assert (await repository.verify_integrity(tenant_id=second_tenant)).valid is True

    monkeypatch.setattr(SqlAlchemyAuditEventRepository, "purge_before", original_purge_before)
    recovery = await purge_expired_audit_events(
        sessions,
        cutoff=datetime.now(UTC) - timedelta(days=7),
        limit=100,
    )

    assert recovery.purged_events == 1
    assert recovery.failed_tenants == 0
    assert await _count_events(sessions, first_tenant) == 1
    async with sessions() as session:
        repository = SqlAlchemyAuditEventRepository(session)
        assert (await repository.verify_integrity(tenant_id=first_tenant)).valid is True
    await engine.dispose()


@pytest.mark.asyncio
async def test_api_lifespan_runs_configuration_driven_audit_retention_sweep() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    load_database_models()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    tenant_id = uuid4()
    await _seed_tenant_events(
        sessions,
        tenant_id,
        expired=2,
        fresh=1,
        expired_age=timedelta(days=3),
    )
    app = create_app(
        settings=AppSettings(
            auth_cookie_secure=False,
            audit_retention_days=1,
            audit_retention_sweep_interval_seconds=3600,
            # 本用例只验证审计清扫。内存 SQLite 的所有 session 共享同一条连接
            # （StaticPool），并发的后台循环会互相干扰；真实 PostgreSQL 下每个
            # session 独占连接，两者并存已验证正常。关掉无关的调度循环保持隔离。
            scheduler_enabled=False,
        ),
        session_factory=sessions,
        auth_rate_limiter=AllowAllRateLimiter(),
        knowledge_provider=FakeKnowledgeProvider(),
        skill_storage=InMemorySkillStorage(),
    )

    async with app.router.lifespan_context(app):
        for _ in range(100):
            if await _count_events(sessions, tenant_id) == 1:
                break
            await asyncio.sleep(0.05)

    assert await _count_events(sessions, tenant_id) == 1
    async with sessions() as session:
        verification = await SqlAlchemyAuditEventRepository(session).verify_integrity(
            tenant_id=tenant_id
        )
    assert verification.valid is True
    await engine.dispose()
