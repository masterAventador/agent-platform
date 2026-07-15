import asyncio
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from pydantic import JsonValue
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_platform.infrastructure.database.base import Base
from agent_platform.infrastructure.database.models import load_database_models
from agent_platform.infrastructure.database.repositories.audit import (
    SqlAlchemyToolAuditSink,
)
from agent_platform.infrastructure.database.repositories.dead_letters import RunDeadLetterRecord
from agent_platform.infrastructure.database.repositories.employees import (
    SqlAlchemyEmployeeVersionRepository,
)
from agent_platform.infrastructure.database.repositories.runs import (
    SqlAlchemyRunCommandRepository,
    SqlAlchemyRunEventRepository,
    SqlAlchemyRunRepository,
)
from agent_platform.infrastructure.database.repositories.runtime_ownership import (
    RuntimeOwnershipBusy,
    RuntimeOwnershipLost,
    SqlAlchemyRuntimeOwnershipRepository,
)
from agent_platform.infrastructure.queue.redis_streams import (
    MalformedRunQueueMessage,
    RedisRunQueue,
    RunQueueDelivery,
    RunQueueMessage,
)
from agent_platform.platform.employees.entities import EmployeeVersion
from agent_platform.platform.runs.commands import RunCommand, RunCommandAction
from agent_platform.platform.runs.entities import Run, RunStatus
from agent_platform.platform.runs.events import EventType, PlatformEvent
from agent_platform.platform.tool_gateway import (
    ArgumentSummary,
    AuditEventType,
    ToolAuditEvent,
)
from agent_platform.runtimes.base import RuntimeStartRequest, RuntimeState
from agent_platform.runtimes.recovery import (
    RuntimeControlMismatch,
    RuntimeRecoveryTransient,
    RuntimeRecoveryUnavailable,
)
from agent_platform.workers import run_worker as run_worker_module
from agent_platform.workers.run_worker import (
    RuntimeAlreadyPrepared,
    RuntimeCleanupError,
    RuntimeNotPrepared,
    RunWorker,
    WorkerFenced,
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


class RestorableRuntime(CompletingRuntime):
    def __init__(self) -> None:
        super().__init__()
        self.recovered: list[tuple[RuntimeStartRequest, RunStatus]] = []

    async def recover(
        self,
        request: RuntimeStartRequest,
        status: RunStatus,
    ) -> RuntimeState:
        self.recovered.append((request, status))
        self.requests.append(request)
        self.events = []
        self.state = RuntimeState(run_id=request.run_id, status=status, data={})
        return self.state


class UnavailableRuntime(CompletingRuntime):
    async def recover(
        self,
        request: RuntimeStartRequest,
        status: RunStatus,
    ) -> RuntimeState:
        del request, status
        raise RuntimeRecoveryUnavailable


class TransientRecoverRuntime(RestorableRuntime):
    def __init__(self) -> None:
        super().__init__()
        self.recovery_attempts = 0

    async def recover(
        self,
        request: RuntimeStartRequest,
        status: RunStatus,
    ) -> RuntimeState:
        self.recovery_attempts += 1
        if self.recovery_attempts == 1:
            raise ConnectionError("checkpoint-password-must-not-leak")
        return await super().recover(request, status)


class ApprovalRecoverRuntime(RestorableRuntime):
    def __init__(self, approval_id: UUID) -> None:
        super().__init__()
        self.approval_id = approval_id
        self.approvals: list[tuple[UUID, UUID]] = []
        self.rejections: list[tuple[UUID, UUID]] = []

    async def approve(self, run_id: UUID, approval_id: UUID) -> None:
        self.approvals.append((run_id, approval_id))

    async def reject(
        self,
        run_id: UUID,
        approval_id: UUID,
        reason: str | None = None,
    ) -> None:
        del reason
        if approval_id != self.approval_id:
            raise RuntimeControlMismatch
        self.rejections.append((run_id, approval_id))
        request = self.requests[-1]
        self.events.append(
            PlatformEvent.create(
                tenant_id=request.tenant_id,
                employee_id=request.employee_id,
                run_id=request.run_id,
                sequence=len(self.events) + 1,
                event_type=EventType.RUN_CANCELLED,
                payload={"status": "cancelled"},
            )
        )
        self.state = RuntimeState(run_id=run_id, status=RunStatus.CANCELLED, data={})

    async def cancel(self, run_id: UUID) -> None:
        request = self.requests[-1]
        self.events.append(
            PlatformEvent.create(
                tenant_id=request.tenant_id,
                employee_id=request.employee_id,
                run_id=request.run_id,
                sequence=len(self.events) + 1,
                event_type=EventType.RUN_CANCELLED,
                payload={"status": "cancelled"},
            )
        )
        self.state = RuntimeState(run_id=run_id, status=RunStatus.CANCELLED, data={})

    def pending_approval_id(self, run_id: UUID) -> UUID | None:
        del run_id
        return self.approval_id

    async def recover(
        self,
        request: RuntimeStartRequest,
        status: RunStatus,
    ) -> RuntimeState:
        state = await super().recover(request, status)
        self.events = [
            PlatformEvent.create(
                tenant_id=request.tenant_id,
                employee_id=request.employee_id,
                run_id=request.run_id,
                sequence=1,
                event_type=EventType.APPROVAL_REQUIRED,
                payload={"approval_id": str(self.approval_id)},
            )
        ]
        return state


class CompletedRecoverRuntime(RestorableRuntime):
    async def recover(
        self,
        request: RuntimeStartRequest,
        status: RunStatus,
    ) -> RuntimeState:
        del status
        self.requests.append(request)
        self.events = [
            PlatformEvent.create(
                tenant_id=request.tenant_id,
                employee_id=request.employee_id,
                run_id=request.run_id,
                sequence=1,
                event_type=EventType.RUN_COMPLETED,
                payload={"status": "completed"},
            )
        ]
        self.state = RuntimeState(
            run_id=request.run_id,
            status=RunStatus.COMPLETED,
            data={"output": "checkpoint result"},
        )
        return self.state


@dataclass
class Prepared:
    runtime: CompletingRuntime
    employee_definition: dict[str, object]
    close_calls: int = 0
    close_error: Exception | None = None
    renew_calls: int = 0
    renew_error: Exception | None = None
    detach_calls: int = 0
    detach_error: Exception | None = None

    async def close(self) -> None:
        self.close_calls += 1
        if self.close_error is not None:
            raise self.close_error

    async def renew(self) -> None:
        self.renew_calls += 1
        if self.renew_error is not None:
            raise self.renew_error

    async def detach(self) -> None:
        self.detach_calls += 1
        if self.detach_error is not None:
            raise self.detach_error


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
        self.prepareds: list[Prepared] = []

    async def resolve(self, run: Run, definition: dict[str, object]) -> Prepared:
        self.calls.append((run, definition))
        self.prepared = Prepared(
            runtime=self.runtime,
            employee_definition={**definition, "skill_paths": self.skill_paths},
            close_error=self.close_error,
        )
        self.prepareds.append(self.prepared)
        return self.prepared


class RecoveringResolver(Resolver):
    def __init__(self, runtime: CompletingRuntime) -> None:
        super().__init__(runtime)
        self.recovery_calls: list[tuple[Run, dict[str, object]]] = []

    async def recover(self, run: Run, definition: dict[str, object]) -> Prepared:
        self.recovery_calls.append((run, definition))
        self.prepared = Prepared(runtime=self.runtime, employee_definition=definition)
        return self.prepared


class DetachFailingRecoveringResolver(RecoveringResolver):
    async def recover(self, run: Run, definition: dict[str, object]) -> Prepared:
        prepared = await super().recover(run, definition)
        prepared.detach_error = RuntimeError("detach failure")
        return prepared


class UnrecoverableResolver(Resolver):
    async def recover(self, run: Run, definition: dict[str, object]) -> Prepared:
        del run, definition
        raise RuntimeRecoveryUnavailable


class PermanentFailingResolver:
    async def resolve(self, run: Run, definition: dict[str, object]) -> Prepared:
        del run, definition
        raise UntrustedRuntimeDefinition("secret definition detail")


class TransientFailingResolver:
    async def resolve(self, run: Run, definition: dict[str, object]) -> Prepared:
        del run, definition
        raise RuntimeError("temporary resolver failure")


class OneMessageQueue:
    def __init__(self, delivery: RunQueueDelivery) -> None:
        self.delivery = delivery
        self.acknowledged: list[str] = []
        self.dead_lettered: list[tuple[str, str]] = []

    async def dequeue(self, *, consumer_name: str, block_ms: int):
        del consumer_name, block_ms
        delivery, self.delivery = self.delivery, None
        return delivery

    async def acknowledge(self, delivery_id: str) -> None:
        self.acknowledged.append(delivery_id)

    async def dead_letter_if_exhausted(
        self,
        delivery: RunQueueDelivery,
        *,
        error_type: str,
    ) -> bool:
        self.dead_lettered.append((delivery.delivery_id, error_type))
        return False

    async def exhausted_delivery_attempts(self, delivery_id: str) -> int | None:
        del delivery_id
        return None


class MessageQueue:
    def __init__(self, deliveries: list[RunQueueDelivery]) -> None:
        self.deliveries = deliveries
        self.acknowledged: list[str] = []
        self.dead_lettered: list[tuple[str, str]] = []

    async def dequeue(self, *, consumer_name: str, block_ms: int):
        del consumer_name, block_ms
        return self.deliveries.pop(0) if self.deliveries else None

    async def acknowledge(self, delivery_id: str) -> None:
        self.acknowledged.append(delivery_id)

    async def dead_letter_if_exhausted(
        self,
        delivery: RunQueueDelivery,
        *,
        error_type: str,
    ) -> bool:
        self.dead_lettered.append((delivery.delivery_id, error_type))
        return False

    async def exhausted_delivery_attempts(self, delivery_id: str) -> int | None:
        del delivery_id
        return None


class ExhaustingQueue(OneMessageQueue):
    async def dead_letter_if_exhausted(
        self,
        delivery: RunQueueDelivery,
        *,
        error_type: str,
    ) -> bool:
        self.dead_lettered.append((delivery.delivery_id, error_type))
        return True

    async def exhausted_delivery_attempts(self, delivery_id: str) -> int | None:
        del delivery_id
        return 5


class AckFailingExhaustingQueue(MessageQueue):
    def __init__(self, deliveries: list[RunQueueDelivery]) -> None:
        super().__init__(deliveries)
        self.ack_calls = 0

    async def exhausted_delivery_attempts(self, delivery_id: str) -> int | None:
        del delivery_id
        return 5

    async def acknowledge(self, delivery_id: str) -> None:
        self.ack_calls += 1
        if self.ack_calls == 1:
            raise RuntimeError("redis-ack-unavailable")
        await super().acknowledge(delivery_id)


class MalformedQueue:
    def __init__(self) -> None:
        self.acknowledged: list[str] = []

    async def dequeue(self, *, consumer_name: str, block_ms: int):
        del consumer_name, block_ms
        raise MalformedRunQueueMessage(
            delivery_id="malformed-1",
            attempts=5,
            exhausted=True,
            raw_fields={"payload": "database-password-must-not-persist"},
        )

    async def acknowledge(self, delivery_id: str) -> None:
        self.acknowledged.append(delivery_id)

    async def publish_dead_letter(self, record) -> None:
        del record


class ActiveThenMalformedQueue(MessageQueue):
    def __init__(
        self,
        start_delivery: RunQueueDelivery,
        *,
        raw_fields: dict[str, str],
    ) -> None:
        super().__init__([start_delivery])
        self.raw_fields = raw_fields
        self.malformed_delivered = False

    async def dequeue(self, *, consumer_name: str, block_ms: int):
        if self.deliveries:
            return await super().dequeue(consumer_name=consumer_name, block_ms=block_ms)
        if not self.malformed_delivered:
            self.malformed_delivered = True
            raise MalformedRunQueueMessage(
                delivery_id="active-malformed",
                attempts=5,
                exhausted=True,
                raw_fields=self.raw_fields,
            )
        return None

    async def publish_dead_letter(self, record) -> None:
        del record


class FailingDeadLetterService:
    async def record_failure(self, delivery, *, attempts: int, error_type: str, ownership=None):
        del delivery, attempts, error_type, ownership
        raise RuntimeError("postgres-dlq-unavailable")

    async def reconcile_mirrors(self, *, publisher, limit: int) -> int:
        del publisher, limit
        return 0


class StartFailingRuntime(CompletingRuntime):
    async def start(self, request: RuntimeStartRequest) -> RuntimeState:
        del request
        raise ValueError("model invocation failed")


class PreStartCountingRuntime(CompletingRuntime):
    def __init__(self) -> None:
        super().__init__()
        self.cancel_calls = 0

    async def cancel(self, run_id: UUID) -> None:
        del run_id
        self.cancel_calls += 1
        raise RuntimeError("runtime is not initialized")


class BlockingCancellableRuntime(CompletingRuntime):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.stopped = asyncio.Event()
        self.side_effects = 0
        self._start_task: asyncio.Task[RuntimeState] | None = None

    async def start(self, request: RuntimeStartRequest) -> RuntimeState:
        self.requests.append(request)
        self._start_task = asyncio.current_task()
        self.events = [
            PlatformEvent.create(
                tenant_id=request.tenant_id,
                employee_id=request.employee_id,
                run_id=request.run_id,
                sequence=1,
                event_type=EventType.RUN_STARTED,
                payload={},
            )
        ]
        self.state = RuntimeState(
            run_id=request.run_id,
            status=RunStatus.RUNNING,
            data={},
        )
        self.started.set()
        try:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                assert self.state is not None
                return self.state
            self.side_effects += 1
            raise AssertionError("cancelled runtime must not continue")
        finally:
            self.stopped.set()

    async def cancel(self, run_id: UUID) -> None:
        request = self.requests[0]
        assert run_id == request.run_id
        if not any(event.type is EventType.RUN_CANCELLED for event in self.events):
            self.events.append(
                PlatformEvent.create(
                    tenant_id=request.tenant_id,
                    employee_id=request.employee_id,
                    run_id=request.run_id,
                    sequence=len(self.events) + 1,
                    event_type=EventType.RUN_CANCELLED,
                    payload={"status": "cancelled"},
                )
            )
        self.state = RuntimeState(
            run_id=run_id,
            status=RunStatus.CANCELLED,
            data={},
        )
        if self._start_task is not None:
            self._start_task.cancel()


class BlockingControlRuntime(CompletingRuntime):
    def __init__(self, run_id: UUID) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.stopped = asyncio.Event()
        self.cancel_calls = 0
        self.control_side_effects = 0
        self.state = RuntimeState(run_id=run_id, status=RunStatus.RUNNING, data={})
        self._control_task: asyncio.Task[None] | None = None

    async def _block(self) -> None:
        self._control_task = asyncio.current_task()
        self.started.set()
        try:
            await asyncio.Event().wait()
            self.control_side_effects += 1
        except asyncio.CancelledError:
            return
        finally:
            self.stopped.set()

    async def send_message(self, run_id: UUID, message: str) -> None:
        del run_id, message
        await self._block()

    async def resume(self, run_id: UUID) -> None:
        del run_id
        await self._block()

    async def approve(self, run_id: UUID, approval_id: UUID) -> None:
        del run_id, approval_id
        await self._block()

    async def reject(
        self,
        run_id: UUID,
        approval_id: UUID,
        reason: str | None = None,
    ) -> None:
        del run_id, approval_id, reason
        await self._block()

    async def cancel(self, run_id: UUID) -> None:
        self.cancel_calls += 1
        self.state = RuntimeState(run_id=run_id, status=RunStatus.CANCELLED, data={})
        if self.requests and not any(
            event.type is EventType.RUN_CANCELLED for event in self.events
        ):
            request = self.requests[-1]
            self.events.append(
                PlatformEvent.create(
                    tenant_id=request.tenant_id,
                    employee_id=request.employee_id,
                    run_id=run_id,
                    sequence=len(self.events) + 1,
                    event_type=EventType.RUN_CANCELLED,
                    payload={"status": "cancelled"},
                )
            )
        if self._control_task is not None:
            self._control_task.cancel()


class WaitingBlockingControlRuntime(BlockingControlRuntime):
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
            )
        ]
        self.state = RuntimeState(
            run_id=request.run_id,
            status=RunStatus.WAITING_FOR_INPUT,
            data={},
        )
        return self.state


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


def test_worker_uses_a_bounded_random_process_owner_id(factory) -> None:
    worker = RunWorker(
        session_factory=factory,
        queue=MessageQueue([]),
        runtime_resolver=Resolver(CompletingRuntime()),
        consumer_name="consumer-" + ("x" * 500),
    )

    assert UUID(worker._owner_id)
    assert len(worker._owner_id) <= 200


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
async def test_worker_observes_cancel_intent_during_blocking_start_and_stops_runtime(
    factory,
) -> None:
    run = Run.create(
        tenant_id=uuid4(),
        employee_id=uuid4(),
        employee_version=1,
        created_by=uuid4(),
        input_data={"task": "block"},
    )
    start = RunCommand.create(
        run_id=run.id,
        tenant_id=run.tenant_id,
        action=RunCommandAction.START,
    )
    version = EmployeeVersion(
        id=uuid4(),
        employee_id=run.employee_id,
        tenant_id=run.tenant_id,
        version=run.employee_version,
        definition={"work_mode": "autonomous"},
        published_by=run.created_by,
        published_at=datetime.now(UTC),
    )
    async with factory() as session:
        await SqlAlchemyRunRepository(session).add(run)
        await SqlAlchemyEmployeeVersionRepository(session).add(version)
        await SqlAlchemyRunCommandRepository(session).add(start)
        await session.commit()

    runtime = BlockingCancellableRuntime()
    queue = MessageQueue(
        [
            RunQueueDelivery(
                delivery_id="blocking-start",
                message=RunQueueMessage(
                    command_id=start.id,
                    run_id=run.id,
                    tenant_id=run.tenant_id,
                    action="start",
                ),
            )
        ]
    )
    worker = RunWorker(
        session_factory=factory,
        queue=queue,
        runtime_resolver=Resolver(runtime),
        consumer_name="cancel-aware-worker",
    )
    start_delivery_task = asyncio.create_task(worker.run_once(block_ms=1))
    await asyncio.wait_for(runtime.started.wait(), timeout=1)

    cancel = RunCommand.create(
        run_id=run.id,
        tenant_id=run.tenant_id,
        action=RunCommandAction.CANCEL,
    )
    async with factory() as session:
        await SqlAlchemyRunCommandRepository(session).add(cancel)
        await session.commit()
    queue.deliveries.append(
        RunQueueDelivery(
            delivery_id="blocking-cancel",
            message=RunQueueMessage(
                command_id=cancel.id,
                run_id=run.id,
                tenant_id=run.tenant_id,
                action="cancel",
            ),
        )
    )

    assert await asyncio.wait_for(start_delivery_task, timeout=1) is True
    assert await worker.run_once(block_ms=1) is True

    assert runtime.side_effects == 0
    assert queue.acknowledged == ["blocking-start", "blocking-cancel"]
    async with factory() as session:
        persisted = await SqlAlchemyRunRepository(session).get(
            tenant_id=run.tenant_id,
            run_id=run.id,
        )
        events = await SqlAlchemyRunEventRepository(session).list(
            run_id=run.id,
            after_sequence=0,
        )
        commands = SqlAlchemyRunCommandRepository(session)
        assert await commands.is_processed(start.id)
        assert await commands.is_processed(cancel.id)
    assert persisted is not None and persisted.status is RunStatus.CANCELLED
    assert [event.type for event in events].count(EventType.RUN_CANCELLED) == 1
    assert EventType.RUN_COMPLETED not in [event.type for event in events]


@pytest.mark.asyncio
async def test_cancel_committed_before_start_never_calls_uninitialized_runtime(factory) -> None:
    run = Run.create(
        tenant_id=uuid4(),
        employee_id=uuid4(),
        employee_version=1,
        created_by=uuid4(),
        input_data={},
    )
    version = EmployeeVersion(
        id=uuid4(),
        employee_id=run.employee_id,
        tenant_id=run.tenant_id,
        version=run.employee_version,
        definition={"work_mode": "autonomous"},
        published_by=run.created_by,
        published_at=datetime.now(UTC),
    )
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
    async with factory() as session:
        await SqlAlchemyRunRepository(session).add(run)
        await SqlAlchemyEmployeeVersionRepository(session).add(version)
        await SqlAlchemyRunCommandRepository(session).add(start)
        await SqlAlchemyRunCommandRepository(session).add(cancel)
        await session.commit()
    runtime = PreStartCountingRuntime()
    resolver = Resolver(runtime)
    queue = OneMessageQueue(
        RunQueueDelivery(
            delivery_id="cancel-before-start",
            message=RunQueueMessage(
                command_id=start.id,
                run_id=run.id,
                tenant_id=run.tenant_id,
                action="start",
            ),
        )
    )
    worker = RunWorker(
        session_factory=factory,
        queue=queue,
        runtime_resolver=resolver,
        consumer_name="cancel-before-start",
    )

    assert await worker.run_once(block_ms=1)

    async with factory() as session:
        persisted = await SqlAlchemyRunRepository(session).get(
            tenant_id=run.tenant_id,
            run_id=run.id,
        )
        events = await SqlAlchemyRunEventRepository(session).list(
            run_id=run.id,
            after_sequence=0,
        )
        commands = SqlAlchemyRunCommandRepository(session)
        ownership = await SqlAlchemyRuntimeOwnershipRepository(session).get(run_id=run.id)
        assert await commands.is_processed(start.id)
        assert await commands.is_processed(cancel.id)
    assert persisted is not None and persisted.status is RunStatus.CANCELLED
    assert resolver.calls == []
    assert resolver.prepared is None
    assert runtime.requests == []
    assert runtime.cancel_calls == 0
    assert [event.type for event in events] == [EventType.RUN_CANCELLED]
    assert ownership is not None and ownership.owner_id is None
    assert queue.acknowledged == ["cancel-before-start"]


@pytest.mark.asyncio
async def test_start_monitor_database_failure_cancels_and_awaits_runtime_child(
    factory,
) -> None:
    run = Run.create(
        tenant_id=uuid4(),
        employee_id=uuid4(),
        employee_version=1,
        created_by=uuid4(),
        input_data={},
    )
    runtime = BlockingCancellableRuntime()
    worker = RunWorker(
        session_factory=factory,
        queue=MessageQueue([]),
        runtime_resolver=Resolver(runtime),
        consumer_name="structured-child-cleanup",
    )

    checks = 0

    async def fail_after_start(run_id: UUID) -> list[RunCommand]:
        nonlocal checks
        assert run_id == run.id
        checks += 1
        if checks == 1:
            return []
        await runtime.started.wait()
        raise RuntimeError("database unavailable")

    worker._pending_cancel_commands = fail_after_start  # type: ignore[method-assign]
    request = RuntimeStartRequest(
        run_id=run.id,
        tenant_id=run.tenant_id,
        user_id=run.created_by,
        employee_id=run.employee_id,
        thread_id=run.thread_id,
        employee_definition={},
        input_data=run.input_data,
    )

    with pytest.raises(RuntimeError, match="database unavailable"):
        await worker._start_cancellable_runtime(
            run=run,
            runtime=runtime,
            request=request,
        )

    assert runtime.stopped.is_set()


@pytest.mark.asyncio
async def test_start_monitor_runtime_cancel_failure_still_reaps_runtime_child(
    factory,
) -> None:
    class CancelFailingRuntime(BlockingCancellableRuntime):
        async def cancel(self, run_id: UUID) -> None:
            del run_id
            raise RuntimeError("runtime cancel failed")

    run = Run.create(
        tenant_id=uuid4(),
        employee_id=uuid4(),
        employee_version=1,
        created_by=uuid4(),
        input_data={},
    )
    cancel = RunCommand.create(
        run_id=run.id,
        tenant_id=run.tenant_id,
        action=RunCommandAction.CANCEL,
    )
    runtime = CancelFailingRuntime()
    worker = RunWorker(
        session_factory=factory,
        queue=MessageQueue([]),
        runtime_resolver=Resolver(runtime),
        consumer_name="cancel-failure-cleanup",
    )

    checks = 0

    async def request_cancel(run_id: UUID) -> list[RunCommand]:
        nonlocal checks
        assert run_id == run.id
        checks += 1
        if checks == 1:
            return []
        await runtime.started.wait()
        return [cancel]

    worker._pending_cancel_commands = request_cancel  # type: ignore[method-assign]
    request = RuntimeStartRequest(
        run_id=run.id,
        tenant_id=run.tenant_id,
        user_id=run.created_by,
        employee_id=run.employee_id,
        thread_id=run.thread_id,
        employee_definition={},
        input_data=run.input_data,
    )

    with pytest.raises(RuntimeError, match="runtime cancel failed"):
        await worker._start_cancellable_runtime(
            run=run,
            runtime=runtime,
            request=request,
        )

    assert runtime.stopped.is_set()


@pytest.mark.asyncio
async def test_start_monitor_parent_cancellation_cancels_and_awaits_runtime_child(
    factory,
) -> None:
    run = Run.create(
        tenant_id=uuid4(),
        employee_id=uuid4(),
        employee_version=1,
        created_by=uuid4(),
        input_data={},
    )
    runtime = BlockingCancellableRuntime()
    worker = RunWorker(
        session_factory=factory,
        queue=MessageQueue([]),
        runtime_resolver=Resolver(runtime),
        consumer_name="structured-parent-cancel",
    )
    never = asyncio.Event()

    checks = 0

    async def wait_forever(run_id: UUID) -> list[RunCommand]:
        nonlocal checks
        assert run_id == run.id
        checks += 1
        if checks == 1:
            return []
        await never.wait()
        return []

    worker._pending_cancel_commands = wait_forever  # type: ignore[method-assign]
    request = RuntimeStartRequest(
        run_id=run.id,
        tenant_id=run.tenant_id,
        user_id=run.created_by,
        employee_id=run.employee_id,
        thread_id=run.thread_id,
        employee_definition={},
        input_data=run.input_data,
    )
    monitor_task = asyncio.create_task(
        worker._start_cancellable_runtime(run=run, runtime=runtime, request=request)
    )
    await runtime.started.wait()

    monitor_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await monitor_task

    assert runtime.stopped.is_set()


@pytest.mark.asyncio
async def test_start_monitor_uses_bounded_backoff_instead_of_fixed_busy_poll(
    factory,
    monkeypatch,
) -> None:
    class PollingRuntime(CompletingRuntime):
        def __init__(self) -> None:
            super().__init__()
            self.release = asyncio.Event()

        async def start(self, request: RuntimeStartRequest) -> RuntimeState:
            self.requests.append(request)
            await self.release.wait()
            self.state = RuntimeState(
                run_id=request.run_id,
                status=RunStatus.COMPLETED,
                data={},
            )
            return self.state

    run = Run.create(
        tenant_id=uuid4(),
        employee_id=uuid4(),
        employee_version=1,
        created_by=uuid4(),
        input_data={},
    )
    runtime = PollingRuntime()
    worker = RunWorker(
        session_factory=factory,
        queue=MessageQueue([]),
        runtime_resolver=Resolver(runtime),
        consumer_name="bounded-cancel-poll",
        cancellation_poll_initial_seconds=0.01,
        cancellation_poll_max_seconds=0.04,
    )
    poll_count = 0

    async def pending(run_id: UUID) -> list[RunCommand]:
        nonlocal poll_count
        assert run_id == run.id
        poll_count += 1
        if poll_count == 4:
            runtime.release.set()
        return []

    observed_timeouts: list[float] = []
    real_wait_for = asyncio.wait_for

    async def recording_wait_for(awaitable, *, timeout: float):
        observed_timeouts.append(timeout)
        return await real_wait_for(awaitable, timeout=timeout)

    worker._pending_cancel_commands = pending  # type: ignore[method-assign]
    monkeypatch.setattr(run_worker_module.asyncio, "wait_for", recording_wait_for)
    request = RuntimeStartRequest(
        run_id=run.id,
        tenant_id=run.tenant_id,
        user_id=run.created_by,
        employee_id=run.employee_id,
        thread_id=run.thread_id,
        employee_definition={},
        input_data=run.input_data,
    )

    state, command_ids = await worker._start_cancellable_runtime(
        run=run,
        runtime=runtime,
        request=request,
    )

    assert state.status is RunStatus.COMPLETED
    assert command_ids == ()
    assert poll_count == 5
    assert observed_timeouts == [0.01, 0.02, 0.04, 0.04]


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["message", "resume", "approve", "reject"])
async def test_control_runner_interrupts_blocking_graph_operation_on_concurrent_cancel(
    factory,
    action: str,
) -> None:
    run = Run.create(
        tenant_id=uuid4(),
        employee_id=uuid4(),
        employee_version=1,
        created_by=uuid4(),
        input_data={},
    )
    cancel = RunCommand.create(
        run_id=run.id,
        tenant_id=run.tenant_id,
        action=RunCommandAction.CANCEL,
    )
    approval_id = uuid4()
    payload: dict[str, JsonValue] = {}
    if action == "message":
        payload["message"] = "continue"
    elif action in {"approve", "reject"}:
        payload["approval_id"] = str(approval_id)
    delivery = RunQueueDelivery(
        delivery_id=f"blocking-{action}",
        message=RunQueueMessage(
            command_id=uuid4(),
            run_id=run.id,
            tenant_id=run.tenant_id,
            action=action,
            payload=payload,
        ),
    )
    runtime = BlockingControlRuntime(run.id)
    worker = RunWorker(
        session_factory=factory,
        queue=MessageQueue([]),
        runtime_resolver=Resolver(runtime),
        consumer_name=f"blocking-{action}",
    )

    checks = 0

    async def pending(run_id: UUID) -> list[RunCommand]:
        nonlocal checks
        assert run_id == run.id
        checks += 1
        if checks == 1:
            return []
        await runtime.started.wait()
        return [cancel]

    worker._pending_cancel_commands = pending  # type: ignore[method-assign]

    result, cancellation_ids = await worker._run_cancellable_runtime_operation(
        run_id=run.id,
        runtime=runtime,
        operation=lambda: worker._invoke_control(runtime, delivery),
    )

    assert result is None
    assert cancellation_ids == (cancel.id,)
    assert runtime.cancel_calls == 1
    assert runtime.stopped.is_set()
    assert runtime.state is not None and runtime.state.status is RunStatus.CANCELLED


@pytest.mark.asyncio
async def test_blocking_resume_and_concurrent_cancel_are_persisted_and_acknowledged(
    factory,
) -> None:
    run = Run.create(
        tenant_id=uuid4(),
        employee_id=uuid4(),
        employee_version=1,
        created_by=uuid4(),
        input_data={},
    )
    version = EmployeeVersion(
        id=uuid4(),
        employee_id=run.employee_id,
        tenant_id=run.tenant_id,
        version=run.employee_version,
        definition={"work_mode": "workflow"},
        published_by=run.created_by,
        published_at=datetime.now(UTC),
    )
    start = RunCommand.create(
        run_id=run.id,
        tenant_id=run.tenant_id,
        action=RunCommandAction.START,
    )
    resume = RunCommand.create(
        run_id=run.id,
        tenant_id=run.tenant_id,
        action=RunCommandAction.RESUME,
    )
    async with factory() as session:
        await SqlAlchemyRunRepository(session).add(run)
        await SqlAlchemyEmployeeVersionRepository(session).add(version)
        await SqlAlchemyRunCommandRepository(session).add(start)
        await SqlAlchemyRunCommandRepository(session).add(resume)
        await session.commit()
    runtime = WaitingBlockingControlRuntime(run.id)
    queue = MessageQueue(
        [
            RunQueueDelivery(
                delivery_id="waiting-start",
                message=RunQueueMessage(
                    command_id=start.id,
                    run_id=run.id,
                    tenant_id=run.tenant_id,
                    action="start",
                ),
            ),
            RunQueueDelivery(
                delivery_id="blocking-resume",
                message=RunQueueMessage(
                    command_id=resume.id,
                    run_id=run.id,
                    tenant_id=run.tenant_id,
                    action="resume",
                ),
            ),
        ]
    )
    worker = RunWorker(
        session_factory=factory,
        queue=queue,
        runtime_resolver=Resolver(runtime),
        consumer_name="resume-cancel-worker",
        cancellation_poll_initial_seconds=0.01,
        cancellation_poll_max_seconds=0.02,
    )
    assert await worker.run_once(block_ms=1)
    resume_task = asyncio.create_task(worker.run_once(block_ms=1))
    await asyncio.wait_for(runtime.started.wait(), timeout=1)
    cancel = RunCommand.create(
        run_id=run.id,
        tenant_id=run.tenant_id,
        action=RunCommandAction.CANCEL,
    )
    async with factory() as session:
        await SqlAlchemyRunCommandRepository(session).add(cancel)
        await session.commit()
    queue.deliveries.append(
        RunQueueDelivery(
            delivery_id="concurrent-cancel",
            message=RunQueueMessage(
                command_id=cancel.id,
                run_id=run.id,
                tenant_id=run.tenant_id,
                action="cancel",
            ),
        )
    )

    assert await asyncio.wait_for(resume_task, timeout=1)
    assert await worker.run_once(block_ms=1)

    async with factory() as session:
        persisted = await SqlAlchemyRunRepository(session).get(
            tenant_id=run.tenant_id,
            run_id=run.id,
        )
        events = await SqlAlchemyRunEventRepository(session).list(
            run_id=run.id,
            after_sequence=0,
        )
        commands = SqlAlchemyRunCommandRepository(session)
        assert await commands.is_processed(start.id)
        assert await commands.is_processed(resume.id)
        assert await commands.is_processed(cancel.id)
    assert persisted is not None and persisted.status is RunStatus.CANCELLED
    assert queue.acknowledged == [
        "waiting-start",
        "blocking-resume",
        "concurrent-cancel",
    ]
    assert runtime.control_side_effects == 0
    assert [event.type for event in events].count(EventType.RUN_CANCELLED) == 1
    assert EventType.RUN_COMPLETED not in [event.type for event in events]


@pytest.mark.asyncio
async def test_terminal_persistence_prefers_cancel_committed_after_runtime_return(
    factory,
) -> None:
    run = Run.create(
        tenant_id=uuid4(),
        employee_id=uuid4(),
        employee_version=1,
        created_by=uuid4(),
        input_data={},
    ).transition_to(RunStatus.RUNNING)
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
    async with factory() as session:
        await SqlAlchemyRunRepository(session).add(run)
        await SqlAlchemyRunCommandRepository(session).add(start)
        await SqlAlchemyRunCommandRepository(session).add(cancel)
        await session.commit()

    worker = RunWorker(
        session_factory=factory,
        queue=MessageQueue([]),
        runtime_resolver=Resolver(CompletingRuntime()),
        consumer_name="terminal-cancel-race",
    )
    await worker._claim_ownership(run)
    history = [
        PlatformEvent.create(
            tenant_id=run.tenant_id,
            employee_id=run.employee_id,
            run_id=run.id,
            sequence=1,
            event_type=EventType.RUN_STARTED,
            payload={},
        ),
        PlatformEvent.create(
            tenant_id=run.tenant_id,
            employee_id=run.employee_id,
            run_id=run.id,
            sequence=2,
            event_type=EventType.RUN_COMPLETED,
            payload={"status": "completed"},
        ),
    ]

    persisted_status = await worker._persist_runtime_result(
        run=run,
        message_command_id=start.id,
        state=RuntimeState(
            run_id=run.id,
            status=RunStatus.COMPLETED,
            data={"output": "too late"},
        ),
        history=history,
    )

    assert persisted_status is RunStatus.CANCELLED
    async with factory() as session:
        persisted = await SqlAlchemyRunRepository(session).get(
            tenant_id=run.tenant_id,
            run_id=run.id,
        )
        events = await SqlAlchemyRunEventRepository(session).list(
            run_id=run.id,
            after_sequence=0,
        )
        commands = SqlAlchemyRunCommandRepository(session)
        assert await commands.is_processed(start.id)
        assert await commands.is_processed(cancel.id)
    assert persisted is not None and persisted.status is RunStatus.CANCELLED
    assert [event.type for event in events] == [
        EventType.RUN_STARTED,
        EventType.RUN_CANCELLED,
    ]


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
async def test_new_worker_recovers_waiting_run_before_processing_control(factory) -> None:
    run = (
        Run.create(
            tenant_id=uuid4(),
            employee_id=uuid4(),
            employee_version=4,
            created_by=uuid4(),
            input_data={"task": "wait"},
        )
        .transition_to(RunStatus.RUNNING)
        .transition_to(RunStatus.WAITING_FOR_INPUT)
    )
    command = RunCommand.create(
        run_id=run.id,
        tenant_id=run.tenant_id,
        action=RunCommandAction.MESSAGE,
        payload={"message": "continue after crash"},
    )
    definition = {"work_mode": "workflow", "workflow_id": str(uuid4())}
    version = EmployeeVersion(
        id=uuid4(),
        employee_id=run.employee_id,
        tenant_id=run.tenant_id,
        version=run.employee_version,
        definition=definition,
        published_by=run.created_by,
        published_at=datetime.now(UTC),
    )
    async with factory() as session:
        await SqlAlchemyRunRepository(session).add(run)
        await SqlAlchemyEmployeeVersionRepository(session).add(version)
        await SqlAlchemyRunCommandRepository(session).add(command)
        await session.commit()

    runtime = RestorableRuntime()
    resolver = RecoveringResolver(runtime)
    queue = OneMessageQueue(
        RunQueueDelivery(
            delivery_id="recovered-control",
            message=RunQueueMessage(
                command_id=command.id,
                run_id=run.id,
                tenant_id=run.tenant_id,
                action="message",
                payload={"message": "continue after crash"},
            ),
        )
    )

    await RunWorker(
        session_factory=factory,
        queue=queue,
        runtime_resolver=resolver,
        consumer_name="replacement-worker",
        runtime_lease_duration=timedelta(seconds=30),
    ).run_once(block_ms=1)

    assert resolver.recovery_calls == [(run, definition)]
    assert len(runtime.recovered) == 1
    assert runtime.recovered[0][1] is RunStatus.WAITING_FOR_INPUT
    assert runtime.messages == ["continue after crash"]
    assert queue.acknowledged == ["recovered-control"]
    async with factory() as session:
        persisted = await SqlAlchemyRunRepository(session).get(
            tenant_id=run.tenant_id, run_id=run.id
        )
        assert persisted is not None and persisted.status is RunStatus.COMPLETED
        ownership = await SqlAlchemyRuntimeOwnershipRepository(session).get(run_id=run.id)
        assert ownership is not None and ownership.owner_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["cancel", "reject"])
async def test_recovered_terminal_control_does_not_duplicate_approval_required(
    factory,
    action: str,
) -> None:
    approval_id = uuid4()
    run = (
        Run.create(
            tenant_id=uuid4(),
            employee_id=uuid4(),
            employee_version=1,
            created_by=uuid4(),
            input_data={},
        )
        .transition_to(RunStatus.RUNNING)
        .transition_to(RunStatus.WAITING_FOR_APPROVAL)
    )
    version = EmployeeVersion(
        id=uuid4(),
        employee_id=run.employee_id,
        tenant_id=run.tenant_id,
        version=run.employee_version,
        definition={"work_mode": "workflow"},
        published_by=run.created_by,
        published_at=datetime.now(UTC),
    )
    command_action = RunCommandAction.REJECT if action == "reject" else RunCommandAction.CANCEL
    command = RunCommand.create(
        run_id=run.id,
        tenant_id=run.tenant_id,
        action=command_action,
        payload={"approval_id": str(approval_id)} if action == "reject" else {},
    )
    existing_approval = PlatformEvent.create(
        tenant_id=run.tenant_id,
        employee_id=run.employee_id,
        run_id=run.id,
        sequence=1,
        event_type=EventType.APPROVAL_REQUIRED,
        payload={"approval_id": str(approval_id)},
    )
    async with factory() as session:
        await SqlAlchemyRunRepository(session).add(run)
        await SqlAlchemyEmployeeVersionRepository(session).add(version)
        await SqlAlchemyRunCommandRepository(session).add(command)
        await SqlAlchemyRunEventRepository(session).append(existing_approval)
        await session.commit()
    runtime = ApprovalRecoverRuntime(approval_id)
    queue = OneMessageQueue(
        RunQueueDelivery(
            delivery_id=f"recovered-{action}",
            message=RunQueueMessage(
                command_id=command.id,
                run_id=run.id,
                tenant_id=run.tenant_id,
                action=action,
                payload=command.payload,
            ),
        )
    )

    assert await RunWorker(
        session_factory=factory,
        queue=queue,
        runtime_resolver=RecoveringResolver(runtime),
        consumer_name=f"replacement-{action}",
    ).run_once(block_ms=1)

    assert runtime.rejections == ([(run.id, approval_id)] if action == "reject" else [])
    async with factory() as session:
        persisted = await SqlAlchemyRunRepository(session).get(
            tenant_id=run.tenant_id,
            run_id=run.id,
        )
        events = await SqlAlchemyRunEventRepository(session).list(
            run_id=run.id,
            after_sequence=0,
        )
        commands = SqlAlchemyRunCommandRepository(session)
        assert await commands.is_processed(command.id)
        assert await commands.unprocessed_cancel_commands(run_id=run.id) == []
    assert persisted is not None and persisted.status is RunStatus.CANCELLED
    assert [event.type for event in events].count(EventType.APPROVAL_REQUIRED) == 1
    assert [event.type for event in events].count(EventType.RUN_CANCELLED) == 1


@pytest.mark.asyncio
async def test_recovered_reject_with_wrong_approval_id_is_controlled_noop(factory) -> None:
    expected_approval_id = uuid4()
    wrong_approval_id = uuid4()
    run = (
        Run.create(
            tenant_id=uuid4(),
            employee_id=uuid4(),
            employee_version=1,
            created_by=uuid4(),
            input_data={},
        )
        .transition_to(RunStatus.RUNNING)
        .transition_to(RunStatus.WAITING_FOR_APPROVAL)
    )
    version = EmployeeVersion(
        id=uuid4(),
        employee_id=run.employee_id,
        tenant_id=run.tenant_id,
        version=run.employee_version,
        definition={"work_mode": "workflow"},
        published_by=run.created_by,
        published_at=datetime.now(UTC),
    )
    reject = RunCommand.create(
        run_id=run.id,
        tenant_id=run.tenant_id,
        action=RunCommandAction.REJECT,
        payload={"approval_id": str(wrong_approval_id)},
    )
    async with factory() as session:
        await SqlAlchemyRunRepository(session).add(run)
        await SqlAlchemyEmployeeVersionRepository(session).add(version)
        await SqlAlchemyRunCommandRepository(session).add(reject)
        await SqlAlchemyRunEventRepository(session).append(
            PlatformEvent.create(
                tenant_id=run.tenant_id,
                employee_id=run.employee_id,
                run_id=run.id,
                sequence=1,
                event_type=EventType.APPROVAL_REQUIRED,
                payload={"approval_id": str(expected_approval_id)},
            )
        )
        await session.commit()
    runtime = ApprovalRecoverRuntime(expected_approval_id)
    queue = OneMessageQueue(
        RunQueueDelivery(
            delivery_id="mismatched-reject",
            message=RunQueueMessage(
                command_id=reject.id,
                run_id=run.id,
                tenant_id=run.tenant_id,
                action="reject",
                payload=reject.payload,
            ),
        )
    )

    assert await RunWorker(
        session_factory=factory,
        queue=queue,
        runtime_resolver=RecoveringResolver(runtime),
        consumer_name="replacement-mismatch",
    ).run_once(block_ms=1)

    assert runtime.rejections == []
    async with factory() as session:
        persisted = await SqlAlchemyRunRepository(session).get(
            tenant_id=run.tenant_id,
            run_id=run.id,
        )
        events = await SqlAlchemyRunEventRepository(session).list(
            run_id=run.id,
            after_sequence=0,
        )
        assert await SqlAlchemyRunCommandRepository(session).is_processed(reject.id)
    assert persisted is not None
    assert persisted.status is RunStatus.WAITING_FOR_APPROVAL
    assert [event.type for event in events].count(EventType.APPROVAL_REQUIRED) == 1
    assert events[-1].payload == {
        "action": "reject",
        "status": "control_rejected",
        "code": "runtime_control_mismatch",
    }


@pytest.mark.asyncio
async def test_worker_startup_recovers_waiting_runs_and_fails_unknown_running_work(factory) -> None:
    tenant_id = uuid4()
    user_id = uuid4()
    employee_id = uuid4()
    waiting = (
        Run.create(
            tenant_id=tenant_id,
            employee_id=employee_id,
            employee_version=1,
            created_by=user_id,
            input_data={},
        )
        .transition_to(RunStatus.RUNNING)
        .transition_to(RunStatus.WAITING_FOR_INPUT)
    )
    waiting_two = (
        Run.create(
            tenant_id=tenant_id,
            employee_id=employee_id,
            employee_version=1,
            created_by=user_id,
            input_data={"second": True},
        )
        .transition_to(RunStatus.RUNNING)
        .transition_to(RunStatus.WAITING_FOR_INPUT)
    )
    running = Run.create(
        tenant_id=tenant_id,
        employee_id=employee_id,
        employee_version=1,
        created_by=user_id,
        input_data={},
    ).transition_to(RunStatus.RUNNING)
    version = EmployeeVersion(
        id=uuid4(),
        employee_id=employee_id,
        tenant_id=tenant_id,
        version=1,
        definition={"work_mode": "workflow"},
        published_by=user_id,
        published_at=datetime.now(UTC),
    )
    async with factory() as session:
        await SqlAlchemyRunRepository(session).add(waiting)
        await SqlAlchemyRunRepository(session).add(waiting_two)
        await SqlAlchemyRunRepository(session).add(running)
        await SqlAlchemyEmployeeVersionRepository(session).add(version)
        await session.commit()
    runtime = RestorableRuntime()
    resolver = RecoveringResolver(runtime)
    worker = RunWorker(
        session_factory=factory,
        queue=MessageQueue([]),
        runtime_resolver=resolver,
        consumer_name="replacement-worker",
    )

    recovered = await worker.recover_incomplete_runs(limit=1)

    assert recovered == 2
    assert {call[0].id for call in resolver.recovery_calls} == {
        waiting.id,
        waiting_two.id,
    }
    assert all(status is RunStatus.WAITING_FOR_INPUT for _, status in runtime.recovered)
    async with factory() as session:
        persisted_running = await SqlAlchemyRunRepository(session).get(
            tenant_id=tenant_id,
            run_id=running.id,
        )
        assert persisted_running is not None
        assert persisted_running.status is RunStatus.FAILED
        assert persisted_running.error_code == "runtime_interrupted"
        waiting_owner = await SqlAlchemyRuntimeOwnershipRepository(session).get(run_id=waiting.id)
        assert waiting_owner is not None and waiting_owner.owner_id is not None

    previous_expiry = waiting_owner.expires_at
    await worker.renew_active_runtimes()
    async with factory() as session:
        renewed = await SqlAlchemyRuntimeOwnershipRepository(session).get(run_id=waiting.id)
        assert renewed is not None and renewed.expires_at >= previous_expiry
    await worker.aclose()
    assert resolver.prepared is not None and resolver.prepared.detach_calls == 1


@pytest.mark.asyncio
async def test_startup_busy_retry_does_not_recover_completed_partial_batch_twice(factory) -> None:
    tenant_id = uuid4()
    user_id = uuid4()
    employee_id = uuid4()
    first = (
        Run.create(
            tenant_id=tenant_id,
            employee_id=employee_id,
            employee_version=1,
            created_by=user_id,
            input_data={"order": 1},
        )
        .transition_to(RunStatus.RUNNING)
        .transition_to(RunStatus.WAITING_FOR_INPUT)
    )
    second = (
        Run.create(
            tenant_id=tenant_id,
            employee_id=employee_id,
            employee_version=1,
            created_by=user_id,
            input_data={"order": 2},
        )
        .transition_to(RunStatus.RUNNING)
        .transition_to(RunStatus.WAITING_FOR_INPUT)
    )
    version = EmployeeVersion(
        id=uuid4(),
        employee_id=employee_id,
        tenant_id=tenant_id,
        version=1,
        definition={"work_mode": "workflow"},
        published_by=user_id,
        published_at=datetime.now(UTC),
    )
    async with factory() as session:
        await SqlAlchemyRunRepository(session).add(first)
        await SqlAlchemyRunRepository(session).add(second)
        await SqlAlchemyEmployeeVersionRepository(session).add(version)
        await session.commit()
    now = datetime.now(UTC)
    async with factory() as session:
        held = await SqlAlchemyRuntimeOwnershipRepository(session).claim(
            run_id=second.id,
            tenant_id=tenant_id,
            owner_id="old-worker",
            now=now,
            lease_duration=timedelta(minutes=5),
        )
        await session.commit()
    resolver = RecoveringResolver(RestorableRuntime())
    worker = RunWorker(
        session_factory=factory,
        queue=MessageQueue([]),
        runtime_resolver=resolver,
        consumer_name="replacement",
    )

    with pytest.raises(RuntimeOwnershipBusy):
        await worker.recover_incomplete_runs(limit=10)
    assert [run.id for run, _ in resolver.recovery_calls] == [first.id]

    async with factory() as session:
        await SqlAlchemyRuntimeOwnershipRepository(session).release(
            run_id=second.id,
            owner_id=held.owner_id or "",
            epoch=held.epoch,
        )
        await session.commit()
    assert await worker.recover_incomplete_runs(limit=10) == 1
    assert [run.id for run, _ in resolver.recovery_calls] == [first.id, second.id]


@pytest.mark.asyncio
async def test_startup_runtime_recovery_transient_failure_can_retry_without_leaking_owner(
    factory,
) -> None:
    run = (
        Run.create(
            tenant_id=uuid4(),
            employee_id=uuid4(),
            employee_version=1,
            created_by=uuid4(),
            input_data={},
        )
        .transition_to(RunStatus.RUNNING)
        .transition_to(RunStatus.WAITING_FOR_INPUT)
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
        await session.commit()
    runtime = TransientRecoverRuntime()
    resolver = RecoveringResolver(runtime)
    worker = RunWorker(
        session_factory=factory,
        queue=MessageQueue([]),
        runtime_resolver=resolver,
        consumer_name="replacement",
    )

    with pytest.raises(RuntimeRecoveryTransient):
        await worker.recover_incomplete_runs()
    async with factory() as session:
        persisted = await SqlAlchemyRunRepository(session).get(
            tenant_id=run.tenant_id,
            run_id=run.id,
        )
        ownership = await SqlAlchemyRuntimeOwnershipRepository(session).get(run_id=run.id)
        assert persisted is not None and persisted.status is RunStatus.WAITING_FOR_INPUT
        assert ownership is not None and ownership.owner_id is None

    assert await worker.recover_incomplete_runs() == 1
    assert runtime.recovery_attempts == 2
    assert resolver.recovery_calls == [(run, version.definition), (run, version.definition)]


@pytest.mark.asyncio
async def test_transient_recovery_releases_ownership_even_when_detach_fails(factory) -> None:
    run = (
        Run.create(
            tenant_id=uuid4(),
            employee_id=uuid4(),
            employee_version=1,
            created_by=uuid4(),
            input_data={},
        )
        .transition_to(RunStatus.RUNNING)
        .transition_to(RunStatus.WAITING_FOR_INPUT)
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
        await session.commit()
    worker = RunWorker(
        session_factory=factory,
        queue=MessageQueue([]),
        runtime_resolver=DetachFailingRecoveringResolver(TransientRecoverRuntime()),
        consumer_name="replacement",
    )

    with pytest.raises(RuntimeRecoveryTransient):
        await worker.recover_incomplete_runs()

    async with factory() as session:
        ownership = await SqlAlchemyRuntimeOwnershipRepository(session).get(run_id=run.id)
        assert ownership is not None and ownership.owner_id is None


@pytest.mark.asyncio
async def test_started_tool_without_advanced_checkpoint_fails_uncertain_without_replay(
    factory,
) -> None:
    approval_id = uuid4()
    run = (
        Run.create(
            tenant_id=uuid4(),
            employee_id=uuid4(),
            employee_version=1,
            created_by=uuid4(),
            input_data={},
        )
        .transition_to(RunStatus.RUNNING)
        .transition_to(RunStatus.WAITING_FOR_APPROVAL)
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
    approval_command = RunCommand.create(
        run_id=run.id,
        tenant_id=run.tenant_id,
        action=RunCommandAction.APPROVE,
        payload={"approval_id": str(approval_id)},
    )
    async with factory() as session:
        await SqlAlchemyRunRepository(session).add(run)
        await SqlAlchemyEmployeeVersionRepository(session).add(version)
        await SqlAlchemyRunCommandRepository(session).add(approval_command)
        await SqlAlchemyRunEventRepository(session).append(
            PlatformEvent.create(
                tenant_id=run.tenant_id,
                employee_id=run.employee_id,
                run_id=run.id,
                sequence=1,
                event_type=EventType.APPROVAL_REQUIRED,
                payload={"approval_id": str(approval_id)},
            )
        )
        await session.commit()
    await SqlAlchemyToolAuditSink(factory).emit(
        ToolAuditEvent(
            event_type=AuditEventType.STARTED,
            occurred_at=datetime.now(UTC),
            tenant_id=run.tenant_id,
            run_id=run.id,
            employee_id=run.employee_id,
            user_id=run.created_by,
            tool_id=uuid4(),
            tool_name="external_operation",
            risk=None,
            argument_summary=ArgumentSummary(keys=("value",), sha256="a" * 64, size_bytes=1),
            invocation_id=approval_id,
        )
    )
    runtime = ApprovalRecoverRuntime(approval_id)
    resolver = RecoveringResolver(runtime)
    worker = RunWorker(
        session_factory=factory,
        queue=MessageQueue([]),
        runtime_resolver=resolver,
        consumer_name="replacement",
    )

    assert await worker.recover_incomplete_runs() == 0

    async with factory() as session:
        persisted = await SqlAlchemyRunRepository(session).get(
            tenant_id=run.tenant_id,
            run_id=run.id,
        )
        assert persisted is not None and persisted.status is RunStatus.FAILED
        assert persisted.error_code == "tool_execution_uncertain"
        assert await SqlAlchemyRunCommandRepository(session).is_processed(approval_command.id)
    assert resolver.recovery_calls == [(run, version.definition)]
    assert resolver.prepared is not None and resolver.prepared.close_calls == 1
    assert len(runtime.recovered) == 1
    assert runtime.recovered[0][1] is RunStatus.WAITING_FOR_APPROVAL
    assert runtime.approvals == []


@pytest.mark.asyncio
async def test_redelivered_approval_with_started_tool_fails_uncertain_after_runtime_recovery(
    factory,
) -> None:
    approval_id = uuid4()
    run = (
        Run.create(
            tenant_id=uuid4(),
            employee_id=uuid4(),
            employee_version=1,
            created_by=uuid4(),
            input_data={},
        )
        .transition_to(RunStatus.RUNNING)
        .transition_to(RunStatus.WAITING_FOR_APPROVAL)
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
    approval_command = RunCommand.create(
        run_id=run.id,
        tenant_id=run.tenant_id,
        action=RunCommandAction.APPROVE,
        payload={"approval_id": str(approval_id)},
    )
    async with factory() as session:
        await SqlAlchemyRunRepository(session).add(run)
        await SqlAlchemyEmployeeVersionRepository(session).add(version)
        await SqlAlchemyRunCommandRepository(session).add(approval_command)
        await session.commit()
    await SqlAlchemyToolAuditSink(factory).emit(
        ToolAuditEvent(
            event_type=AuditEventType.STARTED,
            occurred_at=datetime.now(UTC),
            tenant_id=run.tenant_id,
            run_id=run.id,
            employee_id=run.employee_id,
            user_id=run.created_by,
            tool_id=uuid4(),
            tool_name="external_operation",
            risk=None,
            argument_summary=ArgumentSummary(keys=("value",), sha256="a" * 64, size_bytes=1),
            invocation_id=approval_id,
        )
    )
    delivery = RunQueueDelivery(
        delivery_id="redelivered-approval-after-tool-started",
        message=RunQueueMessage(
            command_id=approval_command.id,
            run_id=run.id,
            tenant_id=run.tenant_id,
            action="approve",
            payload={"approval_id": str(approval_id)},
        ),
    )
    queue = OneMessageQueue(delivery)
    runtime = ApprovalRecoverRuntime(approval_id)
    resolver = RecoveringResolver(runtime)
    worker = RunWorker(
        session_factory=factory,
        queue=queue,
        runtime_resolver=resolver,
        consumer_name="replacement",
    )

    assert await worker.run_once(block_ms=1)

    assert resolver.recovery_calls == [(run, version.definition)]
    assert resolver.prepared is not None and resolver.prepared.close_calls == 1
    assert len(runtime.recovered) == 1
    assert runtime.recovered[0][1] is RunStatus.WAITING_FOR_APPROVAL
    assert runtime.approvals == []
    assert queue.acknowledged == [delivery.delivery_id]
    async with factory() as session:
        persisted = await SqlAlchemyRunRepository(session).get(
            tenant_id=run.tenant_id,
            run_id=run.id,
        )
        assert persisted is not None and persisted.status is RunStatus.FAILED
        assert persisted.error_code == "tool_execution_uncertain"
        assert await SqlAlchemyRunCommandRepository(session).is_processed(approval_command.id)


@pytest.mark.asyncio
async def test_recovered_next_interrupt_settles_old_approval_command(factory) -> None:
    old_approval_id = uuid4()
    current_approval_id = uuid4()
    run = (
        Run.create(
            tenant_id=uuid4(),
            employee_id=uuid4(),
            employee_version=1,
            created_by=uuid4(),
            input_data={},
        )
        .transition_to(RunStatus.RUNNING)
        .transition_to(RunStatus.WAITING_FOR_APPROVAL)
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
    old_command = RunCommand.create(
        run_id=run.id,
        tenant_id=run.tenant_id,
        action=RunCommandAction.APPROVE,
        payload={"approval_id": str(old_approval_id)},
    )
    async with factory() as session:
        await SqlAlchemyRunRepository(session).add(run)
        await SqlAlchemyEmployeeVersionRepository(session).add(version)
        await SqlAlchemyRunCommandRepository(session).add(old_command)
        await SqlAlchemyRunEventRepository(session).append(
            PlatformEvent.create(
                tenant_id=run.tenant_id,
                employee_id=run.employee_id,
                run_id=run.id,
                sequence=1,
                event_type=EventType.APPROVAL_REQUIRED,
                payload={"approval_id": str(old_approval_id)},
            )
        )
        await session.commit()
    await SqlAlchemyToolAuditSink(factory).emit(
        ToolAuditEvent(
            event_type=AuditEventType.STARTED,
            occurred_at=datetime.now(UTC),
            tenant_id=run.tenant_id,
            run_id=run.id,
            employee_id=run.employee_id,
            user_id=run.created_by,
            tool_id=uuid4(),
            tool_name="external_operation",
            risk=None,
            argument_summary=ArgumentSummary(keys=("value",), sha256="a" * 64, size_bytes=1),
            invocation_id=old_approval_id,
        )
    )
    worker = RunWorker(
        session_factory=factory,
        queue=MessageQueue([]),
        runtime_resolver=RecoveringResolver(ApprovalRecoverRuntime(current_approval_id)),
        consumer_name="replacement",
    )

    assert await worker.recover_incomplete_runs() == 1

    async with factory() as session:
        commands = SqlAlchemyRunCommandRepository(session)
        assert await commands.is_processed(old_command.id)
        events = await SqlAlchemyRunEventRepository(session).list(
            run_id=run.id,
            after_sequence=0,
        )
        assert events[-1].payload["approval_id"] == str(current_approval_id)


@pytest.mark.asyncio
async def test_completed_checkpoint_rolls_waiting_database_forward_without_replay(factory) -> None:
    approval_id = uuid4()
    run = (
        Run.create(
            tenant_id=uuid4(),
            employee_id=uuid4(),
            employee_version=1,
            created_by=uuid4(),
            input_data={},
        )
        .transition_to(RunStatus.RUNNING)
        .transition_to(RunStatus.WAITING_FOR_APPROVAL)
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
    command = RunCommand.create(
        run_id=run.id,
        tenant_id=run.tenant_id,
        action=RunCommandAction.APPROVE,
        payload={"approval_id": str(approval_id)},
    )
    async with factory() as session:
        await SqlAlchemyRunRepository(session).add(run)
        await SqlAlchemyEmployeeVersionRepository(session).add(version)
        await SqlAlchemyRunCommandRepository(session).add(command)
        await SqlAlchemyRunEventRepository(session).append(
            PlatformEvent.create(
                tenant_id=run.tenant_id,
                employee_id=run.employee_id,
                run_id=run.id,
                sequence=1,
                event_type=EventType.APPROVAL_REQUIRED,
                payload={"approval_id": str(approval_id)},
            )
        )
        await session.commit()
    tool_id = uuid4()
    audit_common = {
        "occurred_at": datetime.now(UTC),
        "tenant_id": run.tenant_id,
        "run_id": run.id,
        "employee_id": run.employee_id,
        "user_id": run.created_by,
        "tool_id": tool_id,
        "tool_name": "external_operation",
        "risk": None,
        "argument_summary": ArgumentSummary(keys=("value",), sha256="a" * 64, size_bytes=1),
        "invocation_id": approval_id,
    }
    audit_sink = SqlAlchemyToolAuditSink(factory)
    await audit_sink.emit(ToolAuditEvent(event_type=AuditEventType.STARTED, **audit_common))
    await audit_sink.emit(
        ToolAuditEvent(
            event_type=AuditEventType.COMPLETED,
            succeeded=True,
            **audit_common,
        )
    )
    resolver = RecoveringResolver(CompletedRecoverRuntime())
    worker = RunWorker(
        session_factory=factory,
        queue=MessageQueue([]),
        runtime_resolver=resolver,
        consumer_name="replacement",
    )

    assert await worker.recover_incomplete_runs() == 0

    async with factory() as session:
        persisted = await SqlAlchemyRunRepository(session).get(
            tenant_id=run.tenant_id,
            run_id=run.id,
        )
        assert persisted is not None and persisted.status is RunStatus.COMPLETED
        assert await SqlAlchemyRunCommandRepository(session).is_processed(command.id)
    assert resolver.prepared is not None and resolver.prepared.close_calls == 1


@pytest.mark.asyncio
async def test_started_tool_with_unavailable_checkpoint_fails_uncertain(factory) -> None:
    approval_id = uuid4()
    run = (
        Run.create(
            tenant_id=uuid4(),
            employee_id=uuid4(),
            employee_version=1,
            created_by=uuid4(),
            input_data={},
        )
        .transition_to(RunStatus.RUNNING)
        .transition_to(RunStatus.WAITING_FOR_APPROVAL)
    )
    version = EmployeeVersion(
        id=uuid4(),
        employee_id=run.employee_id,
        tenant_id=run.tenant_id,
        version=run.employee_version,
        definition={"work_mode": "autonomous"},
        published_by=run.created_by,
        published_at=datetime.now(UTC),
    )
    command = RunCommand.create(
        run_id=run.id,
        tenant_id=run.tenant_id,
        action=RunCommandAction.APPROVE,
        payload={"approval_id": str(approval_id)},
    )
    async with factory() as session:
        await SqlAlchemyRunRepository(session).add(run)
        await SqlAlchemyEmployeeVersionRepository(session).add(version)
        await SqlAlchemyRunCommandRepository(session).add(command)
        await session.commit()
    await SqlAlchemyToolAuditSink(factory).emit(
        ToolAuditEvent(
            event_type=AuditEventType.STARTED,
            occurred_at=datetime.now(UTC),
            tenant_id=run.tenant_id,
            run_id=run.id,
            employee_id=run.employee_id,
            user_id=run.created_by,
            tool_id=uuid4(),
            tool_name="external_operation",
            risk=None,
            argument_summary=ArgumentSummary(keys=("value",), sha256="a" * 64, size_bytes=1),
            invocation_id=approval_id,
        )
    )
    resolver = RecoveringResolver(UnavailableRuntime())
    worker = RunWorker(
        session_factory=factory,
        queue=MessageQueue([]),
        runtime_resolver=resolver,
        consumer_name="replacement-worker",
    )

    assert await worker.recover_incomplete_runs() == 0

    async with factory() as session:
        persisted = await SqlAlchemyRunRepository(session).get(
            tenant_id=run.tenant_id,
            run_id=run.id,
        )
        assert persisted is not None and persisted.status is RunStatus.FAILED
        assert persisted.error_code == "tool_execution_uncertain"
        assert await SqlAlchemyRunCommandRepository(session).is_processed(command.id)
    assert resolver.prepared is not None and resolver.prepared.close_calls == 1


@pytest.mark.asyncio
async def test_unrecoverable_runtime_is_stably_failed_and_control_is_acknowledged(factory) -> None:
    run = (
        Run.create(
            tenant_id=uuid4(),
            employee_id=uuid4(),
            employee_version=1,
            created_by=uuid4(),
            input_data={},
        )
        .transition_to(RunStatus.RUNNING)
        .transition_to(RunStatus.WAITING_FOR_APPROVAL)
    )
    command = RunCommand.create(
        run_id=run.id,
        tenant_id=run.tenant_id,
        action=RunCommandAction.APPROVE,
        payload={"approval_id": str(uuid4())},
    )
    version = EmployeeVersion(
        id=uuid4(),
        employee_id=run.employee_id,
        tenant_id=run.tenant_id,
        version=run.employee_version,
        definition={"work_mode": "autonomous"},
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
            delivery_id="unrecoverable-control",
            message=RunQueueMessage(
                command_id=command.id,
                run_id=run.id,
                tenant_id=run.tenant_id,
                action="approve",
                payload=command.payload,
            ),
        )
    )

    resolver = RecoveringResolver(UnavailableRuntime())
    await RunWorker(
        session_factory=factory,
        queue=queue,
        runtime_resolver=resolver,
        consumer_name="replacement-worker",
    ).run_once(block_ms=1)

    assert queue.acknowledged == ["unrecoverable-control"]
    async with factory() as session:
        persisted = await SqlAlchemyRunRepository(session).get(
            tenant_id=run.tenant_id, run_id=run.id
        )
        assert persisted is not None and persisted.status is RunStatus.FAILED
        assert persisted.error_code == "runtime_recovery_unavailable"
        events = await SqlAlchemyRunEventRepository(session).list(run_id=run.id, after_sequence=0)
        assert events[-1].type is EventType.RUN_FAILED
        assert events[-1].payload == {"code": "runtime_recovery_unavailable"}
    assert resolver.prepared is not None
    assert resolver.prepared.close_calls == 1
    assert resolver.prepared.detach_calls == 0


@pytest.mark.asyncio
async def test_runtime_ownership_takeover_fences_stale_epoch(factory) -> None:
    run = Run.create(
        tenant_id=uuid4(),
        employee_id=uuid4(),
        employee_version=1,
        created_by=uuid4(),
        input_data={},
    )
    async with factory() as session:
        await SqlAlchemyRunRepository(session).add(run)
        await session.commit()

    now = datetime.now(UTC)
    async with factory() as session:
        repository = SqlAlchemyRuntimeOwnershipRepository(session)
        first = await repository.claim(
            run_id=run.id,
            tenant_id=run.tenant_id,
            owner_id="worker-a",
            now=now,
            lease_duration=timedelta(seconds=10),
        )
        await session.commit()
    async with factory() as session:
        repository = SqlAlchemyRuntimeOwnershipRepository(session)
        with pytest.raises(RuntimeOwnershipBusy):
            await repository.claim(
                run_id=run.id,
                tenant_id=run.tenant_id,
                owner_id="worker-b",
                now=now + timedelta(seconds=9),
                lease_duration=timedelta(seconds=10),
            )
    async with factory() as session:
        repository = SqlAlchemyRuntimeOwnershipRepository(session)
        second = await repository.claim(
            run_id=run.id,
            tenant_id=run.tenant_id,
            owner_id="worker-b",
            now=now + timedelta(seconds=11),
            lease_duration=timedelta(seconds=10),
        )
        assert second.epoch == first.epoch + 1
        with pytest.raises(RuntimeOwnershipLost):
            await repository.assert_owned(
                run_id=run.id,
                owner_id="worker-a",
                epoch=first.epoch,
                now=now + timedelta(seconds=11),
            )
        assert not await repository.release(
            run_id=run.id,
            owner_id="worker-a",
            epoch=first.epoch,
        )
        assert await repository.release(
            run_id=run.id,
            owner_id="worker-b",
            epoch=second.epoch,
        )
        third = await repository.claim(
            run_id=run.id,
            tenant_id=run.tenant_id,
            owner_id="worker-c",
            now=now + timedelta(seconds=12),
            lease_duration=timedelta(seconds=10),
        )
        assert third.epoch == second.epoch + 1
        with pytest.raises(RuntimeOwnershipLost):
            await repository.assert_owned(
                run_id=run.id,
                owner_id="worker-b",
                epoch=second.epoch,
                now=now + timedelta(seconds=12),
            )
        await session.commit()


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

    resolver.prepared.renew_error = RuntimeError("deleting lease cannot be renewed")
    await worker.renew_active_runtimes()

    assert resolver.prepared.renew_calls == 0
    assert resolver.prepared.close_calls == 1

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
async def test_exhausted_transient_failure_is_dead_lettered_with_stable_error_and_runtime_closed(
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
    queue = ExhaustingQueue(
        RunQueueDelivery(
            delivery_id="exhausted-stream",
            message=RunQueueMessage(
                command_id=command.id,
                run_id=run.id,
                tenant_id=run.tenant_id,
                action="start",
            ),
        )
    )
    resolver = Resolver(
        StreamFailingRuntime(),
        close_error=RuntimeError("sandbox-delete-unavailable"),
    )

    worker = RunWorker(
        session_factory=factory,
        queue=queue,
        runtime_resolver=resolver,
        consumer_name="test-worker",
    )

    worked = await worker.run_once(block_ms=1)

    assert worked is True
    assert queue.acknowledged == ["exhausted-stream"]
    assert resolver.prepared is not None
    assert resolver.prepared.close_calls == 1
    assert resolver.prepared.detach_calls == 1
    await worker.renew_active_runtimes()
    assert resolver.prepared.renew_calls == 0
    async with factory() as session:
        assert await SqlAlchemyRunCommandRepository(session).is_processed(command.id)
        persisted = await SqlAlchemyRunRepository(session).get(
            tenant_id=run.tenant_id,
            run_id=run.id,
        )
        assert persisted is not None
        assert persisted.status is RunStatus.FAILED
        assert persisted.error_code == "delivery_attempts_exhausted"


@pytest.mark.asyncio
async def test_exhausted_preparation_failure_releases_ownership_without_prepared_runtime(
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
    queue = ExhaustingQueue(
        RunQueueDelivery(
            delivery_id="exhausted-preparation",
            message=RunQueueMessage(
                command_id=command.id,
                run_id=run.id,
                tenant_id=run.tenant_id,
                action="start",
            ),
        )
    )

    assert await RunWorker(
        session_factory=factory,
        queue=queue,
        runtime_resolver=TransientFailingResolver(),
        consumer_name="test-worker",
    ).run_once(block_ms=1)

    async with factory() as session:
        ownership = await SqlAlchemyRuntimeOwnershipRepository(session).get(run_id=run.id)
        assert ownership is not None
        assert ownership.owner_id is None


@pytest.mark.asyncio
async def test_exhausted_malformed_message_is_safely_persisted_then_acknowledged(factory) -> None:
    queue = MalformedQueue()

    worked = await RunWorker(
        session_factory=factory,
        queue=queue,
        runtime_resolver=Resolver(CompletingRuntime()),
        consumer_name="test-worker",
    ).run_once(block_ms=1)

    assert worked is True
    assert queue.acknowledged == ["malformed-1"]
    async with factory() as session:
        records = list((await session.execute(select(RunDeadLetterRecord))).scalars())
        assert len(records) == 1
        assert records[0].error_type == "malformed_queue_message"
        assert "database-password-must-not-persist" not in repr(records[0].raw_fields_summary)


@pytest.mark.asyncio
async def test_verified_malformed_settlement_discards_active_runtime_even_if_close_fails(
    factory,
) -> None:
    run = Run.create(
        tenant_id=uuid4(),
        employee_id=uuid4(),
        employee_version=1,
        created_by=uuid4(),
        input_data={},
    )
    start = RunCommand.create(
        run_id=run.id,
        tenant_id=run.tenant_id,
        action=RunCommandAction.START,
    )
    malformed = RunCommand.create(
        run_id=run.id,
        tenant_id=run.tenant_id,
        action=RunCommandAction.MESSAGE,
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
        commands = SqlAlchemyRunCommandRepository(session)
        await commands.add(start)
        await commands.add(malformed)
        await session.commit()
    queue = ActiveThenMalformedQueue(
        RunQueueDelivery(
            delivery_id="active-start",
            message=RunQueueMessage(
                command_id=start.id,
                run_id=run.id,
                tenant_id=run.tenant_id,
                action="start",
            ),
        ),
        raw_fields={
            "command_id": str(malformed.id),
            "run_id": str(run.id),
            "tenant_id": str(run.tenant_id),
            "action": "message",
            "payload": "not-json",
        },
    )
    resolver = Resolver(
        InteractiveRuntime(),
        close_error=RuntimeError("sandbox-delete-unavailable"),
    )
    worker = RunWorker(
        session_factory=factory,
        queue=queue,
        runtime_resolver=resolver,
        consumer_name="test-worker",
    )

    assert await worker.run_once(block_ms=1)
    assert await worker.run_once(block_ms=1)
    await worker.renew_active_runtimes()

    assert resolver.prepared is not None
    assert resolver.prepared.close_calls == 1
    assert resolver.prepared.detach_calls == 1
    assert resolver.prepared.renew_calls == 0
    assert queue.acknowledged == ["active-start", "active-malformed"]
    async with factory() as session:
        persisted = await SqlAlchemyRunRepository(session).get(
            tenant_id=run.tenant_id,
            run_id=run.id,
        )
        assert persisted is not None and persisted.status is RunStatus.FAILED
        assert await SqlAlchemyRunCommandRepository(session).is_processed(malformed.id)


@pytest.mark.asyncio
async def test_stale_worker_malformed_does_not_discard_runtime_owned_by_new_worker(
    factory,
) -> None:
    run = Run.create(
        tenant_id=uuid4(),
        employee_id=uuid4(),
        employee_version=1,
        created_by=uuid4(),
        input_data={},
    )
    start = RunCommand.create(
        run_id=run.id,
        tenant_id=run.tenant_id,
        action=RunCommandAction.START,
    )
    malformed = RunCommand.create(
        run_id=run.id,
        tenant_id=run.tenant_id,
        action=RunCommandAction.MESSAGE,
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
        commands = SqlAlchemyRunCommandRepository(session)
        await commands.add(start)
        await commands.add(malformed)
        await session.commit()
    queue = ActiveThenMalformedQueue(
        RunQueueDelivery(
            delivery_id="stale-worker-start",
            message=RunQueueMessage(
                command_id=start.id,
                run_id=run.id,
                tenant_id=run.tenant_id,
                action="start",
            ),
        ),
        raw_fields={
            "command_id": str(malformed.id),
            "run_id": str(run.id),
            "tenant_id": str(run.tenant_id),
            "action": "message",
            "payload": "not-json",
        },
    )
    resolver = Resolver(InteractiveRuntime())
    worker = RunWorker(
        session_factory=factory,
        queue=queue,
        runtime_resolver=resolver,
        consumer_name="old-worker",
        runtime_lease_duration=timedelta(seconds=1),
    )

    assert await worker.run_once(block_ms=1)
    async with factory() as session:
        await SqlAlchemyRuntimeOwnershipRepository(session).claim(
            run_id=run.id,
            tenant_id=run.tenant_id,
            owner_id="new-worker",
            now=datetime.now(UTC) + timedelta(seconds=2),
            lease_duration=timedelta(seconds=30),
        )
        await session.commit()
    with pytest.raises(WorkerFenced):
        await worker.run_once(block_ms=1)

    assert resolver.prepared is not None
    assert resolver.prepared.close_calls == 0
    assert resolver.prepared.detach_calls == 1
    assert queue.acknowledged == ["stale-worker-start"]
    with pytest.raises(WorkerFenced):
        await worker.run_once(block_ms=1)
    async with factory() as session:
        persisted = await SqlAlchemyRunRepository(session).get(
            tenant_id=run.tenant_id,
            run_id=run.id,
        )
        assert persisted is not None
        assert persisted.status is RunStatus.RUNNING
        assert not await SqlAlchemyRunCommandRepository(session).is_processed(malformed.id)
        dead_letters = list((await session.execute(select(RunDeadLetterRecord))).scalars())
        assert dead_letters == []
        ownership = await SqlAlchemyRuntimeOwnershipRepository(session).get(run_id=run.id)
        assert ownership is not None and ownership.owner_id == "new-worker"


@pytest.mark.asyncio
async def test_reentered_malformed_uses_stale_dead_letter_target_for_fencing(
    factory,
) -> None:
    stale = Run.create(
        tenant_id=uuid4(),
        employee_id=uuid4(),
        employee_version=1,
        created_by=uuid4(),
        input_data={},
    )
    local = Run.create(
        tenant_id=stale.tenant_id,
        employee_id=stale.employee_id,
        employee_version=1,
        created_by=stale.created_by,
        input_data={},
    )
    stale_start = RunCommand.create(
        run_id=stale.id,
        tenant_id=stale.tenant_id,
        action=RunCommandAction.START,
    )
    local_start = RunCommand.create(
        run_id=local.id,
        tenant_id=local.tenant_id,
        action=RunCommandAction.START,
    )
    stale_malformed = RunCommand.create(
        run_id=stale.id,
        tenant_id=stale.tenant_id,
        action=RunCommandAction.MESSAGE,
    )
    local_spoof = RunCommand.create(
        run_id=local.id,
        tenant_id=local.tenant_id,
        action=RunCommandAction.MESSAGE,
    )
    version = EmployeeVersion(
        id=uuid4(),
        employee_id=stale.employee_id,
        tenant_id=stale.tenant_id,
        version=1,
        definition={"work_mode": "autonomous"},
        published_by=stale.created_by,
        published_at=datetime.now(UTC),
    )
    dead_letter_id = uuid4()
    async with factory() as session:
        runs = SqlAlchemyRunRepository(session)
        await runs.add(stale)
        await runs.add(local)
        await SqlAlchemyEmployeeVersionRepository(session).add(version)
        commands = SqlAlchemyRunCommandRepository(session)
        await commands.add(stale_start)
        await commands.add(local_start)
        await commands.add(stale_malformed)
        await commands.add(local_spoof)
        session.add(
            RunDeadLetterRecord(
                id=dead_letter_id,
                source_stream="agent-platform:runs",
                original_delivery_id="active-malformed",
                original_command_id=stale_malformed.id,
                original_run_id=stale.id,
                tenant_id=stale.tenant_id,
                action=None,
                attempts=5,
                error_type="malformed_queue_message",
                is_malformed=True,
                raw_fields_summary={},
                failed_at=datetime.now(UTC),
                replayed_run_id=None,
                replayed_command_id=None,
                replayed_at=None,
                settled_run_id=None,
                mirrored_at=None,
            )
        )
        await session.commit()
    queue = ActiveThenMalformedQueue(
        RunQueueDelivery(
            delivery_id="stale-start",
            message=RunQueueMessage(
                command_id=stale_start.id,
                run_id=stale.id,
                tenant_id=stale.tenant_id,
                action="start",
            ),
        ),
        raw_fields={
            "command_id": str(local_spoof.id),
            "run_id": str(local.id),
            "tenant_id": str(local.tenant_id),
            "action": "message",
            "payload": "not-json",
        },
    )
    queue.deliveries.append(
        RunQueueDelivery(
            delivery_id="local-start",
            message=RunQueueMessage(
                command_id=local_start.id,
                run_id=local.id,
                tenant_id=local.tenant_id,
                action="start",
            ),
        )
    )
    resolver = Resolver(InteractiveRuntime())
    worker = RunWorker(
        session_factory=factory,
        queue=queue,
        runtime_resolver=resolver,
        consumer_name="stale-worker",
        runtime_lease_duration=timedelta(seconds=1),
    )

    assert await worker.run_once(block_ms=1)
    assert await worker.run_once(block_ms=1)
    async with factory() as session:
        await SqlAlchemyRuntimeOwnershipRepository(session).claim(
            run_id=stale.id,
            tenant_id=stale.tenant_id,
            owner_id="new-worker",
            now=datetime.now(UTC) + timedelta(seconds=2),
            lease_duration=timedelta(seconds=30),
        )
        await session.commit()

    with pytest.raises(WorkerFenced):
        await worker.run_once(block_ms=1)

    stale_prepared, local_prepared = resolver.prepareds
    assert stale_prepared.detach_calls == 1
    assert stale_prepared.close_calls == 0
    assert local_prepared.detach_calls == 0
    assert local_prepared.close_calls == 0
    assert queue.acknowledged == ["stale-start", "local-start"]
    async with factory() as session:
        ownerships = SqlAlchemyRuntimeOwnershipRepository(session)
        stale_ownership = await ownerships.get(run_id=stale.id)
        local_ownership = await ownerships.get(run_id=local.id)
        assert stale_ownership is not None and stale_ownership.owner_id == "new-worker"
        assert local_ownership is not None and local_ownership.owner_id is not None
        persisted_stale = await SqlAlchemyRunRepository(session).get(
            tenant_id=stale.tenant_id,
            run_id=stale.id,
        )
        assert persisted_stale is not None and persisted_stale.status is RunStatus.RUNNING
        commands = SqlAlchemyRunCommandRepository(session)
        assert not await commands.is_processed(stale_malformed.id)
        assert not await commands.is_processed(local_spoof.id)
        dead_letter = await session.get(RunDeadLetterRecord, dead_letter_id)
        assert dead_letter is not None and dead_letter.settled_run_id is None


@pytest.mark.asyncio
async def test_cross_spliced_malformed_does_not_discard_unrelated_active_runtime(factory) -> None:
    active = Run.create(
        tenant_id=uuid4(),
        employee_id=uuid4(),
        employee_version=1,
        created_by=uuid4(),
        input_data={},
    )
    unrelated = Run.create(
        tenant_id=uuid4(),
        employee_id=uuid4(),
        employee_version=1,
        created_by=uuid4(),
        input_data={},
    )
    start = RunCommand.create(
        run_id=active.id,
        tenant_id=active.tenant_id,
        action=RunCommandAction.START,
    )
    unrelated_command = RunCommand.create(
        run_id=unrelated.id,
        tenant_id=unrelated.tenant_id,
        action=RunCommandAction.MESSAGE,
    )
    version = EmployeeVersion(
        id=uuid4(),
        employee_id=active.employee_id,
        tenant_id=active.tenant_id,
        version=1,
        definition={"work_mode": "autonomous"},
        published_by=active.created_by,
        published_at=datetime.now(UTC),
    )
    async with factory() as session:
        runs = SqlAlchemyRunRepository(session)
        await runs.add(active)
        await runs.add(unrelated)
        await SqlAlchemyEmployeeVersionRepository(session).add(version)
        commands = SqlAlchemyRunCommandRepository(session)
        await commands.add(start)
        await commands.add(unrelated_command)
        await session.commit()
    queue = ActiveThenMalformedQueue(
        RunQueueDelivery(
            delivery_id="unrelated-start",
            message=RunQueueMessage(
                command_id=start.id,
                run_id=active.id,
                tenant_id=active.tenant_id,
                action="start",
            ),
        ),
        raw_fields={
            "command_id": str(unrelated_command.id),
            "run_id": str(active.id),
            "tenant_id": str(active.tenant_id),
            "action": "message",
            "payload": "not-json",
        },
    )
    resolver = Resolver(InteractiveRuntime())
    worker = RunWorker(
        session_factory=factory,
        queue=queue,
        runtime_resolver=resolver,
        consumer_name="test-worker",
    )

    assert await worker.run_once(block_ms=1)
    assert await worker.run_once(block_ms=1)
    await worker.renew_active_runtimes()

    assert resolver.prepared is not None
    assert resolver.prepared.close_calls == 0
    assert resolver.prepared.detach_calls == 0
    assert resolver.prepared.renew_calls == 1
    async with factory() as session:
        persisted = await SqlAlchemyRunRepository(session).get(
            tenant_id=active.tenant_id,
            run_id=active.id,
        )
        assert persisted is not None and persisted.status is RunStatus.RUNNING
        assert not await SqlAlchemyRunCommandRepository(session).is_processed(unrelated_command.id)


@pytest.mark.asyncio
async def test_dead_letter_database_failure_never_acknowledges_original_delivery(factory) -> None:
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
    queue = ExhaustingQueue(
        RunQueueDelivery(
            delivery_id="dlq-db-failure",
            message=RunQueueMessage(
                command_id=command.id,
                run_id=run.id,
                tenant_id=run.tenant_id,
                action="start",
            ),
        )
    )

    with pytest.raises(RuntimeError, match="postgres-dlq-unavailable"):
        await RunWorker(
            session_factory=factory,
            queue=queue,
            runtime_resolver=Resolver(StreamFailingRuntime()),
            consumer_name="test-worker",
            dead_letter_service=FailingDeadLetterService(),
        ).run_once(block_ms=1)

    assert queue.acknowledged == []
    async with factory() as session:
        assert not await SqlAlchemyRunCommandRepository(session).is_processed(command.id)


@pytest.mark.asyncio
async def test_real_redis_keeps_exhausted_delivery_pending_when_dlq_database_fails(
    factory,
) -> None:
    redis_url = os.getenv("TEST_REDIS_URL")
    if redis_url is None:
        pytest.skip("需要 TEST_REDIS_URL 才运行真实 Redis DLQ 故障测试")
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
    redis = Redis.from_url(redis_url, decode_responses=True)
    stream_name = f"test:dlq-db-failure:{uuid4()}"
    queue = RedisRunQueue(
        redis,
        stream_name=stream_name,
        group_name="test-workers",
        pending_min_idle_ms=1,
        max_delivery_attempts=1,
    )
    worker = RunWorker(
        session_factory=factory,
        queue=queue,
        runtime_resolver=Resolver(StreamFailingRuntime()),
        consumer_name="test-worker",
        dead_letter_service=FailingDeadLetterService(),
    )
    try:
        await queue.setup()
        await queue.enqueue(
            RunQueueMessage(
                command_id=command.id,
                run_id=run.id,
                tenant_id=run.tenant_id,
                action="start",
            )
        )
        with pytest.raises(RuntimeError, match="postgres-dlq-unavailable"):
            await worker.run_once(block_ms=100)

        pending = await redis.xpending(stream_name, "test-workers")
        assert pending["pending"] == 1
    finally:
        await worker.aclose()
        await redis.delete(stream_name)
        await redis.aclose()


@pytest.mark.asyncio
async def test_ack_failure_redelivery_reuses_committed_dead_letter_and_converges(factory) -> None:
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
            delivery_id="same-pending-entry",
            message=RunQueueMessage(
                command_id=command.id,
                run_id=run.id,
                tenant_id=run.tenant_id,
                action="start",
            ),
        )
        for _ in range(2)
    ]
    queue = AckFailingExhaustingQueue(deliveries)
    worker = RunWorker(
        session_factory=factory,
        queue=queue,
        runtime_resolver=Resolver(StreamFailingRuntime()),
        consumer_name="test-worker",
    )

    with pytest.raises(RuntimeError, match="redis-ack-unavailable"):
        await worker.run_once(block_ms=1)
    assert await worker.run_once(block_ms=1) is True

    assert queue.acknowledged == ["same-pending-entry"]
    async with factory() as session:
        records = list((await session.execute(select(RunDeadLetterRecord))).scalars())
        assert len(records) == 1
        assert await SqlAlchemyRunCommandRepository(session).is_processed(command.id)


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
    assert resolver.prepared.close_calls == 0
    assert resolver.prepared.detach_calls == 1
    assert queue.acknowledged == ["first-start"]
