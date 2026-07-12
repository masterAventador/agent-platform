from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_platform.infrastructure.database.repositories.runs import (
    SqlAlchemyRunCommandRepository,
)
from agent_platform.infrastructure.queue.redis_streams import RedisRunQueue, RunQueueMessage


class RunCommandDispatcher:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        queue: RedisRunQueue,
    ) -> None:
        self._session_factory = session_factory
        self._queue = queue

    async def dispatch_pending(self, *, limit: int = 100) -> int:
        dispatched = 0
        async with self._session_factory() as session:
            repository = SqlAlchemyRunCommandRepository(session)
            for command in await repository.pending(limit=limit):
                try:
                    await self._queue.enqueue(
                        RunQueueMessage(
                            command_id=command.id,
                            run_id=command.run_id,
                            tenant_id=command.tenant_id,
                            action=command.action.value,
                            payload=command.payload,
                        )
                    )
                except Exception as error:
                    await repository.mark_failed(command.id, type(error).__name__)
                    await session.commit()
                    continue
                await repository.mark_dispatched(command.id)
                await session.commit()
                dispatched += 1
        return dispatched
