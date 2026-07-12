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
from agent_platform.runtimes.base import EmployeeRuntime, RuntimeStartRequest


class RuntimeResolver(Protocol):
    def resolve(self, run: Run, definition: dict[str, object]) -> EmployeeRuntime: ...


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
                return
            run = await SqlAlchemyRunRepository(session).get(
                tenant_id=message.tenant_id, run_id=message.run_id
            )
            if run is None:
                raise LookupError(message.run_id)
            version = await SqlAlchemyEmployeeVersionRepository(session).get(
                tenant_id=run.tenant_id,
                employee_id=run.employee_id,
                version=run.employee_version,
            )
            if version is None:
                raise LookupError((run.employee_id, run.employee_version))

        runtime = self._runtime_resolver.resolve(run, version.definition)
        if message.action == "start":
            await self._mark_running(run)
            state = await runtime.start(
                RuntimeStartRequest(
                    run_id=run.id,
                    tenant_id=run.tenant_id,
                    employee_id=run.employee_id,
                    thread_id=run.thread_id,
                    employee_definition=TypeAdapter(dict[str, JsonValue]).validate_python(
                        version.definition
                    ),
                    input_data=run.input_data,
                )
            )
        else:
            await self._invoke_control(runtime, delivery)
            state = await runtime.get_state(run.id)

        history = [event async for event in runtime.stream(run.id)]
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
            await SqlAlchemyRunCommandRepository(session).mark_processed(message.command_id)
            await session.commit()

    async def _mark_running(self, run: Run) -> None:
        async with self._session_factory() as session:
            repository = SqlAlchemyRunRepository(session)
            current = await repository.get(tenant_id=run.tenant_id, run_id=run.id)
            if current is None:
                raise LookupError(run.id)
            await repository.update(current.transition_to(RunStatus.RUNNING))
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
