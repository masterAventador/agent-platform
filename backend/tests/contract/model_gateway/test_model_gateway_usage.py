"""模型用量查询 API 契约（C16 阶段二，纯观测面）。

`GET /api/v1/model-gateway/usage`：`models.usage.read` 读、按 tenant 严格隔离、时间范围 +
keyset 分页；只返回平台自有用量模型，绝不泄露 LangChain/LiteLLM 原始 response_metadata；
费用以整数 nano-USD 字符串（或 null）返回，绝不浮点。
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import TypedDict, cast
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agent_platform.api.app import create_app
from agent_platform.config import AppSettings
from agent_platform.infrastructure.database.base import Base
from agent_platform.infrastructure.database.models import load_database_models
from agent_platform.infrastructure.database.repositories.model_usage import (
    SqlAlchemyModelUsageRepository,
)
from agent_platform.infrastructure.database.repositories.tenants import TenantMembershipRecord
from agent_platform.platform.model_gateway.usage import ModelCallOutcome, ModelUsageRecord

NOW = datetime(2026, 7, 17, 12, 0, tzinfo=UTC)


class WorkspaceIdentity(TypedDict):
    id: str


class AuthIdentity(TypedDict):
    id: str
    workspaces: list[WorkspaceIdentity]


class AllowAllRateLimiter:
    async def ensure_allowed(self, *, scope: str, key: str) -> None:
        del scope, key


@pytest_asyncio.fixture
async def usage_client() -> AsyncIterator[
    tuple[AsyncClient, async_sessionmaker[AsyncSession]]
]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    load_database_models()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    app = create_app(
        settings=AppSettings(auth_cookie_secure=False),
        session_factory=sessions,
        auth_rate_limiter=AllowAllRateLimiter(),
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client, sessions
    await engine.dispose()


async def _register_and_login(client: AsyncClient, email: str) -> AuthIdentity:
    credentials = {"email": email, "password": "correct horse battery staple"}
    assert (await client.post("/api/v1/auth/register", json=credentials)).status_code == 201
    assert (await client.post("/api/v1/auth/login", json=credentials)).status_code == 200
    return cast(AuthIdentity, (await client.get("/api/v1/auth/me")).json())


async def _seed_usage(
    sessions: async_sessionmaker[AsyncSession],
    tenant_id: UUID,
    *,
    count: int,
    outcome: ModelCallOutcome = ModelCallOutcome.SUCCESS,
    base: datetime = NOW,
) -> list[UUID]:
    ids: list[UUID] = []
    async with sessions() as session:
        repo = SqlAlchemyModelUsageRepository(session)
        for i in range(count):
            known = outcome is ModelCallOutcome.SUCCESS
            rid = uuid4()
            ids.append(rid)
            repo.add(
                ModelUsageRecord(
                    id=rid,
                    tenant_id=tenant_id,
                    run_id=uuid4(),
                    employee_id=uuid4(),
                    model_alias="general-purpose",
                    prompt_tokens=10 if known else None,
                    completion_tokens=5 if known else None,
                    total_tokens=15 if known else None,
                    latency_ms=42,
                    outcome=outcome,
                    error_type=None if known else "ReadTimeout",
                    cost_nanousd=1_500 if known else None,
                    cost_source="platform_pricing_table" if known else None,
                    recorded_at=base + timedelta(seconds=i),
                )
            )
        await session.commit()
    return ids


def _assert_no_framework_leak(record: dict) -> None:
    forbidden = {
        "response_metadata",
        "llm_output",
        "usage_metadata",
        "system_fingerprint",
        "model_name",
        "provider",
        "base_url",
        "api_key",
    }
    assert forbidden.isdisjoint(record)


@pytest.mark.asyncio
async def test_owner_reads_usage_newest_first_platform_model_only(usage_client) -> None:
    client, sessions = usage_client
    owner = await _register_and_login(client, "usage-owner@example.com")
    tenant_id = UUID(str(owner["workspaces"][0]["id"]))
    ids = await _seed_usage(sessions, tenant_id, count=3)
    headers = {"X-Tenant-ID": str(tenant_id)}

    response = await client.get("/api/v1/model-gateway/usage", headers=headers)
    assert response.status_code == 200
    body = response.json()
    returned = [r["id"] for r in body["records"]]
    assert returned == [str(i) for i in reversed(ids)]  # 最新在前
    rec = body["records"][0]
    assert rec["model_alias"] == "general-purpose"
    assert rec["cost_nanousd"] == "1500"  # 整数字符串，绝不浮点
    assert rec["outcome"] == "success"
    _assert_no_framework_leak(rec)


@pytest.mark.asyncio
async def test_error_record_serializes_null_tokens_and_cost(usage_client) -> None:
    client, sessions = usage_client
    owner = await _register_and_login(client, "usage-error@example.com")
    tenant_id = UUID(str(owner["workspaces"][0]["id"]))
    await _seed_usage(sessions, tenant_id, count=1, outcome=ModelCallOutcome.ERROR)
    headers = {"X-Tenant-ID": str(tenant_id)}
    rec = (await client.get("/api/v1/model-gateway/usage", headers=headers)).json()[
        "records"
    ][0]
    assert rec["outcome"] == "error"
    assert rec["error_type"] == "ReadTimeout"
    assert rec["prompt_tokens"] is None
    assert rec["cost_nanousd"] is None


@pytest.mark.asyncio
async def test_tenant_isolation(usage_client) -> None:
    client, sessions = usage_client
    owner_a = await _register_and_login(client, "usage-a@example.com")
    tenant_a = UUID(str(owner_a["workspaces"][0]["id"]))
    await _seed_usage(sessions, tenant_a, count=2)
    await client.post("/api/v1/auth/logout")
    owner_b = await _register_and_login(client, "usage-b@example.com")
    tenant_b = UUID(str(owner_b["workspaces"][0]["id"]))
    body = (
        await client.get(
            "/api/v1/model-gateway/usage", headers={"X-Tenant-ID": str(tenant_b)}
        )
    ).json()
    assert body["records"] == []
    assert body["next_cursor"] is None
    del tenant_b


@pytest.mark.asyncio
async def test_member_without_permission_is_forbidden(usage_client) -> None:
    client, sessions = usage_client
    owner = await _register_and_login(client, "usage-rbac-owner@example.com")
    tenant_id = UUID(str(owner["workspaces"][0]["id"]))
    await _seed_usage(sessions, tenant_id, count=1)

    async def join(email: str, role: str) -> None:
        await client.post("/api/v1/auth/logout")
        user = await _register_and_login(client, email)
        async with sessions() as session:
            session.add(
                TenantMembershipRecord(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    user_id=UUID(str(user["id"])),
                    role=role,
                    created_at=datetime.now(UTC),
                )
            )
            await session.commit()

    headers = {"X-Tenant-ID": str(tenant_id)}
    await join("usage-admin@example.com", "admin")
    assert (
        await client.get("/api/v1/model-gateway/usage", headers=headers)
    ).status_code == 200  # Admin 有 models.usage.read
    await join("usage-member@example.com", "member")
    assert (
        await client.get("/api/v1/model-gateway/usage", headers=headers)
    ).status_code == 403  # Member 无


@pytest.mark.asyncio
async def test_time_range_and_keyset_pagination(usage_client) -> None:
    client, sessions = usage_client
    owner = await _register_and_login(client, "usage-page@example.com")
    tenant_id = UUID(str(owner["workspaces"][0]["id"]))
    ids = await _seed_usage(sessions, tenant_id, count=5)
    headers = {"X-Tenant-ID": str(tenant_id)}

    seen: list[str] = []
    cursor: str | None = None
    for _ in range(10):
        params: dict[str, str] = {"limit": "2"}
        if cursor is not None:
            params["cursor"] = cursor
        page = (
            await client.get(
                "/api/v1/model-gateway/usage", headers=headers, params=params
            )
        ).json()
        seen.extend(r["id"] for r in page["records"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert seen == [str(i) for i in reversed(ids)]

    # 时间范围过滤：只取最后两条
    ranged = (
        await client.get(
            "/api/v1/model-gateway/usage",
            headers=headers,
            params={
                "start": (NOW + timedelta(seconds=3)).isoformat(),
                "end": (NOW + timedelta(seconds=10)).isoformat(),
            },
        )
    ).json()
    assert {r["id"] for r in ranged["records"]} == {str(ids[3]), str(ids[4])}
