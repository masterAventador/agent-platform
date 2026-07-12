import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError
from redis.asyncio import Redis
from redis.exceptions import ResponseError

if TYPE_CHECKING:
    from agent_platform.infrastructure.queue.dead_letters import RunDeadLetter


_MIRROR_DEAD_LETTER_SCRIPT = """
local existing = redis.call('HGET', KEYS[2], ARGV[1])
if existing then
    return existing
end
local stream_id = redis.call(
    'XADD', KEYS[1], '*',
    'dead_letter_id', ARGV[1],
    'source_stream', ARGV[2],
    'original_delivery_id', ARGV[3],
    'command_id', ARGV[4],
    'run_id', ARGV[5],
    'tenant_id', ARGV[6],
    'action', ARGV[7],
    'attempts', ARGV[8],
    'error_type', ARGV[9],
    'failed_at', ARGV[10]
)
redis.call('HSET', KEYS[2], ARGV[1], stream_id)
return stream_id
"""


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
    source_stream: str = "agent-platform:runs"


class MalformedRunQueueMessage(Exception):
    def __init__(
        self,
        *,
        delivery_id: str,
        attempts: int,
        exhausted: bool,
        raw_fields: dict[str, str],
        source_stream: str = "agent-platform:runs",
    ) -> None:
        super().__init__("malformed queue message")
        self.delivery_id = delivery_id
        self.attempts = attempts
        self.exhausted = exhausted
        self.raw_fields = raw_fields
        self.source_stream = source_stream


class RedisRunQueue:
    def __init__(
        self,
        redis: Redis,
        *,
        stream_name: str = "agent-platform:runs",
        group_name: str = "agent-platform-workers",
        pending_min_idle_ms: int = 1_000,
        dead_letter_stream_name: str = "agent-platform:runs:dlq",
        max_delivery_attempts: int = 5,
    ) -> None:
        self._redis = redis
        self._stream_name = stream_name
        self._group_name = group_name
        self._pending_min_idle_ms = pending_min_idle_ms
        self._dead_letter_stream_name = dead_letter_stream_name
        self._max_delivery_attempts = max_delivery_attempts
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
                return await self._delivery(claimed_entries[0])
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
        return await self._delivery(entries[0])

    async def _delivery(self, entry: tuple[str, dict[str, str]]) -> RunQueueDelivery:
        delivery_id, fields = entry
        try:
            message = RunQueueMessage.model_validate(
                {
                    **fields,
                    "payload": json.loads(fields.get("payload", "{}")),
                }
            )
        except (json.JSONDecodeError, ValidationError, TypeError, ValueError):
            attempts = await self.delivery_attempts(str(delivery_id))
            raise MalformedRunQueueMessage(
                delivery_id=str(delivery_id),
                attempts=attempts,
                exhausted=attempts >= self._max_delivery_attempts,
                raw_fields=fields,
                source_stream=self._stream_name,
            ) from None
        return RunQueueDelivery(
            delivery_id=str(delivery_id),
            message=message,
            source_stream=self._stream_name,
        )

    async def acknowledge(self, delivery_id: str) -> None:
        await self._redis.xack(self._stream_name, self._group_name, delivery_id)

    async def delivery_attempts(self, delivery_id: str) -> int:
        pending = await self._redis.xpending_range(
            self._stream_name,
            self._group_name,
            min=delivery_id,
            max=delivery_id,
            count=1,
        )
        if not pending:
            return 0
        return int(pending[0]["times_delivered"])

    async def exhausted_delivery_attempts(self, delivery_id: str) -> int | None:
        attempts = await self.delivery_attempts(delivery_id)
        return attempts if attempts >= self._max_delivery_attempts else None

    async def publish_dead_letter(self, record: "RunDeadLetter") -> None:
        await self._redis.eval(
            _MIRROR_DEAD_LETTER_SCRIPT,
            2,
            self._dead_letter_stream_name,
            f"{self._dead_letter_stream_name}:dedupe",
            str(record.id),
            record.source_stream,
            record.original_delivery_id,
            str(record.original_command_id or ""),
            str(record.original_run_id or ""),
            str(record.tenant_id or ""),
            record.action or "",
            str(record.attempts),
            record.error_type,
            record.failed_at.isoformat(),
        )
