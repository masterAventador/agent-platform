from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer


@asynccontextmanager
async def postgres_checkpointer(
    connection_url: str,
) -> AsyncIterator[AsyncPostgresSaver]:
    serializer = JsonPlusSerializer(allowed_msgpack_modules=())
    async with AsyncPostgresSaver.from_conn_string(
        connection_url,
        serde=serializer,
    ) as checkpointer:
        yield checkpointer
