from uuid import uuid4

import pytest

from agent_platform.infrastructure.queue.redis_streams import RedisRunQueue


class ClaimingRedis:
    def __init__(self) -> None:
        self.claim_calls: list[tuple[object, ...]] = []
        self.read_calls = 0
        self.command_id = uuid4()
        self.run_id = uuid4()
        self.tenant_id = uuid4()

    async def xautoclaim(self, *args: object, **kwargs: object) -> list[object]:
        self.claim_calls.append((*args, kwargs))
        return [
            "0-0",
            [
                (
                    "7-0",
                    {
                        "command_id": str(self.command_id),
                        "run_id": str(self.run_id),
                        "tenant_id": str(self.tenant_id),
                        "action": "start",
                        "payload": "{}",
                    },
                )
            ],
            [],
        ]

    async def xreadgroup(self, *args: object, **kwargs: object) -> list[object]:
        del args, kwargs
        self.read_calls += 1
        return []


@pytest.mark.asyncio
async def test_dequeue_claims_stale_pending_before_reading_new_messages() -> None:
    redis = ClaimingRedis()
    queue = RedisRunQueue(
        redis,  # type: ignore[arg-type]
        stream_name="runs",
        group_name="workers",
        pending_min_idle_ms=2500,
    )

    delivery = await queue.dequeue(consumer_name="worker-2", block_ms=10)

    assert delivery is not None
    assert delivery.delivery_id == "7-0"
    assert delivery.message.command_id == redis.command_id
    assert redis.read_calls == 0
    assert redis.claim_calls == [
        (
            "runs",
            "workers",
            "worker-2",
            2500,
            "0-0",
            {"count": 1},
        )
    ]


@pytest.mark.asyncio
async def test_dequeue_advances_claim_cursor_past_young_pending_entries() -> None:
    redis = ClaimingRedis()
    responses = iter(
        [
            ["5-0", [], []],
            [
                "0-0",
                [
                    (
                        "9-0",
                        {
                            "command_id": str(redis.command_id),
                            "run_id": str(redis.run_id),
                            "tenant_id": str(redis.tenant_id),
                            "action": "start",
                            "payload": "{}",
                        },
                    )
                ],
                [],
            ],
        ]
    )

    async def paged_autoclaim(*args: object, **kwargs: object) -> list[object]:
        redis.claim_calls.append((*args, kwargs))
        return next(responses)

    redis.xautoclaim = paged_autoclaim  # type: ignore[method-assign]
    queue = RedisRunQueue(redis, stream_name="runs", group_name="workers")  # type: ignore[arg-type]

    delivery = await queue.dequeue(consumer_name="worker-2", block_ms=10)

    assert delivery is not None and delivery.delivery_id == "9-0"
    assert [call[4] for call in redis.claim_calls] == ["0-0", "5-0"]
    assert redis.read_calls == 0
