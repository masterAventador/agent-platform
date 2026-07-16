import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol, TypeVar
from uuid import UUID, uuid4, uuid5

from pydantic import JsonValue, TypeAdapter
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_platform.infrastructure.database.repositories.artifacts import (
    SqlAlchemyFileRepository,
)
from agent_platform.infrastructure.database.repositories.audit import (
    SqlAlchemyToolAuditReader,
)
from agent_platform.infrastructure.database.repositories.conversation_dispatch import (
    build_conversation_run_input,
    create_conversation_run,
)
from agent_platform.infrastructure.database.repositories.conversations import (
    SqlAlchemyConversationMessageRepository,
    SqlAlchemyConversationRepository,
)
from agent_platform.infrastructure.database.repositories.employees import (
    SqlAlchemyEmployeeRepository,
    SqlAlchemyEmployeeVersionRepository,
)
from agent_platform.infrastructure.database.repositories.memory_extraction import (
    extract_run_memories,
)
from agent_platform.infrastructure.database.repositories.runs import (
    SqlAlchemyRunCommandRepository,
    SqlAlchemyRunEventRepository,
    SqlAlchemyRunRepository,
)
from agent_platform.infrastructure.database.repositories.runtime_ownership import (
    RuntimeOwnership,
    RuntimeOwnershipBusy,
    RuntimeOwnershipLost,
    SqlAlchemyRuntimeOwnershipRepository,
)
from agent_platform.infrastructure.queue.dead_letters import (
    DELIVERY_PROCESSING_ERROR_TYPE,
    MALFORMED_MESSAGE_ERROR_TYPE,
    RunDeadLetterService,
)
from agent_platform.infrastructure.queue.redis_streams import (
    MalformedRunQueueMessage,
    RedisRunQueue,
    RunQueueDelivery,
    RunQueueMessage,
)
from agent_platform.platform.artifacts.entities import File
from agent_platform.platform.conversations.entities import (
    Conversation,
    ConversationMessage,
    ConversationMessageRole,
    conversation_followup_run_id,
    limit_conversation_message_content,
)
from agent_platform.platform.dynamic_io import (
    DynamicOutputTooLarge,
    DynamicOutputValidationFailed,
    InvalidDynamicSchema,
    has_effective_output_schema,
    validate_run_output,
)
from agent_platform.platform.employees.entities import (
    EmployeeStatus,
    is_runnable_employee_definition,
)
from agent_platform.platform.runs.commands import RunCommand
from agent_platform.platform.runs.entities import Run, RunStatus
from agent_platform.platform.runs.events import EventType, PlatformEvent
from agent_platform.runtimes.base import (
    EmployeeRuntime,
    PreparedRuntime,
    RuntimeStartRequest,
    RuntimeState,
)
from agent_platform.runtimes.recovery import (
    ApprovalCheckpointRuntime,
    RecoverableEmployeeRuntime,
    RuntimeControlMismatch,
    RuntimeInterrupted,
    RuntimeRecoveryTransient,
    RuntimeRecoveryUnavailable,
    ToolExecutionUncertain,
)
from agent_platform.workers.runtime_composition import PermanentRuntimePreparationError


class RuntimeResolver(Protocol):
    async def resolve(
        self,
        run: Run,
        definition: dict[str, object],
    ) -> PreparedRuntime: ...

    async def recover(
        self,
        run: Run,
        definition: dict[str, object],
    ) -> PreparedRuntime: ...


class RuntimeNotPrepared(Exception):
    """控制命令没有命中当前 Worker 中已启动的 run runtime。"""


class RuntimeAlreadyPrepared(Exception):
    """重复 start 命令不能覆盖仍由当前 Worker 持有的环境。"""


class RuntimeCleanupError(RuntimeError):
    """Sanitized runtime environment cleanup failure."""


class WorkerFenced(RuntimeError):
    """当前 Worker 已失去执行 epoch，必须停止消费。"""


logger = logging.getLogger(__name__)
RuntimeOperationResult = TypeVar("RuntimeOperationResult")
_KNOWLEDGE_EVENT_NAMESPACE = UUID("8f3c1af2-6c1d-4be0-9dbb-1a4a9d8f5f77")


@dataclass(frozen=True, slots=True)
class _PendingRuntimeResult:
    command_id: UUID
    state: RuntimeState
    history: tuple[PlatformEvent, ...] | None


class RunWorker:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        queue: RedisRunQueue,
        runtime_resolver: RuntimeResolver,
        consumer_name: str,
        runtime_lease_duration: timedelta = timedelta(seconds=30),
        cancellation_poll_initial_seconds: float = 0.25,
        cancellation_poll_max_seconds: float = 2.0,
        dead_letter_service: RunDeadLetterService | None = None,
    ) -> None:
        if runtime_lease_duration <= timedelta(0):
            raise ValueError("runtime_lease_duration must be positive")
        if cancellation_poll_initial_seconds <= 0:
            raise ValueError("cancellation_poll_initial_seconds must be positive")
        if cancellation_poll_max_seconds < cancellation_poll_initial_seconds:
            raise ValueError("cancellation_poll_max_seconds must be at least the initial delay")
        self._session_factory = session_factory
        self._queue = queue
        self._runtime_resolver = runtime_resolver
        self._consumer_name = consumer_name
        self._owner_id = str(uuid4())
        self._fenced = False
        self._runtime_lease_duration = runtime_lease_duration
        self._cancellation_poll_initial_seconds = cancellation_poll_initial_seconds
        self._cancellation_poll_max_seconds = cancellation_poll_max_seconds
        self._dead_letters = dead_letter_service or RunDeadLetterService(
            session_factory=session_factory
        )
        self._prepared_runtimes: dict[UUID, PreparedRuntime] = {}
        self._ownerships: dict[UUID, RuntimeOwnership] = {}
        self._active_runs: dict[UUID, Run] = {}
        self._terminal_cleanup_pending: set[UUID] = set()
        self._pending_results: dict[UUID, _PendingRuntimeResult] = {}
        self._recovery_cleanup_pending: dict[UUID, PreparedRuntime] = {}

    async def run_once(self, *, block_ms: int = 5_000) -> bool:
        if self._fenced:
            raise WorkerFenced
        try:
            delivery = await self._queue.dequeue(
                consumer_name=self._consumer_name,
                block_ms=block_ms,
            )
        except MalformedRunQueueMessage as error:
            if not error.exhausted:
                raise
            try:
                dead_letter = await self._dead_letters.record_malformed(
                    delivery_id=error.delivery_id,
                    attempts=error.attempts,
                    error_type=MALFORMED_MESSAGE_ERROR_TYPE,
                    raw_fields=error.raw_fields,
                    source_stream=error.source_stream,
                    ownerships=tuple(self._ownerships.values()),
                )
            except RuntimeOwnershipLost as ownership_error:
                self._fenced = True
                if ownership_error.run_id in self._ownerships:
                    await self._abandon_runtime(ownership_error.run_id)
                raise WorkerFenced from None
            if dead_letter.settled_run_id is not None:
                await self._discard_dead_letter_runtime(dead_letter.settled_run_id)
            await self._queue.acknowledge(error.delivery_id)
            await self._reconcile_dead_letter_mirrors()
            return True
        if delivery is None:
            await self._reconcile_dead_letter_mirrors()
            return False
        try:
            await self._process(delivery)
        except RuntimeOwnershipLost:
            self._fenced = True
            await self._abandon_runtime(delivery.message.run_id)
            raise WorkerFenced from None
        except RuntimeOwnershipBusy:
            await self._abandon_runtime(delivery.message.run_id)
            raise
        except Exception:
            attempts = await self._queue.exhausted_delivery_attempts(delivery.delivery_id)
            if attempts is None:
                raise
            try:
                await self._dead_letters.record_failure(
                    delivery,
                    attempts=attempts,
                    error_type=DELIVERY_PROCESSING_ERROR_TYPE,
                    ownership=self._ownerships.get(delivery.message.run_id),
                )
            except RuntimeOwnershipLost:
                self._fenced = True
                await self._abandon_runtime(delivery.message.run_id)
                raise WorkerFenced from None
            await self._discard_dead_letter_runtime(delivery.message.run_id)
            await self._dispatch_followup_after_dead_letter(delivery.message)
            await self._queue.acknowledge(delivery.delivery_id)
            await self._reconcile_dead_letter_mirrors()
            return True
        await self._queue.acknowledge(delivery.delivery_id)
        await self._reconcile_dead_letter_mirrors()
        return True

    async def recover_incomplete_runs(self, *, limit: int = 100) -> int:
        if limit <= 0:
            raise ValueError("recovery limit must be positive")
        recovered = 0
        after_updated_at: datetime | None = None
        after_run_id: UUID | None = None
        while True:
            async with self._session_factory() as session:
                candidates = await SqlAlchemyRunRepository(session).list_recovery_candidates(
                    limit=limit,
                    after_updated_at=after_updated_at,
                    after_run_id=after_run_id,
                )
            if not candidates:
                break
            for run in candidates:
                recovered += await self._recover_candidate(run)
            last = candidates[-1]
            after_updated_at = last.updated_at
            after_run_id = last.id
        return recovered

    async def _recover_candidate(self, run: Run) -> int:
        if run.id in self._prepared_runtimes:
            return 0
        if run.status is RunStatus.RUNNING:
            await self._claim_ownership(run)
            await self._persist_orphaned_run_failure(
                run,
                error_code=RuntimeInterrupted.code,
            )
            return 0
        async with self._session_factory() as session:
            version = await SqlAlchemyEmployeeVersionRepository(session).get(
                tenant_id=run.tenant_id,
                employee_id=run.employee_id,
                version=run.employee_version,
            )
        if version is None:
            await self._claim_ownership(run)
            await self._persist_orphaned_run_failure(
                run,
                error_code=RuntimeRecoveryUnavailable.code,
            )
            return 0
        try:
            prepared = await self._recover_runtime(run, version.definition)
        except RuntimeRecoveryUnavailable as error:
            await self._persist_orphaned_run_failure(
                run,
                error_code=error.code,
                settle_approval_id=(
                    error.approval_id if isinstance(error, ToolExecutionUncertain) else None
                ),
            )
            await self._close_failed_recovery(run.id)
            await self._run_deferred_recovery_cleanup(error, run_id=run.id)
            return 0
        self._prepared_runtimes[run.id] = prepared
        self._active_runs[run.id] = run
        state = await prepared.runtime.get_state(run.id)
        history = [event async for event in prepared.runtime.stream(run.id)]
        current_approval_id = (
            prepared.runtime.pending_approval_id(run.id)
            if isinstance(prepared.runtime, ApprovalCheckpointRuntime)
            else None
        )
        persisted_status = await self._persist_recovered_snapshot(
            run=run,
            state=state,
            history=history,
            current_approval_id=current_approval_id,
        )
        if self._is_terminal(persisted_status):
            await self._release_runtime(run.id)
            await self._dispatch_conversation_followup_safely(run)
            return 0
        return 1

    async def _reconcile_dead_letter_mirrors(self) -> None:
        try:
            await self._dead_letters.reconcile_mirrors(
                publisher=self._queue,
                limit=100,
            )
        except Exception:
            logger.error("dead_letter_mirror_reconciliation_failed", extra={})

    async def _process(self, delivery: RunQueueDelivery) -> None:
        message = delivery.message
        async with self._session_factory() as session:
            commands = SqlAlchemyRunCommandRepository(session)
            if await commands.is_processed(message.command_id):
                if message.run_id in self._terminal_cleanup_pending:
                    await self._release_runtime(message.run_id)
                # 重投递恢复窗口：核心结算已提交但派生可能未落库，此处幂等补派生
                processed_run = await SqlAlchemyRunRepository(session).get(
                    tenant_id=message.tenant_id, run_id=message.run_id
                )
                if processed_run is not None and self._is_terminal(processed_run.status):
                    await self._dispatch_conversation_followup_safely(processed_run)
                return
            run = await SqlAlchemyRunRepository(session).get(
                tenant_id=message.tenant_id, run_id=message.run_id
            )
            if run is None:
                raise LookupError(message.run_id)
            terminal_noop = self._is_terminal(run.status) and (
                message.action == "start" or run.id not in self._prepared_runtimes
            )
            if terminal_noop:
                await commands.mark_processed(message.command_id)
                await session.commit()
                version = None
            else:
                version = await SqlAlchemyEmployeeVersionRepository(session).get(
                    tenant_id=run.tenant_id,
                    employee_id=run.employee_id,
                    version=run.employee_version,
                )
                if version is None:
                    raise LookupError((run.employee_id, run.employee_version))

        if terminal_noop:
            if message.action == "start":
                await self._release_runtime(run.id)
            await self._dispatch_conversation_followup_safely(run)
            return
        assert version is not None

        cancellation_command_ids: tuple[UUID, ...] = ()
        knowledge_event: PlatformEvent | None = None
        pending_result = self._pending_results.get(run.id)
        if pending_result is not None:
            if pending_result.command_id != message.command_id:
                raise RuntimeAlreadyPrepared(run.id)
            runtime = self._required_runtime(run.id)
            # 事件流重收集时必须重建知识事件；确定性 event_id 保证已持久化时被去重。
            knowledge_event = self._knowledge_event(run, self._prepared_runtimes[run.id])
            state = pending_result.state
            history = list(pending_result.history) if pending_result.history is not None else None
        elif message.action == "start":
            if run.id in self._prepared_runtimes:
                if run.id in self._terminal_cleanup_pending:
                    await self._release_runtime(run.id)
                else:
                    raise RuntimeAlreadyPrepared(run.id)
            await self._claim_ownership(run)
            if await self._settle_pre_start_cancellation(
                run=run,
                start_command_id=message.command_id,
            ):
                await self._dispatch_conversation_followup_safely(run)
                return
            try:
                prepared = await self._runtime_resolver.resolve(run, version.definition)
            except PermanentRuntimePreparationError as error:
                await self._persist_preparation_failure(
                    run=run,
                    message_command_id=message.command_id,
                    error_code=error.code,
                )
                try:
                    await error.cleanup_after_failure()
                except Exception:
                    logger.error(
                        "runtime_preparation_cleanup_failed",
                        extra={"run_id": str(run.id)},
                    )
                return
            self._prepared_runtimes[run.id] = prepared
            self._active_runs[run.id] = run
            runtime = prepared.runtime
            knowledge_event = self._knowledge_event(run, prepared)
            try:
                marked_running = await self._mark_running(
                    run,
                    message_command_id=message.command_id,
                )
            except Exception:
                await self._release_runtime_preserving_error(run.id)
                raise
            if not marked_running:
                await self._release_runtime(run.id)
                return
            try:
                state, cancellation_command_ids = await self._start_cancellable_runtime(
                    run=run,
                    runtime=runtime,
                    request=self._runtime_request(run, prepared),
                )
            except Exception as error:
                cancellation_command_ids = ()
                state = RuntimeState(
                    run_id=run.id,
                    status=RunStatus.FAILED,
                    data={"error_code": "runtime_start_failed"},
                )
                history = [
                    PlatformEvent.create(
                        tenant_id=run.tenant_id,
                        employee_id=run.employee_id,
                        run_id=run.id,
                        sequence=1,
                        event_type=EventType.RUN_STARTED,
                        payload={"thread_id": run.thread_id},
                    ),
                    PlatformEvent.create(
                        tenant_id=run.tenant_id,
                        employee_id=run.employee_id,
                        run_id=run.id,
                        sequence=2,
                        event_type=EventType.RUN_FAILED,
                        payload={
                            "code": "runtime_start_failed",
                            "error_type": type(error).__name__,
                        },
                    ),
                ]
            else:
                history = None
        else:
            cancellation_command_ids = ()
            if run.id not in self._prepared_runtimes:
                if run.status is RunStatus.RUNNING:
                    await self._claim_ownership(run)
                    await self._persist_preparation_failure(
                        run=run,
                        message_command_id=message.command_id,
                        error_code=RuntimeInterrupted.code,
                    )
                    return
                if run.status not in {
                    RunStatus.WAITING_FOR_INPUT,
                    RunStatus.WAITING_FOR_APPROVAL,
                }:
                    raise RuntimeNotPrepared(run.id)
                try:
                    prepared = await self._recover_runtime(run, version.definition)
                except RuntimeRecoveryUnavailable as error:
                    await self._persist_preparation_failure(
                        run=run,
                        message_command_id=message.command_id,
                        error_code=error.code,
                    )
                    await self._close_failed_recovery(run.id)
                    await self._run_deferred_recovery_cleanup(error, run_id=run.id)
                    return
                self._prepared_runtimes[run.id] = prepared
                self._active_runs[run.id] = run
            runtime = self._required_runtime(run.id)
            try:
                if message.action == "cancel":
                    await self._invoke_control(runtime, delivery)
                else:
                    _, cancellation_command_ids = await self._run_cancellable_runtime_operation(
                        run_id=run.id,
                        runtime=runtime,
                        operation=lambda: self._invoke_control(runtime, delivery),
                    )
            except RuntimeControlMismatch:
                await self._persist_control_mismatch(
                    run=run,
                    message_command_id=message.command_id,
                    action=message.action,
                )
                return
            state = await runtime.get_state(run.id)
            history = None

        if history is None:
            try:
                history = [event async for event in runtime.stream(run.id)]
            except Exception:
                self._pending_results[run.id] = _PendingRuntimeResult(
                    command_id=message.command_id,
                    state=state,
                    history=None,
                )
                raise
        if knowledge_event is not None:
            history = self._insert_knowledge_event(history, knowledge_event)

        try:
            persisted_status = await self._persist_runtime_result(
                run=run,
                message_command_id=message.command_id,
                state=state,
                history=history,
                additional_command_ids=cancellation_command_ids,
            )
        except Exception:
            self._pending_results[run.id] = _PendingRuntimeResult(
                command_id=message.command_id,
                state=state,
                history=tuple(history),
            )
            raise
        self._pending_results.pop(run.id, None)
        if self._should_release(run.id, persisted_status):
            await self._release_runtime(run.id)
        if self._is_terminal(persisted_status):
            await self._dispatch_conversation_followup_safely(run)

    async def _persist_runtime_result(
        self,
        *,
        run: Run,
        message_command_id: UUID,
        state: RuntimeState,
        history: list[PlatformEvent],
        additional_command_ids: tuple[UUID, ...] = (),
    ) -> RunStatus:
        conversation_projection: tuple[Run, list[PlatformEvent]] | None = None
        async with self._session_factory() as session:
            await self._assert_owned(session=session, run_id=run.id)
            runs = SqlAlchemyRunRepository(session)
            current = await runs.get_for_update(tenant_id=run.tenant_id, run_id=run.id)
            if current is None:
                raise LookupError(run.id)
            if self._is_terminal(current.status):
                commands = SqlAlchemyRunCommandRepository(session)
                await commands.mark_processed(message_command_id)
                for command_id in additional_command_ids:
                    if command_id != message_command_id:
                        await commands.mark_processed(command_id)
                await session.commit()
                return current.status
            commands = SqlAlchemyRunCommandRepository(session)
            pending_cancellations = await commands.unprocessed_cancel_commands(run_id=run.id)
            if pending_cancellations and state.status is not RunStatus.CANCELLED:
                state = RuntimeState(
                    run_id=run.id,
                    status=RunStatus.CANCELLED,
                    data={},
                )
                history = [
                    event
                    for event in history
                    if event.type not in {EventType.RUN_COMPLETED, EventType.RUN_FAILED}
                ]
                if not any(event.type is EventType.RUN_CANCELLED for event in history):
                    history.append(
                        PlatformEvent.create(
                            tenant_id=run.tenant_id,
                            employee_id=run.employee_id,
                            run_id=run.id,
                            sequence=len(history) + 1,
                            event_type=EventType.RUN_CANCELLED,
                            payload={"status": "cancelled"},
                        )
                    )
                additional_command_ids = tuple(
                    dict.fromkeys(
                        (
                            *additional_command_ids,
                            *(command.id for command in pending_cancellations),
                        )
                    )
                )
            state, history = await self._apply_output_schema_guard(
                session=session,
                run=current,
                state=state,
                history=history,
            )
            events = SqlAlchemyRunEventRepository(session)
            await self._append_new_history(events=events, run_id=run.id, history=history)
            conversation_projection = (current, history)
            if current.status != state.status:
                if current.status in {
                    RunStatus.WAITING_FOR_INPUT,
                    RunStatus.WAITING_FOR_APPROVAL,
                } and state.status not in {
                    RunStatus.FAILED,
                    RunStatus.CANCELLED,
                }:
                    current = current.transition_to(RunStatus.RUNNING)
                current = current.transition_to(
                    state.status,
                    error_code=str(state.data.get("error_code", "runtime_failed")),
                    error_message=str(state.data.get("error_message", "")) or None,
                )
                await runs.update(current)
            await commands.mark_processed(message_command_id)
            for command_id in additional_command_ids:
                if command_id != message_command_id:
                    await commands.mark_processed(command_id)
            await session.commit()
        if conversation_projection is not None:
            await self._append_conversation_messages_for_history_safely(
                run=conversation_projection[0],
                history=conversation_projection[1],
            )
            await self._extract_run_memories_safely(
                run=conversation_projection[0],
                history=conversation_projection[1],
            )
        return state.status

    async def _start_cancellable_runtime(
        self,
        *,
        run: Run,
        runtime: EmployeeRuntime,
        request: RuntimeStartRequest,
    ) -> tuple[RuntimeState, tuple[UUID, ...]]:
        state, cancellation_command_ids = await self._run_cancellable_runtime_operation(
            run_id=run.id,
            runtime=runtime,
            operation=lambda: runtime.start(request),
            check_for_cancel_before_operation=False,
        )
        if state is None or cancellation_command_ids:
            state = await runtime.get_state(run.id)
        return state, cancellation_command_ids

    async def _run_cancellable_runtime_operation(
        self,
        *,
        run_id: UUID,
        runtime: EmployeeRuntime,
        operation: Callable[[], Awaitable[RuntimeOperationResult]],
        check_for_cancel_before_operation: bool = True,
    ) -> tuple[RuntimeOperationResult | None, tuple[UUID, ...]]:
        cancellation_commands = (
            await self._pending_cancel_commands(run_id) if check_for_cancel_before_operation else []
        )
        if cancellation_commands:
            await runtime.cancel(run_id)
            return None, tuple(command.id for command in cancellation_commands)

        async def invoke_operation() -> RuntimeOperationResult:
            return await operation()

        operation_task = asyncio.create_task(invoke_operation())
        await asyncio.sleep(0)
        poll_delay = self._cancellation_poll_initial_seconds
        try:
            while not operation_task.done():
                cancellation_commands = await self._pending_cancel_commands(run_id)
                if cancellation_commands:
                    await runtime.cancel(run_id)
                    break
                try:
                    await asyncio.wait_for(
                        asyncio.shield(operation_task),
                        timeout=poll_delay,
                    )
                except TimeoutError:
                    poll_delay = min(
                        poll_delay * 2,
                        self._cancellation_poll_max_seconds,
                    )

            try:
                result = await operation_task
            except asyncio.CancelledError:
                if not cancellation_commands:
                    raise
                result = None
            if not cancellation_commands:
                cancellation_commands = await self._pending_cancel_commands(run_id)
                if cancellation_commands:
                    await runtime.cancel(run_id)
            return result, tuple(command.id for command in cancellation_commands)
        finally:
            if not operation_task.done():
                operation_task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await operation_task

    async def _pending_cancel_commands(self, run_id: UUID) -> list[RunCommand]:
        async with self._session_factory() as session:
            return await SqlAlchemyRunCommandRepository(session).unprocessed_cancel_commands(
                run_id=run_id
            )

    async def _settle_pre_start_cancellation(
        self,
        *,
        run: Run,
        start_command_id: UUID,
    ) -> bool:
        async with self._session_factory() as session:
            await self._assert_owned(session=session, run_id=run.id)
            runs = SqlAlchemyRunRepository(session)
            current = await runs.get_for_update(tenant_id=run.tenant_id, run_id=run.id)
            if current is None:
                raise LookupError(run.id)
            commands = SqlAlchemyRunCommandRepository(session)
            pending_cancellations = await commands.unprocessed_cancel_commands(run_id=run.id)
            if not pending_cancellations:
                return False
            if not self._is_terminal(current.status):
                await runs.update(current.transition_to(RunStatus.CANCELLED))
                events = SqlAlchemyRunEventRepository(session)
                await events.append(
                    PlatformEvent.create(
                        tenant_id=run.tenant_id,
                        employee_id=run.employee_id,
                        run_id=run.id,
                        sequence=await events.next_sequence(run_id=run.id),
                        event_type=EventType.RUN_CANCELLED,
                        payload={"status": "cancelled"},
                    )
                )
            await commands.mark_processed(start_command_id)
            for command in pending_cancellations:
                if command.id != start_command_id:
                    await commands.mark_processed(command.id)
            ownership = self._ownerships[run.id]
            await SqlAlchemyRuntimeOwnershipRepository(session).release(
                run_id=run.id,
                owner_id=ownership.owner_id or "",
                epoch=ownership.epoch,
            )
            await session.commit()
        self._ownerships.pop(run.id, None)
        return True

    async def _mark_running(
        self,
        run: Run,
        *,
        message_command_id: UUID,
    ) -> bool:
        async with self._session_factory() as session:
            await self._assert_owned(session=session, run_id=run.id)
            repository = SqlAlchemyRunRepository(session)
            current = await repository.get_for_update(
                tenant_id=run.tenant_id,
                run_id=run.id,
            )
            if current is None:
                raise LookupError(run.id)
            if self._is_terminal(current.status):
                await SqlAlchemyRunCommandRepository(session).mark_processed(message_command_id)
                await session.commit()
                return False
            await repository.update(current.transition_to(RunStatus.RUNNING))
            await session.commit()
            return True

    async def _persist_control_mismatch(
        self,
        *,
        run: Run,
        message_command_id: UUID,
        action: str,
    ) -> None:
        async with self._session_factory() as session:
            await self._assert_owned(session=session, run_id=run.id)
            runs = SqlAlchemyRunRepository(session)
            current = await runs.get_for_update(tenant_id=run.tenant_id, run_id=run.id)
            if current is None:
                raise LookupError(run.id)
            commands = SqlAlchemyRunCommandRepository(session)
            await commands.mark_processed(message_command_id)
            events = SqlAlchemyRunEventRepository(session)
            await events.append(
                PlatformEvent.create(
                    tenant_id=run.tenant_id,
                    employee_id=run.employee_id,
                    run_id=run.id,
                    sequence=await events.next_sequence(run_id=run.id),
                    event_type=EventType.RUN_PROGRESS,
                    payload={
                        "action": action,
                        "status": "control_rejected",
                        "code": "runtime_control_mismatch",
                    },
                )
            )
            await session.commit()

    async def _persist_preparation_failure(
        self,
        *,
        run: Run,
        message_command_id: UUID,
        error_code: str,
    ) -> None:
        conversation_projection: tuple[Run, list[PlatformEvent]] | None = None
        async with self._session_factory() as session:
            ownership = self._ownerships.get(run.id)
            await self._assert_owned(session=session, run_id=run.id)
            assert ownership is not None
            runs = SqlAlchemyRunRepository(session)
            current = await runs.get_for_update(tenant_id=run.tenant_id, run_id=run.id)
            if current is None:
                raise LookupError(run.id)
            if self._is_terminal(current.status):
                await SqlAlchemyRunCommandRepository(session).mark_processed(message_command_id)
                await SqlAlchemyRuntimeOwnershipRepository(session).release(
                    run_id=run.id,
                    owner_id=ownership.owner_id or "",
                    epoch=ownership.epoch,
                )
                await session.commit()
                self._ownerships.pop(run.id, None)
                await self._dispatch_conversation_followup_safely(run)
                return
            await runs.update(
                current.transition_to(
                    RunStatus.FAILED,
                    error_code=error_code,
                    error_message=None,
                )
            )
            events = SqlAlchemyRunEventRepository(session)
            failed_event = PlatformEvent.create(
                tenant_id=run.tenant_id,
                employee_id=run.employee_id,
                run_id=run.id,
                sequence=await events.next_sequence(run_id=run.id),
                event_type=EventType.RUN_FAILED,
                payload={"code": error_code},
            )
            await events.append(failed_event)
            conversation_projection = (run, [failed_event])
            await SqlAlchemyRunCommandRepository(session).mark_processed(message_command_id)
            await SqlAlchemyRuntimeOwnershipRepository(session).release(
                run_id=run.id,
                owner_id=ownership.owner_id or "",
                epoch=ownership.epoch,
            )
            await session.commit()
        self._ownerships.pop(run.id, None)
        if conversation_projection is not None:
            await self._append_conversation_messages_for_history_safely(
                run=conversation_projection[0],
                history=conversation_projection[1],
            )
        await self._dispatch_conversation_followup_safely(run)

    def _required_runtime(self, run_id: UUID) -> EmployeeRuntime:
        try:
            return self._prepared_runtimes[run_id].runtime
        except KeyError as error:
            raise RuntimeNotPrepared(run_id) from error

    def _should_release(self, run_id: UUID, status: RunStatus) -> bool:
        return run_id in self._prepared_runtimes and self._is_terminal(status)

    @staticmethod
    def _is_terminal(status: RunStatus) -> bool:
        return status in {
            RunStatus.COMPLETED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }

    async def _release_runtime(self, run_id: UUID) -> None:
        prepared = self._prepared_runtimes.get(run_id)
        if prepared is None:
            await self._release_ownership(run_id)
            return
        try:
            await prepared.close()
        except Exception:
            self._terminal_cleanup_pending.add(run_id)
            raise RuntimeCleanupError("Runtime environment cleanup failed") from None
        self._prepared_runtimes.pop(run_id, None)
        self._active_runs.pop(run_id, None)
        self._terminal_cleanup_pending.discard(run_id)
        self._pending_results.pop(run_id, None)
        await self._release_ownership(run_id)

    async def _release_runtime_preserving_error(self, run_id: UUID) -> None:
        try:
            await self._release_runtime(run_id)
        except Exception:
            logger.error(
                "runtime_environment_cleanup_failed",
                extra={"run_id": str(run_id)},
            )

    async def _discard_dead_letter_runtime(self, run_id: UUID) -> None:
        prepared = self._prepared_runtimes.pop(run_id, None)
        self._active_runs.pop(run_id, None)
        self._terminal_cleanup_pending.discard(run_id)
        self._pending_results.pop(run_id, None)
        self._ownerships.pop(run_id, None)
        if prepared is None:
            return
        try:
            await prepared.close()
            return
        except Exception:
            logger.error(
                "dead_letter_runtime_cleanup_failed",
                extra={"run_id": str(run_id)},
            )
        try:
            await prepared.detach()
        except Exception:
            logger.error(
                "dead_letter_runtime_detach_failed",
                extra={"run_id": str(run_id)},
            )

    async def renew_active_runtimes(self) -> None:
        failures = 0
        for run_id, prepared in list(self._prepared_runtimes.items()):
            if run_id in self._terminal_cleanup_pending:
                continue
            try:
                run = self._active_runs.get(run_id)
                if run is not None:
                    await self._claim_ownership(run)
                await prepared.renew()
            except (RuntimeOwnershipBusy, RuntimeOwnershipLost):
                await self._abandon_runtime(run_id)
                raise RuntimeCleanupError("Runtime ownership was lost") from None
            except Exception:
                failures += 1
                logger.error(
                    "runtime_environment_renewal_failed",
                    extra={"run_id": str(run_id)},
                )
                run = self._active_runs.get(run_id)
                if run is not None:
                    await self._persist_renewal_failure(run)
                await self._release_runtime_preserving_error(run_id)
        if failures:
            raise RuntimeCleanupError("Runtime environment renewal failed")

    async def aclose(self) -> None:
        for run_id in list(self._prepared_runtimes):
            try:
                run = self._active_runs.get(run_id)
                if run is not None and not self._is_terminal(run.status):
                    await self._abandon_runtime(run_id)
                else:
                    await self._release_runtime(run_id)
            except Exception:
                logger.error(
                    "runtime_environment_shutdown_cleanup_failed",
                    extra={"run_id": str(run_id)},
                )
                self._prepared_runtimes.pop(run_id, None)
                self._active_runs.pop(run_id, None)
        close = getattr(self._runtime_resolver, "aclose", None)
        if callable(close):
            try:
                await close()
            except Exception:
                logger.error("runtime_resolver_shutdown_failed", extra={})

    async def _persist_renewal_failure(self, run: Run) -> None:
        conversation_projection: tuple[Run, list[PlatformEvent]] | None = None
        async with self._session_factory() as session:
            await self._assert_owned(session=session, run_id=run.id)
            runs = SqlAlchemyRunRepository(session)
            current = await runs.get_for_update(tenant_id=run.tenant_id, run_id=run.id)
            if current is None:
                return
            if self._is_terminal(current.status):
                # 先提交释放 Run 行锁，再与其余终态分支一致地在事务外补派生
                await session.commit()
                await self._dispatch_conversation_followup_safely(run)
                return
            error_code = "sandbox_lease_renewal_failed"
            await runs.update(
                current.transition_to(
                    RunStatus.FAILED,
                    error_code=error_code,
                    error_message=None,
                )
            )
            events = SqlAlchemyRunEventRepository(session)
            failed_event = PlatformEvent.create(
                tenant_id=run.tenant_id,
                employee_id=run.employee_id,
                run_id=run.id,
                sequence=await events.next_sequence(run_id=run.id),
                event_type=EventType.RUN_FAILED,
                payload={"code": error_code},
            )
            await events.append(failed_event)
            conversation_projection = (run, [failed_event])
            await session.commit()
        if conversation_projection is not None:
            await self._append_conversation_messages_for_history_safely(
                run=conversation_projection[0],
                history=conversation_projection[1],
            )
        await self._dispatch_conversation_followup_safely(run)

    async def _persist_orphaned_run_failure(
        self,
        run: Run,
        *,
        error_code: str,
        settle_approval_id: UUID | None = None,
    ) -> None:
        conversation_projection: tuple[Run, list[PlatformEvent]] | None = None
        async with self._session_factory() as session:
            await self._assert_owned(session=session, run_id=run.id)
            runs = SqlAlchemyRunRepository(session)
            current = await runs.get_for_update(tenant_id=run.tenant_id, run_id=run.id)
            if current is None:
                raise LookupError(run.id)
            if self._is_terminal(current.status):
                ownership = self._ownerships[run.id]
                await SqlAlchemyRuntimeOwnershipRepository(session).release(
                    run_id=run.id,
                    owner_id=ownership.owner_id or "",
                    epoch=ownership.epoch,
                )
                await session.commit()
                self._ownerships.pop(run.id, None)
                await self._dispatch_conversation_followup_safely(run)
                return
            await runs.update(
                current.transition_to(
                    RunStatus.FAILED,
                    error_code=error_code,
                    error_message=None,
                )
            )
            events = SqlAlchemyRunEventRepository(session)
            failed_event = PlatformEvent.create(
                tenant_id=run.tenant_id,
                employee_id=run.employee_id,
                run_id=run.id,
                sequence=await events.next_sequence(run_id=run.id),
                event_type=EventType.RUN_FAILED,
                payload={"code": error_code},
            )
            await events.append(failed_event)
            conversation_projection = (run, [failed_event])
            if settle_approval_id is not None:
                await self._settle_approval_commands(
                    session=session,
                    run_id=run.id,
                    approval_ids={settle_approval_id},
                )
            ownership = self._ownerships[run.id]
            await SqlAlchemyRuntimeOwnershipRepository(session).release(
                run_id=run.id,
                owner_id=ownership.owner_id or "",
                epoch=ownership.epoch,
            )
            await session.commit()
        self._ownerships.pop(run.id, None)
        if conversation_projection is not None:
            await self._append_conversation_messages_for_history_safely(
                run=conversation_projection[0],
                history=conversation_projection[1],
            )
        await self._dispatch_conversation_followup_safely(run)

    async def _persist_recovered_snapshot(
        self,
        *,
        run: Run,
        state: RuntimeState,
        history: list[PlatformEvent],
        current_approval_id: UUID | None,
    ) -> RunStatus:
        conversation_projection: tuple[Run, list[PlatformEvent]] | None = None
        async with self._session_factory() as session:
            await self._assert_owned(session=session, run_id=run.id)
            runs = SqlAlchemyRunRepository(session)
            current = await runs.get_for_update(tenant_id=run.tenant_id, run_id=run.id)
            if current is None:
                raise LookupError(run.id)
            if self._is_terminal(current.status):
                await session.commit()
                return current.status
            events = SqlAlchemyRunEventRepository(session)
            existing = await events.list(run_id=run.id, after_sequence=0)
            existing_approvals = {
                str(event.payload.get("approval_id"))
                for event in existing
                if event.type is EventType.APPROVAL_REQUIRED
                and event.payload.get("approval_id") is not None
            }
            state, history = await self._apply_output_schema_guard(
                session=session,
                run=current,
                state=state,
                history=history,
            )
            await self._append_new_history(events=events, run_id=run.id, history=history)
            conversation_projection = (current, history)
            stale_approval_ids = {
                UUID(value) for value in existing_approvals if value != str(current_approval_id)
            }
            if stale_approval_ids:
                await self._settle_approval_commands(
                    session=session,
                    run_id=run.id,
                    approval_ids=stale_approval_ids,
                )
            if current.status != state.status:
                if current.status in {
                    RunStatus.WAITING_FOR_INPUT,
                    RunStatus.WAITING_FOR_APPROVAL,
                } and state.status not in {RunStatus.FAILED, RunStatus.CANCELLED}:
                    current = current.transition_to(RunStatus.RUNNING)
                current = current.transition_to(
                    state.status,
                    error_code=str(state.data.get("error_code", "runtime_failed")),
                    error_message=str(state.data.get("error_message", "")) or None,
                )
                await runs.update(current)
            await session.commit()
        if conversation_projection is not None:
            await self._append_conversation_messages_for_history_safely(
                run=conversation_projection[0],
                history=conversation_projection[1],
            )
            await self._extract_run_memories_safely(
                run=conversation_projection[0],
                history=conversation_projection[1],
            )
        return state.status

    async def _apply_output_schema_guard(
        self,
        *,
        session: AsyncSession,
        run: Run,
        state: RuntimeState,
        history: list[PlatformEvent],
    ) -> tuple[RuntimeState, list[PlatformEvent]]:
        if state.status is not RunStatus.COMPLETED:
            return state, history

        version = await SqlAlchemyEmployeeVersionRepository(session).get(
            tenant_id=run.tenant_id,
            employee_id=run.employee_id,
            version=run.employee_version,
        )
        if version is None:
            return state, history

        output_schema = version.definition.get("output_schema")
        if not isinstance(output_schema, Mapping):
            return state, history
        if not has_effective_output_schema(output_schema):
            return state, history

        try:
            validate_run_output(
                output_schema=output_schema,
                value=state.data.get("output"),
            )
        except (
            DynamicOutputTooLarge,
            DynamicOutputValidationFailed,
            InvalidDynamicSchema,
        ) as error:
            error_code = "output_schema_validation_failed"
            payload = self._output_schema_failure_payload(error)
            filtered_history = [
                event
                for event in history
                if event.type not in {EventType.MESSAGE_OUTPUT, EventType.RUN_COMPLETED}
            ]
            filtered_history.append(
                PlatformEvent.create(
                    tenant_id=run.tenant_id,
                    employee_id=run.employee_id,
                    run_id=run.id,
                    sequence=len(filtered_history) + 1,
                    event_type=EventType.RUN_FAILED,
                    payload=payload,
                )
            )
            return (
                RuntimeState(
                    run_id=run.id,
                    status=RunStatus.FAILED,
                    data={
                        "error_code": error_code,
                        "error_message": str(payload["message"]),
                    },
                ),
                filtered_history,
            )

        return state, history

    @staticmethod
    def _output_schema_failure_payload(
        error: DynamicOutputTooLarge | DynamicOutputValidationFailed | InvalidDynamicSchema,
    ) -> dict[str, JsonValue]:
        payload: dict[str, JsonValue] = {
            "code": "output_schema_validation_failed",
            "message": "运行输出不符合数字员工发布版本的输出 Schema",
        }
        if isinstance(error, DynamicOutputValidationFailed):
            payload["errors"] = list(error.errors)
        elif isinstance(error, DynamicOutputTooLarge):
            payload["reason"] = "output_too_large"
        elif isinstance(error, InvalidDynamicSchema):
            payload["reason"] = "invalid_output_schema"
            payload["schema_path"] = list(error.issue.path)
        return payload

    @staticmethod
    async def _append_new_history(
        *,
        events: SqlAlchemyRunEventRepository,
        run_id: UUID,
        history: list[PlatformEvent],
    ) -> None:
        existing = await events.list(run_id=run_id, after_sequence=0)
        existing_ids = {event.event_id for event in existing}
        existing_approvals = {
            str(event.payload.get("approval_id"))
            for event in existing
            if event.type is EventType.APPROVAL_REQUIRED
            and event.payload.get("approval_id") is not None
        }
        sequence = await events.next_sequence(run_id=run_id)
        for event in history:
            if event.event_id in existing_ids:
                continue
            if (
                event.type is EventType.APPROVAL_REQUIRED
                and str(event.payload.get("approval_id")) in existing_approvals
            ):
                continue
            await events.append(event.model_copy(update={"sequence": sequence}))
            sequence += 1

    async def _extract_run_memories_safely(
        self,
        *,
        run: Run,
        history: list[PlatformEvent],
    ) -> None:
        """任务完成后的长期记忆受控提取：独立安全事务，失败只记录日志，
        不阻断已完成的 Run 结算（与会话投影相同的隔离模式）。"""

        try:
            await extract_run_memories(
                session_factory=self._session_factory,
                run=run,
                history=history,
            )
        except Exception as error:
            logger.error(
                "memory_extraction_failed",
                extra={
                    "run_id": str(run.id),
                    "error_type": type(error).__name__,
                },
            )

    async def _append_conversation_messages_for_history_safely(
        self,
        *,
        run: Run,
        history: list[PlatformEvent],
    ) -> None:
        if run.conversation_id is None or not history:
            return
        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            async with self._session_factory() as session:
                try:
                    await self._append_conversation_messages_for_history(
                        session=session,
                        run=run,
                        history=history,
                    )
                    await session.commit()
                    return
                except IntegrityError as error:
                    await session.rollback()
                    log_extra = {
                        "run_id": str(run.id),
                        "conversation_id": str(run.conversation_id),
                        "attempt": attempt,
                        "error_type": type(error).__name__,
                    }
                    if attempt < max_attempts:
                        logger.warning("conversation_projection_retry", extra=log_extra)
                        continue
                    logger.error("conversation_projection_failed", extra=log_extra)
                    return
                except Exception as error:
                    await session.rollback()
                    logger.error(
                        "conversation_projection_failed",
                        extra={
                            "run_id": str(run.id),
                            "conversation_id": str(run.conversation_id),
                            "attempt": attempt,
                            "error_type": type(error).__name__,
                        },
                    )
                    return

    @staticmethod
    async def _append_conversation_messages_for_history(
        *,
        session: AsyncSession,
        run: Run,
        history: list[PlatformEvent],
    ) -> None:
        if run.conversation_id is None:
            return
        conversations = SqlAlchemyConversationRepository(session)
        conversation = await conversations.get(
            tenant_id=run.tenant_id,
            conversation_id=run.conversation_id,
        )
        if conversation is None:
            return
        messages = SqlAlchemyConversationMessageRepository(session)
        for event in history:
            role: ConversationMessageRole | None = None
            content: str | None = None
            if event.type is EventType.MESSAGE_OUTPUT:
                role = ConversationMessageRole.ASSISTANT
                content = limit_conversation_message_content(
                    RunWorker._message_content(event.payload.get("content"))
                )
            elif event.type is EventType.RUN_FAILED:
                role = ConversationMessageRole.ERROR
                content = limit_conversation_message_content(
                    RunWorker._message_content(
                        event.payload.get("code") or event.payload.get("error_code") or "run_failed"
                    )
                )
            if role is None or content is None:
                continue
            if await messages.exists_for_run_event(
                tenant_id=run.tenant_id,
                conversation_id=run.conversation_id,
                run_id=run.id,
                role=role,
                content=content,
            ):
                continue
            message = ConversationMessage.create(
                tenant_id=run.tenant_id,
                conversation_id=run.conversation_id,
                sequence=await messages.next_sequence(
                    tenant_id=run.tenant_id,
                    conversation_id=run.conversation_id,
                ),
                role=role,
                content=content,
                run_id=run.id,
            )
            await messages.add(message)
            await conversations.update(conversation.touch(message.created_at))
            conversation = conversation.touch(message.created_at)

    @staticmethod
    def _message_content(value: JsonValue | object) -> str:
        if isinstance(value, str):
            return value
        return json.dumps(TypeAdapter(JsonValue).validate_python(value), ensure_ascii=False)

    async def _dispatch_followup_after_dead_letter(self, message: RunQueueMessage) -> None:
        """死信结算同样属于轮次终态：尽力补派生，失败不影响死信处理。"""
        try:
            async with self._session_factory() as session:
                run = await SqlAlchemyRunRepository(session).get(
                    tenant_id=message.tenant_id, run_id=message.run_id
                )
        except Exception:
            logger.error(
                "conversation_followup_dead_letter_lookup_failed",
                extra={"run_id": str(message.run_id)},
            )
            return
        if run is not None and self._is_terminal(run.status):
            await self._dispatch_conversation_followup_safely(run)

    async def _dispatch_conversation_followup_safely(self, run: Run) -> None:
        """终态结算后的自动续跑派生：独立安全事务，失败只记录日志，不影响已完成的结算。"""
        if run.conversation_id is None:
            return
        try:
            await self._dispatch_conversation_followup(
                tenant_id=run.tenant_id,
                conversation_id=run.conversation_id,
            )
        except Exception as error:
            logger.error(
                "conversation_followup_dispatch_failed",
                extra={
                    "run_id": str(run.id),
                    "conversation_id": str(run.conversation_id),
                    "error_type": type(error).__name__,
                },
            )

    async def _dispatch_conversation_followup(
        self,
        *,
        tenant_id: UUID,
        conversation_id: UUID,
    ) -> None:
        async with self._session_factory() as session:
            conversations = SqlAlchemyConversationRepository(session)
            # 会话行锁与 API 追加消息的决策互斥：结算瞬间写入的排队消息
            # 要么在本事务可见（被派生消费），要么其请求已看到无活跃 Run（自行开新轮）。
            conversation = await conversations.get_for_update(
                tenant_id=tenant_id,
                conversation_id=conversation_id,
            )
            if conversation is None:
                return
            commands = SqlAlchemyRunCommandRepository(session)
            followups = await commands.unprocessed_followup_commands_for_conversation(
                tenant_id=tenant_id,
                conversation_id=conversation_id,
            )
            if not followups:
                return
            active = await conversations.latest_active_run(
                tenant_id=tenant_id,
                conversation_id=conversation_id,
            )
            if active is not None:
                # 已有下一轮在跑（API 抢先开新轮或已派生），意图留待该轮结算时消费
                return
            messages = SqlAlchemyConversationMessageRepository(session)
            stale_commands: list[RunCommand] = []
            pending: list[tuple[RunCommand, ConversationMessage]] = []
            for command in followups:
                message = await self._followup_message(
                    messages=messages,
                    tenant_id=tenant_id,
                    conversation_id=conversation_id,
                    command=command,
                )
                if message is None:
                    stale_commands.append(command)
                else:
                    pending.append((command, message))
            if not pending:
                for command in stale_commands:
                    await commands.mark_processed(command.id)
                await session.commit()
                return
            pending.sort(key=lambda pair: pair[1].sequence)
            employee_version = await self._runnable_conversation_employee_version(
                session=session,
                conversation=conversation,
            )
            if employee_version is None:
                # 员工当前不可运行：受控跳过并保留意图，员工恢复后由后续结算继续消费
                logger.warning(
                    "conversation_followup_skipped_employee_not_runnable",
                    extra={
                        "conversation_id": str(conversation_id),
                        "employee_id": str(conversation.employee_id),
                    },
                )
                for command in stale_commands:
                    await commands.mark_processed(command.id)
                await session.commit()
                return
            trigger_command, trigger_message = pending[0]
            created_by = self._followup_requested_by(
                command=trigger_command,
                conversation=conversation,
            )
            attachment_files = await self._followup_attachment_files(
                session=session,
                tenant_id=tenant_id,
                messages=[message for _, message in pending],
            )
            # 确定性幂等键兜底覆盖整个创建区段：仓储 add 会立即 flush，
            # uuid5 主键冲突在 create_conversation_run 内部就会抛出，
            # 不能只包住最终 commit，否则并发派生会被误报为派生失败。
            try:
                followup_run = await create_conversation_run(
                    database_session=session,
                    conversation=conversation,
                    employee_version=employee_version,
                    created_by=created_by,
                    input_data=await build_conversation_run_input(
                        messages=messages,
                        conversation=conversation,
                        content=pending[-1][1].content,
                    ),
                    attachment_files=attachment_files,
                    run_id=conversation_followup_run_id(
                        conversation_id=conversation.id,
                        trigger_message_id=trigger_message.id,
                    ),
                )
                for _, message in pending:
                    await messages.bind_run(
                        tenant_id=tenant_id,
                        conversation_id=conversation_id,
                        message_id=message.id,
                        run_id=followup_run.id,
                    )
                for command in (*(command for command, _ in pending), *stale_commands):
                    await commands.mark_processed(command.id)
                await session.commit()
            except IntegrityError:
                # 并发派生同一触发消息：按已派生处理，不算失败
                await session.rollback()
                logger.warning(
                    "conversation_followup_already_derived",
                    extra={
                        "conversation_id": str(conversation_id),
                        "trigger_message_id": str(trigger_message.id),
                    },
                )

    @staticmethod
    async def _followup_message(
        *,
        messages: SqlAlchemyConversationMessageRepository,
        tenant_id: UUID,
        conversation_id: UUID,
        command: RunCommand,
    ) -> ConversationMessage | None:
        try:
            message_id = UUID(str(command.payload.get("message_id")))
        except ValueError:
            return None
        message = await messages.get(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            message_id=message_id,
        )
        if (
            message is None
            or message.run_id is not None
            or message.role is not ConversationMessageRole.USER
        ):
            return None
        return message

    @staticmethod
    def _followup_requested_by(*, command: RunCommand, conversation: Conversation) -> UUID:
        try:
            return UUID(str(command.payload.get("requested_by")))
        except ValueError:
            return conversation.created_by

    @staticmethod
    async def _runnable_conversation_employee_version(
        *,
        session: AsyncSession,
        conversation: Conversation,
    ) -> int | None:
        employee = await SqlAlchemyEmployeeRepository(session).get(
            tenant_id=conversation.tenant_id,
            employee_id=conversation.employee_id,
        )
        if (
            employee is None
            or employee.status is not EmployeeStatus.PUBLISHED
            or employee.published_version is None
        ):
            return None
        version = await SqlAlchemyEmployeeVersionRepository(session).get(
            tenant_id=conversation.tenant_id,
            employee_id=conversation.employee_id,
            version=employee.published_version,
        )
        if version is None or not is_runnable_employee_definition(version.definition):
            return None
        capabilities = version.definition.get("capabilities")
        if not isinstance(capabilities, dict) or capabilities.get("conversation") is not True:
            return None
        return employee.published_version

    @staticmethod
    async def _followup_attachment_files(
        *,
        session: AsyncSession,
        tenant_id: UUID,
        messages: list[ConversationMessage],
    ) -> list[File]:
        files: list[File] = []
        repository = SqlAlchemyFileRepository(session)
        seen: set[UUID] = set()
        for message in messages:
            for file_id in message.attachment_ids:
                if file_id in seen:
                    continue
                seen.add(file_id)
                file = await repository.get(tenant_id=tenant_id, file_id=file_id)
                if file is not None:
                    files.append(file)
        return files

    @staticmethod
    async def _settle_approval_commands(
        *,
        session: AsyncSession,
        run_id: UUID,
        approval_ids: set[UUID],
    ) -> None:
        commands = SqlAlchemyRunCommandRepository(session)
        for command in await commands.unprocessed_approval_commands(run_id=run_id):
            raw_approval_id = command.payload.get("approval_id")
            try:
                approval_id = UUID(str(raw_approval_id))
            except ValueError:
                continue
            if approval_id in approval_ids:
                await commands.mark_processed(command.id)

    async def _started_pending_approval_invocation(self, run: Run) -> UUID | None:
        async with self._session_factory() as session:
            commands = await SqlAlchemyRunCommandRepository(session).unprocessed_approval_commands(
                run_id=run.id
            )
            audits = SqlAlchemyToolAuditReader(session)
            for command in commands:
                try:
                    approval_id = UUID(str(command.payload.get("approval_id")))
                except ValueError:
                    continue
                if await audits.has_started(
                    tenant_id=run.tenant_id,
                    run_id=run.id,
                    invocation_id=approval_id,
                ):
                    return approval_id
        return None

    async def _recover_runtime(
        self,
        run: Run,
        definition: dict[str, object],
    ) -> PreparedRuntime:
        recover = getattr(self._runtime_resolver, "recover", None)
        if not callable(recover):
            raise RuntimeRecoveryUnavailable
        await self._claim_ownership(run)
        prepared: PreparedRuntime | None = None
        try:
            prepared = await recover(run, definition)
            runtime = prepared.runtime
            if not isinstance(runtime, RecoverableEmployeeRuntime):
                raise RuntimeRecoveryUnavailable
            started_approval_id = await self._started_pending_approval_invocation(run)
            try:
                state = await runtime.recover(
                    self._runtime_request(run, prepared),
                    run.status,
                )
            except RuntimeRecoveryUnavailable:
                if started_approval_id is not None:
                    raise ToolExecutionUncertain(approval_id=started_approval_id) from None
                raise
            if state.status is RunStatus.WAITING_FOR_APPROVAL and isinstance(
                runtime, ApprovalCheckpointRuntime
            ):
                approval_id = runtime.pending_approval_id(run.id)
                if approval_id is not None:
                    async with self._session_factory() as session:
                        started = await SqlAlchemyToolAuditReader(session).has_started(
                            tenant_id=run.tenant_id, run_id=run.id, invocation_id=approval_id
                        )
                    if started:
                        raise ToolExecutionUncertain(approval_id=approval_id)
            return prepared
        except RuntimeRecoveryUnavailable:
            if prepared is not None:
                self._recovery_cleanup_pending[run.id] = prepared
            raise
        except Exception:
            try:
                if prepared is not None:
                    await prepared.detach()
            except Exception:
                logger.error(
                    "runtime_recovery_detach_failed",
                    extra={"run_id": str(run.id)},
                )
            finally:
                await self._release_ownership(run.id)
            raise RuntimeRecoveryTransient from None

    @staticmethod
    def _runtime_request(run: Run, prepared: PreparedRuntime) -> RuntimeStartRequest:
        input_data = dict(run.input_data)
        knowledge_context = getattr(prepared, "knowledge_context", None)
        if knowledge_context is not None:
            input_data["knowledge_context"] = knowledge_context.as_input_payload()
        # 记忆是数据不是指令：只进入 input_data，不改写员工定义或系统指令，
        # 避免记忆内容中的提示注入文本被放大为系统指令级文本。
        memory_context = getattr(prepared, "memory_context", None)
        if memory_context is not None:
            input_data["memory_context"] = memory_context.as_input_payload()
        return RuntimeStartRequest(
            run_id=run.id,
            tenant_id=run.tenant_id,
            user_id=run.created_by,
            employee_id=run.employee_id,
            thread_id=run.thread_id,
            employee_definition=TypeAdapter(dict[str, JsonValue]).validate_python(
                prepared.employee_definition
            ),
            input_data=TypeAdapter(dict[str, JsonValue]).validate_python(input_data),
        )

    @staticmethod
    def _knowledge_event(run: Run, prepared: PreparedRuntime) -> PlatformEvent | None:
        knowledge_context = getattr(prepared, "knowledge_context", None)
        if knowledge_context is None:
            return None
        payload = {
            "citation_count": len(knowledge_context.citations),
            **knowledge_context.as_input_payload(),
        }
        event = PlatformEvent.create(
            tenant_id=run.tenant_id,
            employee_id=run.employee_id,
            run_id=run.id,
            sequence=1,
            event_type=EventType.KNOWLEDGE_RETRIEVED,
            payload=TypeAdapter(dict[str, JsonValue]).validate_python(payload),
        )
        # 重投递会重新生成本事件；event_id 必须按 run 确定，依托持久化 event_id 去重。
        return event.model_copy(
            update={
                "event_id": uuid5(
                    _KNOWLEDGE_EVENT_NAMESPACE,
                    f"{EventType.KNOWLEDGE_RETRIEVED.value}:{run.id}",
                )
            }
        )

    @staticmethod
    def _insert_knowledge_event(
        history: list[PlatformEvent],
        knowledge_event: PlatformEvent,
    ) -> list[PlatformEvent]:
        if any(event.type is EventType.KNOWLEDGE_RETRIEVED for event in history):
            return history
        started_index = next(
            (index for index, event in enumerate(history) if event.type is EventType.RUN_STARTED),
            -1,
        )
        insert_at = started_index + 1 if started_index >= 0 else 0
        return [*history[:insert_at], knowledge_event, *history[insert_at:]]

    async def _claim_ownership(self, run: Run) -> RuntimeOwnership:
        async with self._session_factory() as session:
            ownership = await SqlAlchemyRuntimeOwnershipRepository(session).claim(
                run_id=run.id,
                tenant_id=run.tenant_id,
                owner_id=self._owner_id,
                now=datetime.now(UTC),
                lease_duration=self._runtime_lease_duration,
            )
            await session.commit()
        self._ownerships[run.id] = ownership
        return ownership

    async def _assert_owned(self, *, session: AsyncSession, run_id: UUID) -> None:
        ownership = self._ownerships.get(run_id)
        if ownership is None:
            raise RuntimeOwnershipLost(run_id)
        await SqlAlchemyRuntimeOwnershipRepository(session).assert_owned(
            run_id=run_id,
            owner_id=ownership.owner_id or "",
            epoch=ownership.epoch,
            now=datetime.now(UTC),
        )

    async def _release_ownership(self, run_id: UUID) -> None:
        ownership = self._ownerships.get(run_id)
        if ownership is None:
            return
        async with self._session_factory() as session:
            await SqlAlchemyRuntimeOwnershipRepository(session).release(
                run_id=run_id,
                owner_id=ownership.owner_id or "",
                epoch=ownership.epoch,
            )
            await session.commit()
        self._ownerships.pop(run_id, None)

    async def _abandon_runtime(self, run_id: UUID) -> None:
        prepared = self._prepared_runtimes.pop(run_id, None)
        if prepared is not None:
            try:
                await prepared.detach()
            except Exception:
                logger.error(
                    "runtime_environment_detach_failed",
                    extra={"run_id": str(run_id)},
                )
        self._active_runs.pop(run_id, None)
        self._pending_results.pop(run_id, None)
        await self._release_ownership(run_id)

    async def _close_failed_recovery(self, run_id: UUID) -> None:
        prepared = self._recovery_cleanup_pending.pop(run_id, None)
        if prepared is None:
            return
        try:
            await prepared.close()
        except Exception:
            logger.error(
                "failed_recovery_environment_cleanup_failed",
                extra={"run_id": str(run_id)},
            )

    @staticmethod
    async def _run_deferred_recovery_cleanup(
        error: RuntimeRecoveryUnavailable,
        *,
        run_id: UUID,
    ) -> None:
        try:
            await error.cleanup_after_failure()
        except Exception:
            logger.error(
                "failed_recovery_environment_cleanup_failed",
                extra={"run_id": str(run_id)},
            )

    @staticmethod
    async def _invoke_control(runtime: EmployeeRuntime, delivery: RunQueueDelivery) -> None:
        message = delivery.message
        if message.action == "cancel":
            await runtime.cancel(message.run_id)
        elif message.action == "resume":
            await runtime.resume(message.run_id)
        elif message.action == "approve":
            await runtime.approve(message.run_id, UUID(str(message.payload["approval_id"])))
        elif message.action == "reject":
            await runtime.reject(
                message.run_id,
                UUID(str(message.payload["approval_id"])),
                str(message.payload.get("reason")) if message.payload.get("reason") else None,
            )
        elif message.action == "message":
            await runtime.send_message(message.run_id, str(message.payload["message"]))
        else:
            raise ValueError(message.action)
