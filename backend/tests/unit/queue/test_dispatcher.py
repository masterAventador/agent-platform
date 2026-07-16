from collections.abc import AsyncIterator
from dataclasses import replace
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
from agent_platform.infrastructure.queue.dispatcher import RunCommandDispatcher
from agent_platform.infrastructure.queue.redis_streams import RunQueueMessage
from agent_platform.platform.runs.commands import RunCommand, RunCommandAction
from agent_platform.platform.runs.entities import Run


class RecordingQueue:
    def __init__(self) -> None:
        self.enqueued: list[RunQueueMessage] = []

    async def enqueue(self, message: RunQueueMessage) -> None:
        self.enqueued.append(message)


@pytest_asyncio.fixture
async def factory() -> AsyncIterator[async_sessionmaker]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    load_database_models()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.mark.asyncio
async def test_dispatcher_never_enqueues_followup_intent_commands(factory) -> None:
    """FOLLOWUP 意图命令只作结算标记：即使异常残留为未分发，也不得进入执行队列。"""
    run = Run.create(
        tenant_id=uuid4(),
        employee_id=uuid4(),
        employee_version=1,
        created_by=uuid4(),
        input_data={"message": "第一轮"},
    )
    start_command = RunCommand.create(
        run_id=run.id, tenant_id=run.tenant_id, action=RunCommandAction.START
    )
    followup_command = RunCommand.create(
        run_id=run.id,
        tenant_id=run.tenant_id,
        action=RunCommandAction.FOLLOWUP,
        payload={"message_id": str(uuid4()), "requested_by": str(run.created_by)},
    )
    # 异常场景：followup 未按约定在创建时置 dispatched_at，落入 pending 扫描范围
    followup_command = replace(followup_command, dispatched_at=None)
    async with factory() as session:
        await SqlAlchemyRunRepository(session).add(run)
        commands = SqlAlchemyRunCommandRepository(session)
        await commands.add(start_command)
        await commands.add(followup_command)
        await session.commit()
    queue = RecordingQueue()

    dispatched = await RunCommandDispatcher(
        session_factory=factory,
        queue=queue,  # type: ignore[arg-type]
    ).dispatch_pending()

    assert dispatched == 1
    assert [message.command_id for message in queue.enqueued] == [start_command.id]
    async with factory() as session:
        commands = SqlAlchemyRunCommandRepository(session)
        persisted_start = await commands.get(start_command.id)
        persisted_followup = await commands.get(followup_command.id)
    assert persisted_start is not None and persisted_start.dispatched_at is not None
    # followup 被兜底结清（不再被反复扫描），但从未进入队列
    assert persisted_followup is not None and persisted_followup.dispatched_at is not None
    assert all(message.action != "followup" for message in queue.enqueued)
