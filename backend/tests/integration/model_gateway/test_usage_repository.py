"""模型用量记录仓储：写入、租户隔离查询、keyset 分页、有界清扫（C16 阶段二）。

用 sqlite 建表跑读写与分页语义；真实多副本并发写与真实清扫由 PG 门禁另测。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_platform.infrastructure.database.base import Base
from agent_platform.infrastructure.database.models import load_database_models
from agent_platform.infrastructure.database.repositories.model_usage import (
    ModelUsageRow,
    SessionModelUsageRecorder,
    SqlAlchemyModelUsageRepository,
)
from agent_platform.platform.model_gateway.usage import (
    ModelCallOutcome,
    ModelUsageQuery,
    ModelUsageRecord,
)

NOW = datetime(2026, 7, 17, 12, 0, tzinfo=UTC)


def _record(
    tenant_id,
    *,
    recorded_at=NOW,
    run_id="auto",
    employee_id="auto",
    outcome=ModelCallOutcome.SUCCESS,
) -> ModelUsageRecord:
    error_type = None if outcome is ModelCallOutcome.SUCCESS else "ReadTimeout"
    known_cost = outcome is ModelCallOutcome.SUCCESS
    return ModelUsageRecord(
        id=uuid4(),
        tenant_id=tenant_id,
        run_id=uuid4() if run_id == "auto" else run_id,
        employee_id=uuid4() if employee_id == "auto" else employee_id,
        model_alias="general-purpose",
        prompt_tokens=10 if known_cost else None,
        completion_tokens=5 if known_cost else None,
        total_tokens=15 if known_cost else None,
        latency_ms=42,
        outcome=outcome,
        error_type=error_type,
        cost_nanousd=1_000 if known_cost else None,
        cost_source="platform_pricing_table" if known_cost else None,
        recorded_at=recorded_at,
    )


@pytest.fixture
def sessions(tmp_path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'usage.db'}")
    load_database_models()
    return engine


async def _create_all(engine) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


@pytest.mark.asyncio
async def test_record_then_query_returns_it_tenant_scoped(sessions) -> None:
    engine = sessions
    await _create_all(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    tenant_a, tenant_b = uuid4(), uuid4()
    recorder = SessionModelUsageRecorder(factory)
    rec = _record(tenant_a)
    await recorder.record(rec)
    await recorder.record(_record(tenant_b))

    async with factory() as session:
        page = await SqlAlchemyModelUsageRepository(session).query(
            ModelUsageQuery(tenant_id=tenant_a)
        )
    assert [r.id for r in page.records] == [rec.id]
    assert page.records[0].model_alias == "general-purpose"
    assert page.records[0].cost_nanousd == 1_000


@pytest.mark.asyncio
async def test_cross_tenant_query_returns_nothing(sessions) -> None:
    engine = sessions
    await _create_all(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    tenant_a, tenant_b = uuid4(), uuid4()
    await SessionModelUsageRecorder(factory).record(_record(tenant_a))
    async with factory() as session:
        page = await SqlAlchemyModelUsageRepository(session).query(
            ModelUsageQuery(tenant_id=tenant_b)
        )
    assert page.records == ()
    assert page.next_cursor is None


@pytest.mark.asyncio
async def test_null_attribution_record_persists_and_is_queryable(sessions) -> None:
    engine = sessions
    await _create_all(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    tenant = uuid4()
    rec = _record(tenant, run_id=None, employee_id=None)
    await SessionModelUsageRecorder(factory).record(rec)
    async with factory() as session:
        page = await SqlAlchemyModelUsageRepository(session).query(
            ModelUsageQuery(tenant_id=tenant)
        )
    assert [r.id for r in page.records] == [rec.id]
    assert page.records[0].run_id is None
    assert page.records[0].employee_id is None


@pytest.mark.asyncio
async def test_error_record_roundtrips_with_null_tokens(sessions) -> None:
    engine = sessions
    await _create_all(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    tenant = uuid4()
    rec = _record(tenant, outcome=ModelCallOutcome.ERROR)
    await SessionModelUsageRecorder(factory).record(rec)
    async with factory() as session:
        page = await SqlAlchemyModelUsageRepository(session).query(
            ModelUsageQuery(tenant_id=tenant)
        )
    got = page.records[0]
    assert got.outcome is ModelCallOutcome.ERROR
    assert got.error_type == "ReadTimeout"
    assert got.prompt_tokens is None
    assert got.cost_nanousd is None


@pytest.mark.asyncio
async def test_time_range_filter(sessions) -> None:
    engine = sessions
    await _create_all(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    tenant = uuid4()
    recorder = SessionModelUsageRecorder(factory)
    old = _record(tenant, recorded_at=NOW - timedelta(days=10))
    mid = _record(tenant, recorded_at=NOW - timedelta(days=5))
    new = _record(tenant, recorded_at=NOW)
    for r in (old, mid, new):
        await recorder.record(r)
    async with factory() as session:
        page = await SqlAlchemyModelUsageRepository(session).query(
            ModelUsageQuery(
                tenant_id=tenant,
                start=NOW - timedelta(days=7),
                end=NOW + timedelta(seconds=1),
            )
        )
    assert {r.id for r in page.records} == {mid.id, new.id}


@pytest.mark.asyncio
async def test_keyset_pagination_is_stable_and_complete(sessions) -> None:
    engine = sessions
    await _create_all(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    tenant = uuid4()
    recorder = SessionModelUsageRecorder(factory)
    made = []
    for i in range(5):
        r = _record(tenant, recorded_at=NOW + timedelta(seconds=i))
        made.append(r)
        await recorder.record(r)
    seen: list = []
    cursor = None
    async with factory() as session:
        repo = SqlAlchemyModelUsageRepository(session)
        for _ in range(10):
            page = await repo.query(
                ModelUsageQuery(tenant_id=tenant, limit=2, cursor=cursor)
            )
            seen.extend(r.id for r in page.records)
            cursor = page.next_cursor
            if cursor is None:
                break
    # 全部 5 条恰好各出现一次，最新在前
    assert seen == [r.id for r in reversed(made)]


@pytest.mark.asyncio
async def test_prune_deletes_old_keeps_recent_and_is_bounded(sessions) -> None:
    engine = sessions
    await _create_all(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    tenant = uuid4()
    recorder = SessionModelUsageRecorder(factory)
    for i in range(4):
        await recorder.record(_record(tenant, recorded_at=NOW - timedelta(days=100 + i)))
    recent = _record(tenant, recorded_at=NOW)
    await recorder.record(recent)
    async with factory() as session:
        repo = SqlAlchemyModelUsageRepository(session)
        deleted = await repo.prune_older_than(NOW - timedelta(days=90), limit=2)
        await session.commit()
    assert deleted == 2  # 受 limit 约束，一次只删 2 条
    async with factory() as session:
        remaining = (
            await session.execute(select(func.count()).select_from(ModelUsageRow))
        ).scalar_one()
    assert remaining == 3  # 5 - 2
