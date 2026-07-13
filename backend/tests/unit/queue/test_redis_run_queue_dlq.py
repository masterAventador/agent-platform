from datetime import UTC, datetime
from uuid import uuid4

import pytest

from agent_platform.infrastructure.queue.dead_letters import RunDeadLetter
from agent_platform.infrastructure.queue.redis_streams import (
    MalformedRunQueueMessage,
    RedisRunQueue,
    RunQueueDelivery,
    RunQueueMessage,
)


class FakeRedis:
    def __init__(self) -> None:
        self.pending_deliveries = 1
        self.eval_calls: list[tuple[object, ...]] = []
        self.range_entries: list[tuple[str, dict[str, str]]] = []
        self.eval_result: object = 1

    async def xpending_range(self, *args: object, **kwargs: object) -> list[dict[str, object]]:
        del args, kwargs
        return [{"times_delivered": self.pending_deliveries}]

    async def eval(self, *args: object) -> object:
        self.eval_calls.append(args)
        if isinstance(self.eval_result, Exception):
            raise self.eval_result
        return self.eval_result

    async def xrange(self, *args: object, **kwargs: object) -> list[tuple[str, dict[str, str]]]:
        del args, kwargs
        return self.range_entries


def make_message() -> RunQueueMessage:
    return RunQueueMessage(
        command_id=uuid4(),
        run_id=uuid4(),
        tenant_id=uuid4(),
        action="start",
        payload={"safe": "value"},
    )


@pytest.mark.asyncio
async def test_delivery_attempts_come_from_redis_pending_metadata() -> None:
    redis = FakeRedis()
    redis.pending_deliveries = 4
    queue = RedisRunQueue(redis, stream_name="runs", group_name="workers")  # type: ignore[arg-type]

    attempts = await queue.delivery_attempts("10-0")

    assert attempts == 4


@pytest.mark.asyncio
async def test_failure_below_limit_stays_pending_without_writing_dlq() -> None:
    redis = FakeRedis()
    redis.pending_deliveries = 2
    queue = RedisRunQueue(  # type: ignore[arg-type]
        redis,
        stream_name="runs",
        group_name="workers",
        dead_letter_stream_name="runs:dlq",
        max_delivery_attempts=3,
    )
    delivery = RunQueueDelivery(delivery_id="10-0", message=make_message())

    exhausted = await queue.exhausted_delivery_attempts(delivery.delivery_id)

    assert exhausted is None
    assert redis.eval_calls == []


@pytest.mark.asyncio
async def test_malformed_message_exposes_only_bounded_raw_fields_after_max_attempts() -> None:
    redis = FakeRedis()
    redis.pending_deliveries = 3
    raw = {
        "command_id": "not-a-uuid",
        "run_id": str(uuid4()),
        "tenant_id": str(uuid4()),
        "action": "attacker-action",
        "payload": "not-json",
    }

    async def claim(*args: object, **kwargs: object) -> list[object]:
        del args, kwargs
        return ["0-0", [("10-0", raw)], []]

    redis.xautoclaim = claim  # type: ignore[attr-defined]
    queue = RedisRunQueue(  # type: ignore[arg-type]
        redis,
        stream_name="runs",
        group_name="workers",
        max_delivery_attempts=3,
    )

    with pytest.raises(MalformedRunQueueMessage) as caught:
        await queue.dequeue(consumer_name="worker", block_ms=1)

    assert caught.value.delivery_id == "10-0"
    assert caught.value.attempts == 3
    assert caught.value.exhausted is True
    assert caught.value.raw_fields == raw


@pytest.mark.asyncio
async def test_redis_mirror_is_idempotent_metadata_only() -> None:
    redis = FakeRedis()
    redis.eval_result = "20-0"
    queue = RedisRunQueue(  # type: ignore[arg-type]
        redis,
        stream_name="runs",
        group_name="workers",
        dead_letter_stream_name="runs:dlq",
    )
    record = RunDeadLetter(
        id=uuid4(),
        source_stream="runs",
        original_delivery_id="10-0",
        original_command_id=uuid4(),
        original_run_id=uuid4(),
        tenant_id=uuid4(),
        action="start",
        attempts=5,
        error_type="delivery_processing_failed",
        is_malformed=False,
        raw_fields_summary={},
        failed_at=datetime.now(UTC),
        replayed_run_id=None,
        replayed_command_id=None,
        replayed_at=None,
        settled_run_id=None,
        mirrored_at=None,
    )

    await queue.publish_dead_letter(record)

    call = redis.eval_calls[0]
    assert call[1:4] == (2, "runs:dlq", "runs:dlq:dedupe")
    serialized = repr(call)
    assert str(record.id) in serialized
    assert str(record.original_command_id) in serialized
    assert "payload" not in serialized
    assert "must-not-leak" not in serialized
