from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_platform.infrastructure.database.base import Base
from agent_platform.infrastructure.database.models import load_database_models
from agent_platform.infrastructure.database.repositories.runs import (
    SqlAlchemyRunCommandRepository,
    SqlAlchemyRunRepository,
)
from agent_platform.platform.runs.commands import RunCommand, RunCommandAction
from agent_platform.platform.runs.entities import Run


@pytest_asyncio.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    load_database_models()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_run_and_start_command_are_committed_atomically(session_factory) -> None:
    run = Run.create(
        tenant_id=uuid4(),
        employee_id=uuid4(),
        employee_version=1,
        created_by=uuid4(),
        input_data={"task": "可靠执行"},
    )
    command = RunCommand.create(
        run_id=run.id, tenant_id=run.tenant_id, action=RunCommandAction.START
    )
    async with session_factory() as session:
        await SqlAlchemyRunRepository(session).add(run)
        await SqlAlchemyRunCommandRepository(session).add(command)
        await session.commit()

    async with session_factory() as session:
        pending = await SqlAlchemyRunCommandRepository(session).pending()
        assert pending == [command]


@pytest.mark.asyncio
async def test_rollback_removes_run_and_command_together(session_factory) -> None:
    run = Run.create(
        tenant_id=uuid4(),
        employee_id=uuid4(),
        employee_version=1,
        created_by=uuid4(),
        input_data={},
    )
    async with session_factory() as session:
        await SqlAlchemyRunRepository(session).add(run)
        await SqlAlchemyRunCommandRepository(session).add(
            RunCommand.create(run_id=run.id, tenant_id=run.tenant_id, action=RunCommandAction.START)
        )
        await session.rollback()

    async with session_factory() as session:
        assert await SqlAlchemyRunCommandRepository(session).pending() == []
        assert (
            await SqlAlchemyRunRepository(session).get(tenant_id=run.tenant_id, run_id=run.id)
            is None
        )
