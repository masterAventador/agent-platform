import json
from dataclasses import dataclass
from typing import Literal, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue
from redis.asyncio import Redis
from redis.exceptions import ResponseError


class RunQueueMessage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    command_id: UUID
    run_id: UUID
    tenant_id: UUID
    action: Literal["start", "resume", "cancel", "message", "approve", "reject"]
    payload: dict[str, JsonValue] = Field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RunQueueDelivery:
    delivery_id: str
    message: RunQueueMessage


class RedisRunQueue:
    def __init__(
        self,
        redis: Redis,
        *,
        stream_name: str = "agent-platform:runs",
        group_name: str = "agent-platform-workers",
        pending_min_idle_ms: int = 1_000,
    ) -> None:
        self._redis = redis
        self._stream_name = stream_name
        self._group_name = group_name
        self._pending_min_idle_ms = pending_min_idle_ms
        self._claim_cursor = "0-0"

    async def setup(self) -> None:
        try:
            await self._redis.xgroup_create(
                self._stream_name,
                self._group_name,
                id="0",
                mkstream=True,
            )
        except ResponseError as error:
            if "BUSYGROUP" not in str(error):
                raise

    async def enqueue(self, message: RunQueueMessage) -> str:
        delivery_id = await self._redis.xadd(
            self._stream_name,
            {
                "command_id": str(message.command_id),
                "run_id": str(message.run_id),
                "tenant_id": str(message.tenant_id),
                "action": message.action,
                "payload": json.dumps(message.payload, ensure_ascii=False),
            },
        )
        return str(delivery_id)

    async def dequeue(
        self,
        *,
        consumer_name: str,
        block_ms: int = 5_000,
    ) -> RunQueueDelivery | None:
        while True:
            claimed = await self._redis.xautoclaim(
                self._stream_name,
                self._group_name,
                consumer_name,
                self._pending_min_idle_ms,
                self._claim_cursor,
                count=1,
            )
            self._claim_cursor = str(claimed[0])
            claimed_entries = cast(
                list[tuple[str, dict[str, str]]],
                claimed[1],
            )
            if claimed_entries:
                return self._delivery(claimed_entries[0])
            if self._claim_cursor == "0-0":
                break

        raw_streams = await self._redis.xreadgroup(
            self._group_name,
            consumer_name,
            {self._stream_name: ">"},
            count=1,
            block=block_ms,
        )
        streams = cast(
            list[tuple[str, list[tuple[str, dict[str, str]]]]],
            raw_streams,
        )
        if not streams:
            return None
        _, entries = streams[0]
        return self._delivery(entries[0])

    @staticmethod
    def _delivery(entry: tuple[str, dict[str, str]]) -> RunQueueDelivery:
        delivery_id, fields = entry
        return RunQueueDelivery(
            delivery_id=str(delivery_id),
            message=RunQueueMessage.model_validate(
                {
                    **fields,
                    "payload": json.loads(fields.get("payload", "{}")),
                }
            ),
        )

    async def acknowledge(self, delivery_id: str) -> None:
        await self._redis.xack(self._stream_name, self._group_name, delivery_id)
