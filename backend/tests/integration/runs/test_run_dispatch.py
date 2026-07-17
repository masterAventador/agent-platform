"""Run 创建共享路径：API 直跑、会话轮次与定时调度必须产生同构的 Run + START 命令。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agent_platform.infrastructure.database.base import Base
from agent_platform.infrastructure.database.models import load_database_models
from agent_platform.infrastructure.database.repositories.run_dispatch import (
    create_employee_run,
)
from agent_platform.infrastructure.database.repositories.runs import RunCommandRecord
from agent_platform.platform.runs.commands import RunCommandAction
from agent_platform.platform.runs.entities import RunStatus


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    load_database_models()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as active_session:
        yield active_session
    await engine.dispose()


@pytest.mark.asyncio
async def test_shared_path_creates_a_queued_run_with_its_start_command(
    session: AsyncSession,
) -> None:
    tenant_id, employee_id, user_id = uuid4(), uuid4(), uuid4()

    run = await create_employee_run(
        database_session=session,
        tenant_id=tenant_id,
        employee_id=employee_id,
        employee_version=3,
        created_by=user_id,
        input_data={"topic": "巡检"},
    )
    await session.flush()

    assert run.status is RunStatus.QUEUED
    assert run.employee_version == 3
    assert run.thread_id == str(run.id)

    commands = (
        await session.execute(select(RunCommandRecord).where(RunCommandRecord.run_id == run.id))
    ).scalars()
    actions = [command.action for command in commands]
    assert actions == [RunCommandAction.START.value]


@pytest.mark.asyncio
async def test_shared_path_honours_an_explicit_run_id_for_deterministic_dispatch(
    session: AsyncSession,
) -> None:
    run_id = uuid4()

    run = await create_employee_run(
        database_session=session,
        tenant_id=uuid4(),
        employee_id=uuid4(),
        employee_version=1,
        created_by=uuid4(),
        input_data={},
        run_id=run_id,
    )
    await session.flush()

    assert run.id == run_id
    assert run.thread_id == str(run_id)
