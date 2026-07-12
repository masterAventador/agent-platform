import os
from uuid import uuid4

import pytest
from redis.asyncio import Redis

from agent_platform.infrastructure.queue.redis_streams import RedisRunQueue, RunQueueMessage


@pytest.mark.asyncio
async def test_redis_stream_queue_enqueues_consumes_and_acknowledges() -> None:
    redis_url = os.getenv("TEST_REDIS_URL")
    if redis_url is None:
        pytest.skip("需要 TEST_REDIS_URL 才运行真实 Redis 队列测试")

    redis = Redis.from_url(redis_url, decode_responses=True)
    stream_name = f"test:runs:{uuid4()}"
    queue = RedisRunQueue(redis, stream_name=stream_name, group_name="test-workers")
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
    await queue.acknowledge(delivery.delivery_id)
    pending = await redis.xpending(stream_name, "test-workers")
    assert pending["pending"] == 0

    await redis.delete(stream_name)
    await redis.aclose()
