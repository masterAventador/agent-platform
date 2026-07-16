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

    async with sessions() as session:
        purged = await purge_expired_audit_events(
            session,
            cutoff=datetime.now(UTC) - timedelta(days=7),
            limit=100,
        )
        await session.commit()

    assert purged == 3
    assert await _count_events(sessions, first_tenant) == 1
    assert await _count_events(sessions, second_tenant) == 2
    async with sessions() as session:
        repository = SqlAlchemyAuditEventRepository(session)
        assert (await repository.verify_integrity(tenant_id=first_tenant)).valid is True
        assert (await repository.verify_integrity(tenant_id=second_tenant)).valid is True
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
