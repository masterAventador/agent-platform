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
from agent_platform.infrastructure.security.rate_limits import RedisAuthRateLimiter

BACKEND_ROOT = Path(__file__).parents[3]


@pytest.fixture(scope="module")
def employee_dependency_urls() -> tuple[str, str]:
    database_url = os.getenv("TEST_DATABASE_URL")
    redis_url = os.getenv("TEST_REDIS_URL")
    if database_url is None or redis_url is None:
        pytest.skip("需要 TEST_DATABASE_URL 和 TEST_REDIS_URL 才运行真实员工集成测试")

    config = Config(BACKEND_ROOT / "alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    return database_url, redis_url


@pytest.mark.asyncio
async def test_employee_lifecycle_on_postgres(
    employee_dependency_urls: tuple[str, str],
) -> None:
    database_url, redis_url = employee_dependency_urls
    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    redis = Redis.from_url(redis_url, decode_responses=True)
    app = create_app(
        settings=AppSettings(
            database_url=database_url,
            redis_url=redis_url,
            auth_cookie_secure=False,
        ),
        session_factory=session_factory,
        auth_rate_limiter=RedisAuthRateLimiter(redis, register_limit=5, login_limit=10),
    )
    email = f"employee-real-{uuid4()}@example.com"
    credentials = {"email": email, "password": "correct horse battery staple"}
    definition = {
        "name": "PostgreSQL Research Agent",
        "role_description": "验证真实 PostgreSQL 员工生命周期",
        "work_mode": "hybrid",
        "system_prompt": "完成真实数据库验证。",
        "model": {"provider": "openai", "name": "gpt-5"},
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object"},
        "capabilities": {
            "conversation": True,
            "scheduled_tasks": False,
            "file_upload": False,
        },
    }

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        assert (await client.post("/api/v1/auth/register", json=credentials)).status_code == 201
        assert (await client.post("/api/v1/auth/login", json=credentials)).status_code == 200
        current_user = (await client.get("/api/v1/auth/me")).json()
        headers = {"X-Tenant-ID": current_user["workspaces"][0]["id"]}

        created = await client.post("/api/v1/employees", headers=headers, json=definition)
        assert created.status_code == 201
        employee_id = created.json()["id"]
        assert (
            await client.post(f"/api/v1/employees/{employee_id}/publish", headers=headers)
        ).status_code == 200
        versions = await client.get(
            f"/api/v1/employees/{employee_id}/versions",
            headers=headers,
        )
        assert versions.status_code == 200
        assert versions.json()[0]["version"] == 1

        duplicate = await client.post(
            "/api/v1/employees",
            headers=headers,
            json={**definition, "name": "postgresql research agent"},
        )
        assert duplicate.status_code == 409

    await redis.aclose()
    await engine.dispose()
