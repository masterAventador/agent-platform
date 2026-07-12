import asyncio
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from redis.asyncio import Redis
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
from agent_platform.infrastructure.queue.redis_streams import (
    RedisRunQueue,
    RunQueueDelivery,
    RunQueueMessage,
)
from agent_platform.platform.employees.entities import EmployeeVersion
from agent_platform.platform.runs.commands import RunCommand, RunCommandAction
from agent_platform.platform.runs.entities import Run, RunStatus
from agent_platform.platform.runs.events import EventType, PlatformEvent
from agent_platform.runtimes.base import RuntimeStartRequest, RuntimeState
from agent_platform.workers.run_worker import (
    RuntimeAlreadyPrepared,
    RuntimeCleanupError,
    RuntimeNotPrepared,
    RunWorker,
)
from agent_platform.workers.runtime_composition import UntrustedRuntimeDefinition


class CompletingRuntime:
    def __init__(self) -> None:
        self.events: list[PlatformEvent] = []
        self.state: RuntimeState | None = None
        self.requests: list[RuntimeStartRequest] = []
        self.messages: list[str] = []
        self.stream_calls = 0

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
        self.stream_calls += 1

        async def iterate():
            for event in self.events:
                if event.run_id == run_id and event.sequence > after_sequence:
                    yield event

        return iterate()


class InteractiveRuntime(CompletingRuntime):
    async def start(self, request: RuntimeStartRequest) -> RuntimeState:
        completed = await super().start(request)
        self.events = self.events[:1]
        self.state = completed.model_copy(update={"status": RunStatus.RUNNING})
        return self.state


@dataclass
class Prepared:
    runtime: CompletingRuntime
    employee_definition: dict[str, object]
    close_calls: int = 0
    close_error: Exception | None = None
    renew_calls: int = 0
    renew_error: Exception | None = None

    async def close(self) -> None:
        self.close_calls += 1
        if self.close_error is not None:
            raise self.close_error

    async def renew(self) -> None:
        self.renew_calls += 1
        if self.renew_error is not None:
            raise self.renew_error


class Resolver:
    def __init__(
        self,
        runtime: CompletingRuntime,
        *,
        skill_paths: list[str] | None = None,
        close_error: Exception | None = None,
    ) -> None:
        self.runtime = runtime
        self.skill_paths = skill_paths or []
        self.calls: list[tuple[Run, dict[str, object]]] = []
        self.close_error = close_error
        self.prepared: Prepared | None = None

    async def resolve(self, run: Run, definition: dict[str, object]) -> Prepared:
        self.calls.append((run, definition))
        self.prepared = Prepared(
            runtime=self.runtime,
            employee_definition={**definition, "skill_paths": self.skill_paths},
            close_error=self.close_error,
        )
        return self.prepared


class PermanentFailingResolver:
    async def resolve(self, run: Run, definition: dict[str, object]) -> Prepared:
        del run, definition
        raise UntrustedRuntimeDefinition("secret definition detail")


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


class StartFailingRuntime(CompletingRuntime):
    async def start(self, request: RuntimeStartRequest) -> RuntimeState:
        del request
        raise ValueError("model invocation failed")


class StreamFailingRuntime(CompletingRuntime):
    def stream(self, run_id: UUID, *, after_sequence: int = 0):
        self.stream_calls += 1
        if self.stream_calls > 1:

            async def iterate_events():
                for event in self.events:
                    if event.run_id == run_id and event.sequence > after_sequence:
                        yield event

            return iterate_events()

        async def iterate():
            raise RuntimeError("transient event stream failure")
            yield

        return iterate()


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
        tenant_id=uuid4(),
        employee_id=uuid4(),
        employee_version=1,
        created_by=uuid4(),
        input_data={"task": "execute"},
    )
    command = RunCommand.create(
        run_id=run.id, tenant_id=run.tenant_id, action=RunCommandAction.START
    )
    version = EmployeeVersion(
        id=uuid4(),
        employee_id=run.employee_id,
        tenant_id=run.tenant_id,
        version=1,
        definition={"runtime_type": "workflow"},
        published_by=run.created_by,
        published_at=datetime.now(UTC),
    )
    async with factory() as session:
        await SqlAlchemyRunRepository(session).add(run)
        await SqlAlchemyEmployeeVersionRepository(session).add(version)
        await SqlAlchemyRunCommandRepository(session).add(command)
        await session.commit()
    delivery = RunQueueDelivery(
        delivery_id="1-0",
        message=RunQueueMessage(
            command_id=command.id,
            run_id=run.id,
            tenant_id=run.tenant_id,
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
        events = await SqlAlchemyRunEventRepository(session).list(run_id=run.id, after_sequence=0)
        assert [event.type for event in events] == [EventType.RUN_STARTED, EventType.RUN_COMPLETED]
        assert await SqlAlchemyRunCommandRepository(session).is_processed(command.id)


@pytest.mark.asyncio
async def test_permanent_preparation_failure_is_persisted_and_acknowledged(factory) -> None:
    run = Run.create(
        tenant_id=uuid4(),
        employee_id=uuid4(),
        employee_version=1,
        created_by=uuid4(),
        input_data={},
    )
    command = RunCommand.create(
        run_id=run.id, tenant_id=run.tenant_id, action=RunCommandAction.START
    )
    version = EmployeeVersion(
        id=uuid4(),
        employee_id=run.employee_id,
        tenant_id=run.tenant_id,
        version=1,
        definition={"work_mode": "workflow"},
        published_by=run.created_by,
        published_at=datetime.now(UTC),
    )
    async with factory() as session:
        await SqlAlchemyRunRepository(session).add(run)
        await SqlAlchemyEmployeeVersionRepository(session).add(version)
        await SqlAlchemyRunCommandRepository(session).add(command)
        await session.commit()
    delivery = RunQueueDelivery(
        delivery_id="permanent-1",
        message=RunQueueMessage(
            command_id=command.id,
            run_id=run.id,
            tenant_id=run.tenant_id,
            action="start",
        ),
    )
    queue = OneMessageQueue(delivery)

    worked = await RunWorker(
        session_factory=factory,
        queue=queue,
        runtime_resolver=PermanentFailingResolver(),
        consumer_name="test-worker",
    ).run_once(block_ms=1)

    assert worked is True
    assert queue.acknowledged == ["permanent-1"]
    async with factory() as session:
        persisted = await SqlAlchemyRunRepository(session).get(
            tenant_id=run.tenant_id, run_id=run.id
        )
        assert persisted is not None
        assert persisted.status is RunStatus.FAILED
        assert persisted.error_code == "invalid_runtime_definition"
        assert persisted.error_message is None
        events = await SqlAlchemyRunEventRepository(session).list(run_id=run.id, after_sequence=0)
        assert events[-1].payload == {"code": "invalid_runtime_definition"}
        assert "secret definition detail" not in repr(events)


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

    runtime = InteractiveRuntime()
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
async def test_renewal_failure_marks_running_run_failed_and_releases_environment(factory) -> None:
    run = Run.create(
        tenant_id=uuid4(),
        employee_id=uuid4(),
        employee_version=1,
        created_by=uuid4(),
        input_data={},
    )
    command = RunCommand.create(
        run_id=run.id, tenant_id=run.tenant_id, action=RunCommandAction.START
    )
    version = EmployeeVersion(
        id=uuid4(),
        employee_id=run.employee_id,
        tenant_id=run.tenant_id,
        version=1,
        definition={"work_mode": "autonomous"},
        published_by=run.created_by,
        published_at=datetime.now(UTC),
    )
    async with factory() as session:
        await SqlAlchemyRunRepository(session).add(run)
        await SqlAlchemyEmployeeVersionRepository(session).add(version)
        await SqlAlchemyRunCommandRepository(session).add(command)
        await session.commit()
    resolver = Resolver(InteractiveRuntime())
    worker = RunWorker(
        session_factory=factory,
        queue=OneMessageQueue(
            RunQueueDelivery(
                delivery_id="renew-start",
                message=RunQueueMessage(
                    command_id=command.id,
                    run_id=run.id,
                    tenant_id=run.tenant_id,
                    action="start",
                ),
            )
        ),
        runtime_resolver=resolver,
        consumer_name="test-worker",
    )
    await worker.run_once(block_ms=1)
    assert resolver.prepared is not None
    resolver.prepared.renew_error = RuntimeError("controller-secret")

    with pytest.raises(RuntimeCleanupError, match="renewal failed"):
        await worker.renew_active_runtimes()

    assert resolver.prepared.close_calls == 1
    async with factory() as session:
        persisted = await SqlAlchemyRunRepository(session).get(
            tenant_id=run.tenant_id, run_id=run.id
        )
        assert persisted is not None
        assert persisted.status is RunStatus.FAILED
        assert persisted.error_code == "sandbox_lease_renewal_failed"
        assert "controller-secret" not in repr(persisted)
        events = await SqlAlchemyRunEventRepository(session).list(run_id=run.id, after_sequence=0)
        assert events[-1].payload == {"code": "sandbox_lease_renewal_failed"}


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

    runtime = InteractiveRuntime()
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


@pytest.mark.asyncio
async def test_cancelled_before_start_is_idempotently_processed_without_runtime(factory) -> None:
    run = Run.create(
        tenant_id=uuid4(),
        employee_id=uuid4(),
        employee_version=1,
        created_by=uuid4(),
        input_data={},
    ).transition_to(RunStatus.CANCELLED)
    start = RunCommand.create(
        run_id=run.id,
        tenant_id=run.tenant_id,
        action=RunCommandAction.START,
    )
    cancel = RunCommand.create(
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
        await SqlAlchemyRunCommandRepository(session).add(start)
        await SqlAlchemyRunCommandRepository(session).add(cancel)
        await session.commit()
    queue = MessageQueue(
        [
            RunQueueDelivery(
                delivery_id="cancelled-start",
                message=RunQueueMessage(
                    command_id=start.id,
                    run_id=run.id,
                    tenant_id=run.tenant_id,
                    action="start",
                ),
            ),
            RunQueueDelivery(
                delivery_id="cancelled-control",
                message=RunQueueMessage(
                    command_id=cancel.id,
                    run_id=run.id,
                    tenant_id=run.tenant_id,
                    action="cancel",
                ),
            ),
        ]
    )
    resolver = Resolver(CompletingRuntime())
    worker = RunWorker(
        session_factory=factory,
        queue=queue,
        runtime_resolver=resolver,
        consumer_name="test-worker",
    )

    assert await worker.run_once(block_ms=1) is True
    assert await worker.run_once(block_ms=1) is True

    assert queue.acknowledged == ["cancelled-start", "cancelled-control"]
    assert resolver.calls == []
    async with factory() as session:
        persisted = await SqlAlchemyRunRepository(session).get(
            tenant_id=run.tenant_id,
            run_id=run.id,
        )
        commands = SqlAlchemyRunCommandRepository(session)
        assert persisted is not None and persisted.status is RunStatus.CANCELLED
        assert await commands.is_processed(start.id)
        assert await commands.is_processed(cancel.id)


@pytest.mark.asyncio
async def test_terminal_run_releases_prepared_environment(factory) -> None:
    run = Run.create(
        tenant_id=uuid4(),
        employee_id=uuid4(),
        employee_version=1,
        created_by=uuid4(),
        input_data={},
    )
    command = RunCommand.create(
        run_id=run.id, tenant_id=run.tenant_id, action=RunCommandAction.START
    )
    version = EmployeeVersion(
        id=uuid4(),
        employee_id=run.employee_id,
        tenant_id=run.tenant_id,
        version=1,
        definition={"work_mode": "autonomous"},
        published_by=run.created_by,
        published_at=datetime.now(UTC),
    )
    async with factory() as session:
        await SqlAlchemyRunRepository(session).add(run)
        await SqlAlchemyEmployeeVersionRepository(session).add(version)
        await SqlAlchemyRunCommandRepository(session).add(command)
        await session.commit()
    resolver = Resolver(CompletingRuntime())
    queue = OneMessageQueue(
        RunQueueDelivery(
            delivery_id="release-terminal",
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

    assert resolver.prepared is not None
    assert resolver.prepared.close_calls == 1


@pytest.mark.asyncio
async def test_terminal_cleanup_failure_is_sanitized_and_retried_after_persistence(
    factory,
) -> None:
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
        action=RunCommandAction.START,
    )
    version = EmployeeVersion(
        id=uuid4(),
        employee_id=run.employee_id,
        tenant_id=run.tenant_id,
        version=1,
        definition={"work_mode": "autonomous"},
        published_by=run.created_by,
        published_at=datetime.now(UTC),
    )
    async with factory() as session:
        await SqlAlchemyRunRepository(session).add(run)
        await SqlAlchemyEmployeeVersionRepository(session).add(version)
        await SqlAlchemyRunCommandRepository(session).add(command)
        await session.commit()
    deliveries = [
        RunQueueDelivery(
            delivery_id=delivery_id,
            message=RunQueueMessage(
                command_id=command.id,
                run_id=run.id,
                tenant_id=run.tenant_id,
                action="start",
            ),
        )
        for delivery_id in ("cleanup-failed", "cleanup-retry")
    ]
    queue = MessageQueue(deliveries)
    runtime = CompletingRuntime()
    cleanup_secret = "sandbox-provider-token-must-not-leak"
    resolver = Resolver(runtime, close_error=RuntimeError(cleanup_secret))
    worker = RunWorker(
        session_factory=factory,
        queue=queue,
        runtime_resolver=resolver,
        consumer_name="test-worker",
    )

    with pytest.raises(RuntimeCleanupError) as caught:
        await worker.run_once(block_ms=1)

    assert str(caught.value) == "Runtime environment cleanup failed"
    assert cleanup_secret not in repr(caught.value)
    assert resolver.prepared is not None
    assert resolver.prepared.close_calls == 1
    assert queue.acknowledged == []
    async with factory() as session:
        assert await SqlAlchemyRunCommandRepository(session).is_processed(command.id)

    resolver.prepared.close_error = None
    await worker.run_once(block_ms=1)

    assert resolver.prepared.close_calls == 2
    assert len(resolver.calls) == 1
    assert len(runtime.requests) == 1
    assert queue.acknowledged == ["cleanup-retry"]


@pytest.mark.asyncio
async def test_unexpected_start_error_is_persisted_as_sanitized_failure(factory) -> None:
    run = Run.create(
        tenant_id=uuid4(),
        employee_id=uuid4(),
        employee_version=1,
        created_by=uuid4(),
        input_data={},
    )
    command = RunCommand.create(
        run_id=run.id, tenant_id=run.tenant_id, action=RunCommandAction.START
    )
    version = EmployeeVersion(
        id=uuid4(),
        employee_id=run.employee_id,
        tenant_id=run.tenant_id,
        version=1,
        definition={"work_mode": "autonomous"},
        published_by=run.created_by,
        published_at=datetime.now(UTC),
    )
    async with factory() as session:
        await SqlAlchemyRunRepository(session).add(run)
        await SqlAlchemyEmployeeVersionRepository(session).add(version)
        await SqlAlchemyRunCommandRepository(session).add(command)
        await session.commit()
    resolver = Resolver(StartFailingRuntime())
    queue = OneMessageQueue(
        RunQueueDelivery(
            delivery_id="start-error",
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

    assert resolver.prepared is not None
    assert resolver.prepared.close_calls == 1
    assert queue.acknowledged == ["start-error"]
    async with factory() as session:
        persisted = await SqlAlchemyRunRepository(session).get(
            tenant_id=run.tenant_id,
            run_id=run.id,
        )
        assert persisted is not None
        assert persisted.status is RunStatus.FAILED
        assert persisted.error_code == "runtime_start_failed"
        assert "model invocation failed" not in repr(persisted)
        events = await SqlAlchemyRunEventRepository(session).list(
            run_id=run.id,
            after_sequence=0,
        )
        assert [event.type for event in events] == [
            EventType.RUN_STARTED,
            EventType.RUN_FAILED,
        ]
        assert "model invocation failed" not in repr(events)
        assert await SqlAlchemyRunCommandRepository(session).is_processed(command.id)


@pytest.mark.asyncio
async def test_terminal_start_retry_reuses_runtime_after_transient_event_collection_failure(
    factory,
) -> None:
    run = Run.create(
        tenant_id=uuid4(),
        employee_id=uuid4(),
        employee_version=1,
        created_by=uuid4(),
        input_data={},
    )
    command = RunCommand.create(
        run_id=run.id, tenant_id=run.tenant_id, action=RunCommandAction.START
    )
    version = EmployeeVersion(
        id=uuid4(),
        employee_id=run.employee_id,
        tenant_id=run.tenant_id,
        version=1,
        definition={"work_mode": "autonomous"},
        published_by=run.created_by,
        published_at=datetime.now(UTC),
    )
    async with factory() as session:
        await SqlAlchemyRunRepository(session).add(run)
        await SqlAlchemyEmployeeVersionRepository(session).add(version)
        await SqlAlchemyRunCommandRepository(session).add(command)
        await session.commit()
    resolver = Resolver(StreamFailingRuntime())
    queue = MessageQueue(
        [
            RunQueueDelivery(
                delivery_id=delivery_id,
                message=RunQueueMessage(
                    command_id=command.id,
                    run_id=run.id,
                    tenant_id=run.tenant_id,
                    action="start",
                ),
            )
            for delivery_id in ("stream-error", "stream-retry")
        ]
    )
    worker = RunWorker(
        session_factory=factory,
        queue=queue,
        runtime_resolver=resolver,
        consumer_name="test-worker",
    )

    with pytest.raises(RuntimeError, match="transient event stream failure"):
        await worker.run_once(block_ms=1)

    assert resolver.prepared is not None
    assert resolver.prepared.close_calls == 0
    assert queue.acknowledged == []

    await worker.run_once(block_ms=1)

    assert len(resolver.calls) == 1
    assert len(resolver.runtime.requests) == 1
    assert resolver.runtime.stream_calls == 2
    assert resolver.prepared.close_calls == 1
    assert queue.acknowledged == ["stream-retry"]


@pytest.mark.asyncio
async def test_terminal_start_retry_reuses_cached_runtime_state_and_history_after_db_failure(
    factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = Run.create(
        tenant_id=uuid4(),
        employee_id=uuid4(),
        employee_version=1,
        created_by=uuid4(),
        input_data={"task": "single-side-effect"},
    )
    command = RunCommand.create(
        run_id=run.id,
        tenant_id=run.tenant_id,
        action=RunCommandAction.START,
    )
    version = EmployeeVersion(
        id=uuid4(),
        employee_id=run.employee_id,
        tenant_id=run.tenant_id,
        version=1,
        definition={"work_mode": "autonomous"},
        published_by=run.created_by,
        published_at=datetime.now(UTC),
    )
    async with factory() as session:
        await SqlAlchemyRunRepository(session).add(run)
        await SqlAlchemyEmployeeVersionRepository(session).add(version)
        await SqlAlchemyRunCommandRepository(session).add(command)
        await session.commit()

    original_mark_processed = SqlAlchemyRunCommandRepository.mark_processed
    persistence_attempts = 0
    database_secret = "database-password-must-not-be-logged"

    async def fail_first_persistence(
        repository: SqlAlchemyRunCommandRepository,
        command_id: UUID,
    ) -> None:
        nonlocal persistence_attempts
        persistence_attempts += 1
        if persistence_attempts == 1:
            raise RuntimeError(database_secret)
        await original_mark_processed(repository, command_id)

    monkeypatch.setattr(
        SqlAlchemyRunCommandRepository,
        "mark_processed",
        fail_first_persistence,
    )
    deliveries = [
        RunQueueDelivery(
            delivery_id=delivery_id,
            message=RunQueueMessage(
                command_id=command.id,
                run_id=run.id,
                tenant_id=run.tenant_id,
                action="start",
            ),
        )
        for delivery_id in ("first-attempt", "retry-attempt")
    ]
    queue = MessageQueue(deliveries)
    runtime = CompletingRuntime()
    resolver = Resolver(runtime)
    worker = RunWorker(
        session_factory=factory,
        queue=queue,
        runtime_resolver=resolver,
        consumer_name="test-worker",
    )

    with pytest.raises(RuntimeError, match=database_secret):
        await worker.run_once(block_ms=1)

    assert resolver.prepared is not None
    assert resolver.prepared.close_calls == 0
    assert queue.acknowledged == []
    async with factory() as session:
        after_failure = await SqlAlchemyRunRepository(session).get(
            tenant_id=run.tenant_id,
            run_id=run.id,
        )
        assert after_failure is not None
        assert after_failure.status is RunStatus.RUNNING
        assert not await SqlAlchemyRunCommandRepository(session).is_processed(command.id)

    await worker.run_once(block_ms=1)

    assert persistence_attempts == 2
    assert len(resolver.calls) == 1
    assert len(runtime.requests) == 1
    assert runtime.stream_calls == 1
    assert resolver.prepared.close_calls == 1
    assert queue.acknowledged == ["retry-attempt"]
    async with factory() as session:
        persisted = await SqlAlchemyRunRepository(session).get(
            tenant_id=run.tenant_id,
            run_id=run.id,
        )
        assert persisted is not None
        assert persisted.status is RunStatus.COMPLETED
        events = await SqlAlchemyRunEventRepository(session).list(
            run_id=run.id,
            after_sequence=0,
        )
        assert [event.type for event in events] == [
            EventType.RUN_STARTED,
            EventType.RUN_COMPLETED,
        ]
        assert await SqlAlchemyRunCommandRepository(session).is_processed(command.id)


@pytest.mark.asyncio
async def test_real_redis_claim_retries_pending_terminal_result_without_restarting_runtime(
    factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis_url = os.getenv("TEST_REDIS_URL")
    if redis_url is None:
        pytest.skip("需要 TEST_REDIS_URL 才运行真实 Redis pending 重试测试")
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
        action=RunCommandAction.START,
    )
    version = EmployeeVersion(
        id=uuid4(),
        employee_id=run.employee_id,
        tenant_id=run.tenant_id,
        version=1,
        definition={"work_mode": "autonomous"},
        published_by=run.created_by,
        published_at=datetime.now(UTC),
    )
    async with factory() as session:
        await SqlAlchemyRunRepository(session).add(run)
        await SqlAlchemyEmployeeVersionRepository(session).add(version)
        await SqlAlchemyRunCommandRepository(session).add(command)
        await session.commit()

    original_mark_processed = SqlAlchemyRunCommandRepository.mark_processed
    failed = False

    async def fail_once(
        repository: SqlAlchemyRunCommandRepository,
        command_id: UUID,
    ) -> None:
        nonlocal failed
        if not failed:
            failed = True
            raise RuntimeError("postgres-secret-must-not-leak")
        await original_mark_processed(repository, command_id)

    monkeypatch.setattr(SqlAlchemyRunCommandRepository, "mark_processed", fail_once)
    redis = Redis.from_url(redis_url, decode_responses=True)
    stream_name = f"test:worker-retry:{uuid4()}"
    queue = RedisRunQueue(
        redis,
        stream_name=stream_name,
        group_name="test-workers",
        pending_min_idle_ms=1,
    )
    await queue.setup()
    await queue.enqueue(
        RunQueueMessage(
            command_id=command.id,
            run_id=run.id,
            tenant_id=run.tenant_id,
            action="start",
        )
    )
    runtime = CompletingRuntime()
    resolver = Resolver(runtime)
    worker = RunWorker(
        session_factory=factory,
        queue=queue,
        runtime_resolver=resolver,
        consumer_name="worker-retry",
    )
    try:
        with pytest.raises(RuntimeError, match="postgres-secret-must-not-leak"):
            await worker.run_once(block_ms=100)
        await asyncio.sleep(0.01)

        assert await worker.run_once(block_ms=100) is True

        assert len(resolver.calls) == 1
        assert len(runtime.requests) == 1
        assert runtime.stream_calls == 1
        assert resolver.prepared is not None
        assert resolver.prepared.close_calls == 1
        pending = await redis.xpending(stream_name, "test-workers")
        assert pending["pending"] == 0
    finally:
        await redis.delete(stream_name)
        await redis.aclose()


@pytest.mark.asyncio
async def test_duplicate_start_does_not_overwrite_live_environment(factory) -> None:
    run = Run.create(
        tenant_id=uuid4(),
        employee_id=uuid4(),
        employee_version=1,
        created_by=uuid4(),
        input_data={},
    )
    first = RunCommand.create(run_id=run.id, tenant_id=run.tenant_id, action=RunCommandAction.START)
    duplicate = RunCommand.create(
        run_id=run.id, tenant_id=run.tenant_id, action=RunCommandAction.START
    )
    version = EmployeeVersion(
        id=uuid4(),
        employee_id=run.employee_id,
        tenant_id=run.tenant_id,
        version=1,
        definition={"work_mode": "autonomous"},
        published_by=run.created_by,
        published_at=datetime.now(UTC),
    )
    async with factory() as session:
        await SqlAlchemyRunRepository(session).add(run)
        await SqlAlchemyEmployeeVersionRepository(session).add(version)
        await SqlAlchemyRunCommandRepository(session).add(first)
        await SqlAlchemyRunCommandRepository(session).add(duplicate)
        await session.commit()
    queue = MessageQueue(
        [
            RunQueueDelivery(
                delivery_id="first-start",
                message=RunQueueMessage(
                    command_id=first.id,
                    run_id=run.id,
                    tenant_id=run.tenant_id,
                    action="start",
                ),
            ),
            RunQueueDelivery(
                delivery_id="duplicate-start",
                message=RunQueueMessage(
                    command_id=duplicate.id,
                    run_id=run.id,
                    tenant_id=run.tenant_id,
                    action="start",
                ),
            ),
        ]
    )
    resolver = Resolver(InteractiveRuntime())
    worker = RunWorker(
        session_factory=factory,
        queue=queue,
        runtime_resolver=resolver,
        consumer_name="test-worker",
    )

    await worker.run_once(block_ms=1)
    with pytest.raises(RuntimeAlreadyPrepared):
        await worker.run_once(block_ms=1)

    assert len(resolver.calls) == 1
    assert resolver.prepared is not None
    assert resolver.prepared.close_calls == 0
    await worker.aclose()
    assert resolver.prepared.close_calls == 1
    assert queue.acknowledged == ["first-start"]
