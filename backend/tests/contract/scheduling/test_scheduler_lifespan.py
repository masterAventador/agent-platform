"""C12 调度循环随 API 生命周期运行：配置驱动、真实产生 Run、可优雅取消。"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_platform.api.app import SCHEDULER_TASK_NAME, create_app
from agent_platform.config import AppSettings
from agent_platform.infrastructure.database.base import Base
from agent_platform.infrastructure.database.models import load_database_models
from agent_platform.infrastructure.database.repositories.runs import RunRecord
from agent_platform.infrastructure.database.repositories.scheduling import (
    SqlAlchemyScheduledTaskRepository,
)
from agent_platform.platform.scheduling.entities import ScheduledTask
from agent_platform.platform.scheduling.schedule import Schedule
from tests.integration.scheduling.conftest import seed_workspace


class AllowAllRateLimiter:
    async def ensure_allowed(self, *, scope: str, key: str) -> None:
        del scope, key


def _scheduler_tasks() -> list[asyncio.Task]:
    return [
        task
        for task in asyncio.all_tasks()
        if task.get_name() == SCHEDULER_TASK_NAME and not task.done()
    ]


async def count_runs(factory: async_sessionmaker) -> int:
    async with factory() as session:
        total = await session.execute(select(func.count()).select_from(RunRecord))
        return int(total.scalar_one())


@pytest.mark.asyncio
async def test_the_api_lifespan_dispatches_a_due_scheduled_task() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    load_database_models()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    seed = await seed_workspace(sessions)

    now = datetime.now(UTC)
    async with sessions() as session:
        await SqlAlchemyScheduledTaskRepository(session).add(
            ScheduledTask.create(
                tenant_id=seed.tenant_id,
                employee_id=seed.employee_id,
                created_by=seed.user_id,
                name="马上跑一次",
                schedule=Schedule.once(run_at=now + timedelta(seconds=1), timezone="UTC"),
                input_data={"topic": "巡检"},
                now=now,
            )
        )
        await session.commit()

    app = create_app(
        settings=AppSettings(
            auth_cookie_secure=False,
            scheduler_enabled=True,
            scheduler_tick_interval_seconds=1,
        ),
        session_factory=sessions,
        auth_rate_limiter=AllowAllRateLimiter(),
    )

    async with app.router.lifespan_context(app):
        for _ in range(100):
            if await count_runs(sessions) == 1:
                break
            await asyncio.sleep(0.05)
        running = _scheduler_tasks()
        assert len(running) == 1
        scheduler_task = running[0]
        assert scheduler_task.done() is False

    # 退出 lifespan 后调度任务必须已被取消并 await 干净，不留悬挂任务。
    assert scheduler_task.done() is True
    assert scheduler_task.cancelled() is True
    assert _scheduler_tasks() == []
    assert await count_runs(sessions) == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_the_scheduler_loop_can_be_disabled_by_configuration() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    load_database_models()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    seed = await seed_workspace(sessions)

    now = datetime.now(UTC)
    async with sessions() as session:
        await SqlAlchemyScheduledTaskRepository(session).add(
            ScheduledTask.create(
                tenant_id=seed.tenant_id,
                employee_id=seed.employee_id,
                created_by=seed.user_id,
                name="不该被跑",
                schedule=Schedule.once(run_at=now - timedelta(hours=1), timezone="UTC"),
                input_data={"topic": "巡检"},
                now=now - timedelta(hours=2),
            )
        )
        await session.commit()

    app = create_app(
        settings=AppSettings(auth_cookie_secure=False, scheduler_enabled=False),
        session_factory=sessions,
        auth_rate_limiter=AllowAllRateLimiter(),
    )

    async with app.router.lifespan_context(app):
        await asyncio.sleep(0.2)
        assert _scheduler_tasks() == []

    assert await count_runs(sessions) == 0
    await engine.dispose()
