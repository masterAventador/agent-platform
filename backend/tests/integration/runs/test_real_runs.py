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
from agent_platform.infrastructure.database.repositories.runs import (
    SqlAlchemyRunEventRepository,
)
from agent_platform.infrastructure.security.rate_limits import RedisAuthRateLimiter
from agent_platform.platform.runs.events import EventType, PlatformEvent

BACKEND_ROOT = Path(__file__).parents[3]


@pytest.fixture(scope="module")
def run_dependency_urls() -> tuple[str, str]:
    database_url = os.getenv("TEST_DATABASE_URL")
    redis_url = os.getenv("TEST_REDIS_URL")
    if database_url is None or redis_url is None:
        pytest.skip("需要 TEST_DATABASE_URL 和 TEST_REDIS_URL 才运行真实任务集成测试")
    config = Config(BACKEND_ROOT / "alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    return database_url, redis_url


@pytest.mark.asyncio
async def test_run_and_incremental_events_on_postgres(
    run_dependency_urls: tuple[str, str],
) -> None:
    database_url, redis_url = run_dependency_urls
    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    redis = Redis.from_url(redis_url, decode_responses=True)
    app = create_app(
        settings=AppSettings(database_url=database_url, redis_url=redis_url),
        session_factory=session_factory,
        auth_rate_limiter=RedisAuthRateLimiter(redis, register_limit=5, login_limit=10),
    )
    credentials = {
        "email": f"run-real-{uuid4()}@example.com",
        "password": "correct horse battery staple",
    }
    definition = {
        "name": "Real Run Employee",
        "role_description": "验证真实任务与事件",
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

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        await client.post("/api/v1/auth/register", json=credentials)
        await client.post("/api/v1/auth/login", json=credentials)
        current_user = (await client.get("/api/v1/auth/me")).json()
        tenant_id = current_user["workspaces"][0]["id"]
        headers = {"X-Tenant-ID": tenant_id}
        employee = (await client.post("/api/v1/employees", headers=headers, json=definition)).json()
        await client.post(f"/api/v1/employees/{employee['id']}/publish", headers=headers)
        run = (
            await client.post(
                f"/api/v1/employees/{employee['id']}/runs",
                headers=headers,
                json={"input": {"topic": "events"}},
            )
        ).json()

        async with session_factory() as database_session:
            events = SqlAlchemyRunEventRepository(database_session)
            for sequence, event_type in (
                (1, EventType.RUN_STARTED),
                (2, EventType.RUN_PROGRESS),
            ):
                await events.append(
                    PlatformEvent.create(
                        tenant_id=tenant_id,
                        employee_id=employee["id"],
                        run_id=run["id"],
                        sequence=sequence,
                        event_type=event_type,
                        payload={"sequence": sequence},
                    )
                )
            await database_session.commit()

        response = await client.get(
            f"/api/v1/runs/{run['id']}/events?after_sequence=1",
            headers=headers,
        )
        assert response.status_code == 200
        assert [event["sequence"] for event in response.json()] == [2]
        assert response.json()[0]["type"] == "run.progress"

    await redis.aclose()
    await engine.dispose()
