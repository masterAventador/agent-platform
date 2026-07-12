from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_platform.infrastructure.database.base import Base
from agent_platform.infrastructure.database.models import load_database_models
from agent_platform.infrastructure.database.repositories.employees import (
    SqlAlchemyEmployeeVersionRepository,
)
from agent_platform.infrastructure.database.repositories.runs import (
    SqlAlchemyRunCommandRepository,
    SqlAlchemyRunEventRepository,
    SqlAlchemyRunRepository,
)
from agent_platform.infrastructure.queue.redis_streams import RunQueueDelivery, RunQueueMessage
from agent_platform.platform.employees.entities import EmployeeVersion
from agent_platform.platform.runs.commands import RunCommand, RunCommandAction
from agent_platform.platform.runs.entities import Run, RunStatus
from agent_platform.platform.runs.events import EventType, PlatformEvent
from agent_platform.runtimes.base import RuntimeStartRequest, RuntimeState
from agent_platform.workers.run_worker import RunWorker


class CompletingRuntime:
    def __init__(self) -> None:
        self.events: list[PlatformEvent] = []
        self.state: RuntimeState | None = None

    async def start(self, request: RuntimeStartRequest) -> RuntimeState:
        self.events = [
            PlatformEvent.create(
                tenant_id=request.tenant_id,
                employee_id=request.employee_id,
                run_id=request.run_id,
                sequence=1,
                event_type=EventType.RUN_STARTED,
                payload={},
            ),
            PlatformEvent.create(
                tenant_id=request.tenant_id,
                employee_id=request.employee_id,
                run_id=request.run_id,
                sequence=2,
                event_type=EventType.RUN_COMPLETED,
                payload={"output": "done"},
            ),
        ]
        self.state = RuntimeState(
            run_id=request.run_id,
            status=RunStatus.COMPLETED,
            data={"output": "done"},
        )
        return self.state

    def stream(self, run_id: UUID, *, after_sequence: int = 0):
        async def iterate():
            for event in self.events:
                if event.run_id == run_id and event.sequence > after_sequence:
                    yield event
        return iterate()


class Resolver:
    def __init__(self, runtime: CompletingRuntime) -> None:
        self.runtime = runtime

    def resolve(self, run, definition):
        del run, definition
        return self.runtime


class OneMessageQueue:
    def __init__(self, delivery: RunQueueDelivery) -> None:
        self.delivery = delivery
        self.acknowledged: list[str] = []

    async def dequeue(self, *, consumer_name: str, block_ms: int):
        del consumer_name, block_ms
        delivery, self.delivery = self.delivery, None
        return delivery

    async def acknowledge(self, delivery_id: str) -> None:
        self.acknowledged.append(delivery_id)


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
async def test_worker_executes_run_and_persists_events_and_terminal_state(factory) -> None:
    run = Run.create(
        tenant_id=uuid4(), employee_id=uuid4(), employee_version=1,
        created_by=uuid4(), input_data={"task": "execute"},
    )
    command = RunCommand.create(
        run_id=run.id, tenant_id=run.tenant_id, action=RunCommandAction.START
    )
    version = EmployeeVersion(
        id=uuid4(), employee_id=run.employee_id, tenant_id=run.tenant_id,
        version=1, definition={"runtime_type": "workflow"},
        published_by=run.created_by, published_at=datetime.now(UTC),
    )
    async with factory() as session:
        await SqlAlchemyRunRepository(session).add(run)
        await SqlAlchemyEmployeeVersionRepository(session).add(version)
        await SqlAlchemyRunCommandRepository(session).add(command)
        await session.commit()
    delivery = RunQueueDelivery(
        delivery_id="1-0",
        message=RunQueueMessage(
            command_id=command.id, run_id=run.id, tenant_id=run.tenant_id,
            action="start",
        ),
    )
    queue = OneMessageQueue(delivery)

    worked = await RunWorker(
        session_factory=factory,
        queue=queue,
        runtime_resolver=Resolver(CompletingRuntime()),
        consumer_name="test-worker",
    ).run_once(block_ms=1)

    assert worked is True
    assert queue.acknowledged == ["1-0"]
    async with factory() as session:
        persisted = await SqlAlchemyRunRepository(session).get(
            tenant_id=run.tenant_id, run_id=run.id
        )
        assert persisted is not None and persisted.status is RunStatus.COMPLETED
        events = await SqlAlchemyRunEventRepository(session).list(
            run_id=run.id, after_sequence=0
        )
        assert [event.type for event in events] == [
            EventType.RUN_STARTED, EventType.RUN_COMPLETED
        ]
        assert await SqlAlchemyRunCommandRepository(session).is_processed(command.id)
