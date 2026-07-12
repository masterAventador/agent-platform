from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_platform.infrastructure.database.base import Base
from agent_platform.infrastructure.database.models import load_database_models
from agent_platform.infrastructure.database.repositories.runs import SqlAlchemyRunCommandRepository
from agent_platform.infrastructure.queue.dispatcher import RunCommandDispatcher
from agent_platform.platform.runs.commands import RunCommand, RunCommandAction


class RecordingQueue:
    def __init__(self) -> None:
        self.messages = []

    async def enqueue(self, message):
        self.messages.append(message)
        return "1-0"


@pytest_asyncio.fixture
async def factory() -> AsyncIterator[async_sessionmaker]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    load_database_models()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    value = async_sessionmaker(engine, expire_on_commit=False)
    yield value
    await engine.dispose()


@pytest.mark.asyncio
async def test_dispatcher_sends_pending_command_and_marks_it_dispatched(factory) -> None:
    command = RunCommand.create(run_id=uuid4(), tenant_id=uuid4(), action=RunCommandAction.START)
    async with factory() as session:
        await SqlAlchemyRunCommandRepository(session).add(command)
        await session.commit()
    queue = RecordingQueue()

    count = await RunCommandDispatcher(session_factory=factory, queue=queue).dispatch_pending()

    assert count == 1
    assert queue.messages[0].command_id == command.id
    async with factory() as session:
        assert await SqlAlchemyRunCommandRepository(session).pending() == []
