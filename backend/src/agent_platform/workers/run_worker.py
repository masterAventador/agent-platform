import logging
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from pydantic import JsonValue, TypeAdapter
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_platform.infrastructure.database.repositories.employees import (
    SqlAlchemyEmployeeVersionRepository,
)
from agent_platform.infrastructure.database.repositories.runs import (
    SqlAlchemyRunCommandRepository,
    SqlAlchemyRunEventRepository,
    SqlAlchemyRunRepository,
)
from agent_platform.infrastructure.queue.redis_streams import RedisRunQueue, RunQueueDelivery
from agent_platform.platform.runs.entities import Run, RunStatus
from agent_platform.platform.runs.events import EventType, PlatformEvent
from agent_platform.runtimes.base import (
    EmployeeRuntime,
    PreparedRuntime,
    RuntimeStartRequest,
    RuntimeState,
)
from agent_platform.workers.runtime_composition import PermanentRuntimePreparationError


class RuntimeResolver(Protocol):
    async def resolve(
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


logger = logging.getLogger(__name__)


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
    ) -> None:
        self._session_factory = session_factory
        self._queue = queue
        self._runtime_resolver = runtime_resolver
        self._consumer_name = consumer_name
        self._prepared_runtimes: dict[UUID, PreparedRuntime] = {}
        self._active_runs: dict[UUID, Run] = {}
        self._terminal_cleanup_pending: set[UUID] = set()
        self._pending_results: dict[UUID, _PendingRuntimeResult] = {}

    async def run_once(self, *, block_ms: int = 5_000) -> bool:
        delivery = await self._queue.dequeue(consumer_name=self._consumer_name, block_ms=block_ms)
        if delivery is None:
            return False
        await self._process(delivery)
        await self._queue.acknowledge(delivery.delivery_id)
        return True

    async def _process(self, delivery: RunQueueDelivery) -> None:
        message = delivery.message
        async with self._session_factory() as session:
            commands = SqlAlchemyRunCommandRepository(session)
            if await commands.is_processed(message.command_id):
                if message.run_id in self._terminal_cleanup_pending:
                    await self._release_runtime(message.run_id)
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
            return
        assert version is not None

        pending_result = self._pending_results.get(run.id)
        if pending_result is not None:
            if pending_result.command_id != message.command_id:
                raise RuntimeAlreadyPrepared(run.id)
            runtime = self._required_runtime(run.id)
            state = pending_result.state
            history = list(pending_result.history) if pending_result.history is not None else None
        elif message.action == "start":
            if run.id in self._prepared_runtimes:
                if run.id in self._terminal_cleanup_pending:
                    await self._release_runtime(run.id)
                else:
                    raise RuntimeAlreadyPrepared(run.id)
            try:
                prepared = await self._runtime_resolver.resolve(run, version.definition)
            except PermanentRuntimePreparationError as error:
                await self._persist_preparation_failure(
                    run=run,
                    message_command_id=message.command_id,
                    error_code=error.code,
                )
                return
            self._prepared_runtimes[run.id] = prepared
            self._active_runs[run.id] = run
            runtime = prepared.runtime
            try:
                await self._mark_running(run)
            except Exception:
                await self._release_runtime_preserving_error(run.id)
                raise
            try:
                state = await runtime.start(
                    RuntimeStartRequest(
                        run_id=run.id,
                        tenant_id=run.tenant_id,
                        user_id=run.created_by,
                        employee_id=run.employee_id,
                        thread_id=run.thread_id,
                        employee_definition=TypeAdapter(dict[str, JsonValue]).validate_python(
                            prepared.employee_definition
                        ),
                        input_data=run.input_data,
                    )
                )
            except Exception as error:
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
            runtime = self._required_runtime(run.id)
            await self._invoke_control(runtime, delivery)
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

        try:
            await self._persist_runtime_result(
                run=run,
                message_command_id=message.command_id,
                state=state,
                history=history,
            )
        except Exception:
            self._pending_results[run.id] = _PendingRuntimeResult(
                command_id=message.command_id,
                state=state,
                history=tuple(history),
            )
            raise
        self._pending_results.pop(run.id, None)
        if self._should_release(run.id, state.status):
            await self._release_runtime(run.id)

    async def _persist_runtime_result(
        self,
        *,
        run: Run,
        message_command_id: UUID,
        state: RuntimeState,
        history: list[PlatformEvent],
    ) -> None:
        async with self._session_factory() as session:
            runs = SqlAlchemyRunRepository(session)
            current = await runs.get(tenant_id=run.tenant_id, run_id=run.id)
            if current is None:
                raise LookupError(run.id)
            events = SqlAlchemyRunEventRepository(session)
            existing_event_ids = {
                event.event_id for event in await events.list(run_id=run.id, after_sequence=0)
            }
            sequence = await events.next_sequence(run_id=run.id)
            for event in history:
                if event.event_id in existing_event_ids:
                    continue
                await events.append(event.model_copy(update={"sequence": sequence}))
                sequence += 1
            if current.status != state.status:
                current = current.transition_to(
                    state.status,
                    error_code=str(state.data.get("error_code", "runtime_failed")),
                    error_message=str(state.data.get("error_message", "")) or None,
                )
                await runs.update(current)
            await SqlAlchemyRunCommandRepository(session).mark_processed(message_command_id)
            await session.commit()

    async def _mark_running(self, run: Run) -> None:
        async with self._session_factory() as session:
            repository = SqlAlchemyRunRepository(session)
            current = await repository.get(tenant_id=run.tenant_id, run_id=run.id)
            if current is None:
                raise LookupError(run.id)
            await repository.update(current.transition_to(RunStatus.RUNNING))
            await session.commit()

    async def _persist_preparation_failure(
        self,
        *,
        run: Run,
        message_command_id: UUID,
        error_code: str,
    ) -> None:
        async with self._session_factory() as session:
            runs = SqlAlchemyRunRepository(session)
            current = await runs.get(tenant_id=run.tenant_id, run_id=run.id)
            if current is None:
                raise LookupError(run.id)
            await runs.update(
                current.transition_to(
                    RunStatus.FAILED,
                    error_code=error_code,
                    error_message=None,
                )
            )
            events = SqlAlchemyRunEventRepository(session)
            await events.append(
                PlatformEvent.create(
                    tenant_id=run.tenant_id,
                    employee_id=run.employee_id,
                    run_id=run.id,
                    sequence=await events.next_sequence(run_id=run.id),
                    event_type=EventType.RUN_FAILED,
                    payload={"code": error_code},
                )
            )
            await SqlAlchemyRunCommandRepository(session).mark_processed(message_command_id)
            await session.commit()

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

    async def _release_runtime_preserving_error(self, run_id: UUID) -> None:
        try:
            await self._release_runtime(run_id)
        except Exception:
            logger.error(
                "runtime_environment_cleanup_failed",
                extra={"run_id": str(run_id)},
            )

    async def renew_active_runtimes(self) -> None:
        failures = 0
        for run_id, prepared in list(self._prepared_runtimes.items()):
            try:
                await prepared.renew()
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
        async with self._session_factory() as session:
            runs = SqlAlchemyRunRepository(session)
            current = await runs.get(tenant_id=run.tenant_id, run_id=run.id)
            if current is None or current.status in {
                RunStatus.COMPLETED,
                RunStatus.FAILED,
                RunStatus.CANCELLED,
            }:
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
            await events.append(
                PlatformEvent.create(
                    tenant_id=run.tenant_id,
                    employee_id=run.employee_id,
                    run_id=run.id,
                    sequence=await events.next_sequence(run_id=run.id),
                    event_type=EventType.RUN_FAILED,
                    payload={"code": error_code},
                )
            )
            await session.commit()

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
