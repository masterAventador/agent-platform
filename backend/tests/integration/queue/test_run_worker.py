from collections.abc import AsyncIterator
from dataclasses import dataclass
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
from agent_platform.workers.run_worker import RuntimeNotPrepared, RunWorker


class CompletingRuntime:
    def __init__(self) -> None:
        self.events: list[PlatformEvent] = []
        self.state: RuntimeState | None = None
        self.requests: list[RuntimeStartRequest] = []
        self.messages: list[str] = []

    async def start(self, request: RuntimeStartRequest) -> RuntimeState:
        self.requests.append(request)
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

    async def send_message(self, run_id: UUID, message: str) -> None:
        self.messages.append(message)
        self.state = RuntimeState(
            run_id=run_id,
            status=RunStatus.COMPLETED,
            data={"output": message},
        )

    async def get_state(self, run_id: UUID) -> RuntimeState:
        assert self.state is not None
        assert self.state.run_id == run_id
        return self.state

    def stream(self, run_id: UUID, *, after_sequence: int = 0):
        async def iterate():
            for event in self.events:
                if event.run_id == run_id and event.sequence > after_sequence:
                    yield event
        return iterate()


@dataclass(frozen=True)
class Prepared:
    runtime: CompletingRuntime
    employee_definition: dict[str, object]


class Resolver:
    def __init__(
        self,
        runtime: CompletingRuntime,
        *,
        skill_paths: list[str] | None = None,
    ) -> None:
        self.runtime = runtime
        self.skill_paths = skill_paths or []
        self.calls: list[tuple[Run, dict[str, object]]] = []

    async def resolve(self, run: Run, definition: dict[str, object]) -> Prepared:
        self.calls.append((run, definition))
        return Prepared(
            runtime=self.runtime,
            employee_definition={**definition, "skill_paths": self.skill_paths},
        )


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


class MessageQueue:
    def __init__(self, deliveries: list[RunQueueDelivery]) -> None:
        self.deliveries = deliveries
        self.acknowledged: list[str] = []

    async def dequeue(self, *, consumer_name: str, block_ms: int):
        del consumer_name, block_ms
        return self.deliveries.pop(0) if self.deliveries else None

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


@pytest.mark.asyncio
async def test_worker_uses_trusted_run_identity_and_prepared_employee_capabilities(factory) -> None:
    tenant_id = uuid4()
    user_id = uuid4()
    tool_id = uuid4()
    skill_id = uuid4()
    untrusted_tool_id = uuid4()
    run = Run.create(
        tenant_id=tenant_id,
        employee_id=uuid4(),
        employee_version=3,
        created_by=user_id,
        input_data={
            "task": "execute",
            "tenant_id": str(uuid4()),
            "user_id": str(uuid4()),
            "tool_ids": [str(untrusted_tool_id)],
            "skill_paths": ["/host/attacker-controlled"],
        },
    )
    command = RunCommand.create(
        run_id=run.id,
        tenant_id=run.tenant_id,
        action=RunCommandAction.START,
    )
    stored_definition = {
        "work_mode": "autonomous",
        "tool_ids": [str(tool_id)],
        "skill_ids": [str(skill_id)],
    }
    version = EmployeeVersion(
        id=uuid4(),
        employee_id=run.employee_id,
        tenant_id=run.tenant_id,
        version=run.employee_version,
        definition=stored_definition,
        published_by=run.created_by,
        published_at=datetime.now(UTC),
    )
    async with factory() as session:
        await SqlAlchemyRunRepository(session).add(run)
        await SqlAlchemyEmployeeVersionRepository(session).add(version)
        await SqlAlchemyRunCommandRepository(session).add(command)
        await session.commit()

    runtime = CompletingRuntime()
    prepared_skill_paths = [f"/skills/{tenant_id}/{skill_id}/v2"]
    resolver = Resolver(runtime, skill_paths=prepared_skill_paths)
    queue = OneMessageQueue(
        RunQueueDelivery(
            delivery_id="trusted-start",
            message=RunQueueMessage(
                command_id=command.id,
                run_id=run.id,
                tenant_id=run.tenant_id,
                action="start",
            ),
        )
    )

    await RunWorker(
        session_factory=factory,
        queue=queue,
        runtime_resolver=resolver,
        consumer_name="test-worker",
    ).run_once(block_ms=1)

    assert resolver.calls == [(run, stored_definition)]
    assert len(runtime.requests) == 1
    request = runtime.requests[0]
    assert request.user_id == user_id
    assert request.tenant_id == tenant_id
    assert request.employee_definition["tool_ids"] == [str(tool_id)]
    assert request.employee_definition["skill_ids"] == [str(skill_id)]
    assert request.employee_definition["skill_paths"] == prepared_skill_paths
    assert request.input_data["tool_ids"] == [str(untrusted_tool_id)]


@pytest.mark.asyncio
async def test_control_command_reuses_the_runtime_prepared_for_the_run(factory) -> None:
    run = Run.create(
        tenant_id=uuid4(),
        employee_id=uuid4(),
        employee_version=1,
        created_by=uuid4(),
        input_data={"task": "start"},
    )
    start = RunCommand.create(
        run_id=run.id,
        tenant_id=run.tenant_id,
        action=RunCommandAction.START,
    )
    message = RunCommand.create(
        run_id=run.id,
        tenant_id=run.tenant_id,
        action=RunCommandAction.MESSAGE,
        payload={"message": "continue"},
    )
    version = EmployeeVersion(
        id=uuid4(),
        employee_id=run.employee_id,
        tenant_id=run.tenant_id,
        version=run.employee_version,
        definition={"work_mode": "autonomous", "tool_ids": [], "skill_ids": []},
        published_by=run.created_by,
        published_at=datetime.now(UTC),
    )
    async with factory() as session:
        await SqlAlchemyRunRepository(session).add(run)
        await SqlAlchemyEmployeeVersionRepository(session).add(version)
        await SqlAlchemyRunCommandRepository(session).add(start)
        await SqlAlchemyRunCommandRepository(session).add(message)
        await session.commit()

    runtime = CompletingRuntime()
    resolver = Resolver(runtime)
    queue = MessageQueue(
        [
            RunQueueDelivery(
                delivery_id="start",
                message=RunQueueMessage(
                    command_id=start.id,
                    run_id=run.id,
                    tenant_id=run.tenant_id,
                    action="start",
                ),
            ),
            RunQueueDelivery(
                delivery_id="message",
                message=RunQueueMessage(
                    command_id=message.id,
                    run_id=run.id,
                    tenant_id=run.tenant_id,
                    action="message",
                    payload={"message": "continue"},
                ),
            ),
        ]
    )
    worker = RunWorker(
        session_factory=factory,
        queue=queue,
        runtime_resolver=resolver,
        consumer_name="test-worker",
    )

    await worker.run_once(block_ms=1)
    await worker.run_once(block_ms=1)

    assert len(resolver.calls) == 1
    assert runtime.messages == ["continue"]
    assert queue.acknowledged == ["start", "message"]


@pytest.mark.asyncio
async def test_control_command_without_local_runtime_is_not_acknowledged(factory) -> None:
    run = Run.create(
        tenant_id=uuid4(),
        employee_id=uuid4(),
        employee_version=1,
        created_by=uuid4(),
        input_data={},
    )
    command = RunCommand.create(
        run_id=run.id,
        tenant_id=run.tenant_id,
        action=RunCommandAction.CANCEL,
    )
    version = EmployeeVersion(
        id=uuid4(),
        employee_id=run.employee_id,
        tenant_id=run.tenant_id,
        version=run.employee_version,
        definition={"work_mode": "autonomous", "tool_ids": [], "skill_ids": []},
        published_by=run.created_by,
        published_at=datetime.now(UTC),
    )
    async with factory() as session:
        await SqlAlchemyRunRepository(session).add(run)
        await SqlAlchemyEmployeeVersionRepository(session).add(version)
        await SqlAlchemyRunCommandRepository(session).add(command)
        await session.commit()
    queue = OneMessageQueue(
        RunQueueDelivery(
            delivery_id="missing-runtime",
            message=RunQueueMessage(
                command_id=command.id,
                run_id=run.id,
                tenant_id=run.tenant_id,
                action="cancel",
            ),
        )
    )

    with pytest.raises(RuntimeNotPrepared):
        await RunWorker(
            session_factory=factory,
            queue=queue,
            runtime_resolver=Resolver(CompletingRuntime()),
            consumer_name="test-worker",
        ).run_once(block_ms=1)

    assert queue.acknowledged == []
