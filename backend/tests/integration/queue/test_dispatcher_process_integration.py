import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_platform.api.app import create_app
from agent_platform.config import AppSettings
from agent_platform.infrastructure.queue.dispatcher import RunCommandDispatcher
from agent_platform.infrastructure.queue.redis_streams import RedisRunQueue
from agent_platform.infrastructure.security.rate_limits import RedisAuthRateLimiter

BACKEND_ROOT = Path(__file__).parents[3]


@pytest.fixture(scope="module")
def migrated_dependency_urls() -> tuple[str, str]:
    database_url = os.getenv("TEST_DATABASE_URL")
    redis_url = os.getenv("TEST_REDIS_URL")
    if database_url is None or redis_url is None:
        pytest.skip("需要 TEST_DATABASE_URL 和 TEST_REDIS_URL 才运行真实 Dispatcher 集成测试")
    config = Config(BACKEND_ROOT / "alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    return database_url, redis_url


@pytest.mark.asyncio
async def test_api_outbox_is_dispatched_to_real_redis(
    migrated_dependency_urls: tuple[str, str],
) -> None:
    database_url, redis_url = migrated_dependency_urls
    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    redis = Redis.from_url(redis_url, decode_responses=True)
    stream_name = f"test:dispatcher:{uuid4()}"
    queue = RedisRunQueue(redis, stream_name=stream_name, group_name="test-dispatcher-workers")
    await queue.setup()
    app = create_app(
        settings=AppSettings(database_url=database_url, redis_url=redis_url),
        session_factory=session_factory,
        auth_rate_limiter=RedisAuthRateLimiter(redis, register_limit=5, login_limit=10),
    )
    credentials = {
        "email": f"dispatcher-{uuid4()}@example.com",
        "password": "correct horse battery staple",
    }
    definition = {
        "name": "Dispatcher Integration Employee",
        "role_description": "验证 outbox 到 Redis 的可靠投递边界",
        "work_mode": "autonomous",
        "system_prompt": "执行任务。",
        "model": {"provider": "openai", "name": "gpt-5"},
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object"},
        "capabilities": {
            "conversation": False,
            "scheduled_tasks": False,
            "file_upload": False,
        },
    }

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            await client.post("/api/v1/auth/register", json=credentials)
            await client.post("/api/v1/auth/login", json=credentials)
            current_user = (await client.get("/api/v1/auth/me")).json()
            headers = {"X-Tenant-ID": current_user["workspaces"][0]["id"]}
            employee = (
                await client.post("/api/v1/employees", headers=headers, json=definition)
            ).json()
            await client.post(f"/api/v1/employees/{employee['id']}/publish", headers=headers)
            run = (
                await client.post(
                    f"/api/v1/employees/{employee['id']}/runs",
                    headers=headers,
                    json={"input": {"topic": "outbox"}},
                )
            ).json()

        dispatched = await RunCommandDispatcher(
            session_factory=session_factory,
            queue=queue,
        ).dispatch_pending()
        deliveries = [
            await queue.dequeue(consumer_name="integration-worker", block_ms=100)
            for _ in range(dispatched)
        ]

        assert dispatched >= 1
        matching = [
            delivery
            for delivery in deliveries
            if delivery is not None and str(delivery.message.run_id) == run["id"]
        ]
        assert len(matching) == 1
        assert matching[0].message.action == "start"
    finally:
        await redis.delete(stream_name)
        await redis.aclose()
        await engine.dispose()
