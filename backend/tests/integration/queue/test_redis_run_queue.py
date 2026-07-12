import asyncio
import os
from uuid import uuid4

import pytest
from redis.asyncio import Redis

from agent_platform.infrastructure.queue.redis_streams import (
    MalformedRunQueueMessage,
    RedisRunQueue,
    RunQueueMessage,
)


@pytest.mark.asyncio
async def test_redis_stream_queue_enqueues_consumes_and_acknowledges() -> None:
    redis_url = os.getenv("TEST_REDIS_URL")
    if redis_url is None:
        pytest.skip("需要 TEST_REDIS_URL 才运行真实 Redis 队列测试")

    redis = Redis.from_url(redis_url, decode_responses=True)
    stream_name = f"test:runs:{uuid4()}"
    queue = RedisRunQueue(
        redis,
        stream_name=stream_name,
        group_name="test-workers",
        pending_min_idle_ms=0,
    )
    await queue.setup()
    message = RunQueueMessage(
        command_id=uuid4(),
        run_id=uuid4(),
        tenant_id=uuid4(),
        action="start",
        payload={"message": "你好"},
    )

    await queue.enqueue(message)
    delivery = await queue.dequeue(consumer_name="worker-1", block_ms=100)

    assert delivery is not None
    assert delivery.message == message
    claimed = await queue.dequeue(consumer_name="worker-2", block_ms=100)
    assert claimed is not None
    assert claimed.delivery_id == delivery.delivery_id
    assert claimed.message == message

    await queue.acknowledge(claimed.delivery_id)
    pending = await redis.xpending(stream_name, "test-workers")
    assert pending["pending"] == 0

    await redis.delete(stream_name)
    await redis.aclose()


@pytest.mark.asyncio
async def test_real_redis_persists_delivery_attempts_across_queue_restarts() -> None:
    redis_url = os.getenv("TEST_REDIS_URL")
    if redis_url is None:
        pytest.skip("需要 TEST_REDIS_URL 才运行真实 Redis DLQ 测试")

    redis = Redis.from_url(redis_url, decode_responses=True)
    suffix = uuid4()
    stream_name = f"test:runs:{suffix}"
    group_name = "test-workers"
    message = RunQueueMessage(
        command_id=uuid4(),
        run_id=uuid4(),
        tenant_id=uuid4(),
        action="start",
        payload={"task": "retry safely"},
    )
    try:
        first_queue = RedisRunQueue(
            redis,
            stream_name=stream_name,
            group_name=group_name,
            pending_min_idle_ms=1,
            max_delivery_attempts=3,
        )
        await first_queue.setup()
        await first_queue.enqueue(message)
        first = await first_queue.dequeue(consumer_name="worker-before-restart", block_ms=100)
        assert first is not None
        assert await first_queue.delivery_attempts(first.delivery_id) == 1

        await asyncio.sleep(0.01)
        restarted_queue = RedisRunQueue(
            redis,
            stream_name=stream_name,
            group_name=group_name,
            pending_min_idle_ms=1,
            max_delivery_attempts=3,
        )
        second = await restarted_queue.dequeue(
            consumer_name="worker-after-restart",
            block_ms=100,
        )
        assert second is not None
        assert await restarted_queue.delivery_attempts(second.delivery_id) == 2

        await asyncio.sleep(0.01)
        third = await restarted_queue.dequeue(consumer_name="worker-third", block_ms=100)
        assert third is not None
        assert await restarted_queue.delivery_attempts(third.delivery_id) == 3

        assert await restarted_queue.exhausted_delivery_attempts(third.delivery_id) == 3
        await restarted_queue.acknowledge(third.delivery_id)
    finally:
        await redis.delete(stream_name)
        await redis.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_field,invalid_value",
    [
        ("payload", "not-json"),
        ("command_id", "not-a-uuid"),
        ("action", "not-an-action"),
    ],
)
async def test_real_redis_malformed_entries_reach_bounded_exhaustion(
    invalid_field: str,
    invalid_value: str,
) -> None:
    redis_url = os.getenv("TEST_REDIS_URL")
    if redis_url is None:
        pytest.skip("需要 TEST_REDIS_URL 才运行真实 Redis malformed 测试")
    redis = Redis.from_url(redis_url, decode_responses=True)
    stream_name = f"test:malformed:{uuid4()}"
    group_name = "test-workers"
    fields = {
        "command_id": str(uuid4()),
        "run_id": str(uuid4()),
        "tenant_id": str(uuid4()),
        "action": "start",
        "payload": "{}",
    }
    fields[invalid_field] = invalid_value
    try:
        queue = RedisRunQueue(
            redis,
            stream_name=stream_name,
            group_name=group_name,
            pending_min_idle_ms=1,
            max_delivery_attempts=3,
        )
        await queue.setup()
        delivery_id = str(await redis.xadd(stream_name, fields))
        for attempt in range(1, 4):
            if attempt > 1:
                await asyncio.sleep(0.01)
            restarted = RedisRunQueue(
                redis,
                stream_name=stream_name,
                group_name=group_name,
                pending_min_idle_ms=1,
                max_delivery_attempts=3,
            )
            with pytest.raises(MalformedRunQueueMessage) as caught:
                await restarted.dequeue(consumer_name=f"worker-{attempt}", block_ms=100)
            assert caught.value.attempts == attempt
            assert caught.value.exhausted is (attempt == 3)
            assert str(caught.value) == "malformed queue message"
        await queue.acknowledge(delivery_id)
    finally:
        await redis.delete(stream_name)
        await redis.aclose()
