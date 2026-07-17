import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any, TypedDict, cast
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agent_platform.api.app import create_app
from agent_platform.config import AppSettings
from agent_platform.infrastructure.database.base import Base
from agent_platform.infrastructure.database.models import load_database_models
from agent_platform.infrastructure.database.repositories.audit import AuditEventRecord
from agent_platform.infrastructure.database.repositories.model_gateway import (
    TenantModelGatewayKeyRecord,
    TenantModelGatewayPolicyRecord,
)
from agent_platform.infrastructure.database.repositories.tenants import TenantMembershipRecord
from agent_platform.platform.model_gateway.credentials import (
    INSECURE_DEV_MODEL_GATEWAY_KEY_SECRET,
    derive_tenant_gateway_key,
)
from agent_platform.platform.model_gateway.entities import MAX_BUDGET_MICROUSD


class WorkspaceIdentity(TypedDict):
    id: str


class AuthIdentity(TypedDict):
    id: str
    workspaces: list[WorkspaceIdentity]


class AllowAllRateLimiter:
    async def ensure_allowed(self, *, scope: str, key: str) -> None:
        del scope, key


@pytest_asyncio.fixture
async def model_gateway_client() -> AsyncIterator[
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


def _payload(expected_revision: int = 0) -> dict[str, object]:
    return {
        "expected_revision": expected_revision,
        "enabled": True,
        "allowed_aliases": ["general-purpose"],
        "budget_microusd": "5000000",
        "budget_period": "monthly",
        "rpm_limit": 60,
        "tpm_limit": 120_000,
        "max_parallel_requests": 4,
    }


async def _register_and_login(client: AsyncClient, email: str) -> AuthIdentity:
    credentials = {"email": email, "password": "correct horse battery staple"}
    assert (await client.post("/api/v1/auth/register", json=credentials)).status_code == 201
    assert (await client.post("/api/v1/auth/login", json=credentials)).status_code == 200
    return cast(AuthIdentity, (await client.get("/api/v1/auth/me")).json())


def _assert_forbidden_fields_absent(value: object) -> None:
    forbidden = {
        "api_key",
        "master_key",
        "raw_key",
        "secret_ref",
        "secret_reference",
        "provider",
        "model",
        "base_url",
        "usage",
        "spend",
        "credentials",
        "has_credentials",
    }
    if isinstance(value, dict):
        assert forbidden.isdisjoint(value)
        for nested in value.values():
            _assert_forbidden_fields_absent(nested)
    elif isinstance(value, list):
        for nested in value:
            _assert_forbidden_fields_absent(nested)


@pytest.mark.asyncio
async def test_owner_can_create_read_and_revise_provider_neutral_policy(
    model_gateway_client: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, _ = model_gateway_client
    owner = await _register_and_login(client, "gateway-owner@example.com")
    headers = {"X-Tenant-ID": owner["workspaces"][0]["id"]}

    missing = await client.get("/api/v1/model-gateway/policy", headers=headers)
    assert missing.status_code == 404
    created = await client.put(
        "/api/v1/model-gateway/policy", headers=headers, json=_payload()
    )
    assert created.status_code == 200
    body = created.json()
    assert body["revision"] == 1
    assert body["status"] == "pending"
    assert body["allowed_aliases"] == ["general-purpose"]
    assert body["budget_microusd"] == "5000000"
    _assert_forbidden_fields_absent(body)
    assert (
        await client.get("/api/v1/model-gateway/policy", headers=headers)
    ).json() == body

    revised_payload = _payload(expected_revision=1)
    revised_payload["enabled"] = False
    revised = await client.put(
        "/api/v1/model-gateway/policy", headers=headers, json=revised_payload
    )
    assert revised.status_code == 200
    assert revised.json()["revision"] == 2
    assert revised.json()["enabled"] is False

    stale = await client.put(
        "/api/v1/model-gateway/policy", headers=headers, json=_payload(expected_revision=1)
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "model_gateway_policy_revision_conflict"


@pytest.mark.asyncio
async def test_policy_contract_uses_decimal_budget_string_and_strict_json_types(
    model_gateway_client: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, _ = model_gateway_client
    owner = await _register_and_login(client, "gateway-types@example.com")
    headers = {"X-Tenant-ID": owner["workspaces"][0]["id"]}

    openapi = cast(dict[str, Any], (await client.get("/openapi.json")).json())
    put_schema = openapi["components"]["schemas"]["ModelGatewayPolicyPut"]["properties"]
    response_schema = openapi["components"]["schemas"]["ModelGatewayPolicyResponse"][
        "properties"
    ]
    assert put_schema["budget_microusd"]["type"] == "string"
    assert put_schema["budget_microusd"]["pattern"] == "^[1-9][0-9]*$"
    assert response_schema["budget_microusd"]["type"] == "string"

    invalid_payloads = []
    for field, value in (
        ("enabled", "true"),
        ("enabled", 1),
        ("expected_revision", 0.0),
        ("rpm_limit", 60.0),
        ("tpm_limit", "120000"),
        ("max_parallel_requests", True),
        ("budget_microusd", 5_000_000),
        ("budget_microusd", "05000000"),
        ("budget_microusd", str(MAX_BUDGET_MICROUSD + 1)),
    ):
        payload = _payload()
        payload[field] = value
        invalid_payloads.append(payload)
    duplicate_aliases = _payload()
    duplicate_aliases["allowed_aliases"] = ["general-purpose", "general-purpose"]
    invalid_payloads.append(duplicate_aliases)
    sensitive_extra = _payload()
    sensitive_extra["provider"] = "must-not-be-accepted"
    invalid_payloads.append(sensitive_extra)

    for payload in invalid_payloads:
        response = await client.put(
            "/api/v1/model-gateway/policy", headers=headers, json=payload
        )
        assert response.status_code == 422

    assert (
        await client.get("/api/v1/model-gateway/policy", headers=headers)
    ).status_code == 404


@pytest.mark.asyncio
async def test_corrupt_persisted_policy_returns_stable_redacted_server_error(
    model_gateway_client: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, sessions = model_gateway_client
    owner = await _register_and_login(client, "gateway-corrupt@example.com")
    tenant_id = UUID(str(owner["workspaces"][0]["id"]))
    user_id = UUID(str(owner["id"]))
    headers = {"X-Tenant-ID": str(tenant_id)}
    async with sessions() as session:
        session.add(
            TenantModelGatewayPolicyRecord(
                tenant_id=tenant_id,
                enabled=True,
                allowed_aliases=["secret-provider-model"],
                budget_microusd=1_000_000,
                budget_period="monthly",
                rpm_limit=60,
                tpm_limit=100_000,
                max_parallel_requests=4,
                revision=1,
                status="active",
                created_at=datetime(2026, 7, 14, tzinfo=UTC),
                updated_at=datetime(2026, 7, 15, tzinfo=UTC),
                updated_by=user_id,
            )
        )
        await session.commit()

    response = await client.get("/api/v1/model-gateway/policy", headers=headers)
    assert response.status_code == 500
    assert response.json()["detail"] == {
        "code": "corrupt_model_gateway_policy",
        "message": "模型网关策略持久化数据无效",
    }
    assert "secret-provider-model" not in response.text


@pytest.mark.asyncio
async def test_model_gateway_policy_permission_matrix_and_cross_tenant_404(
    model_gateway_client: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, sessions = model_gateway_client
    owner = await _register_and_login(client, "gateway-rbac-owner@example.com")
    tenant_id = UUID(str(owner["workspaces"][0]["id"]))
    headers = {"X-Tenant-ID": str(tenant_id)}
    assert (
        await client.put("/api/v1/model-gateway/policy", headers=headers, json=_payload())
    ).status_code == 200

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

    await join("gateway-admin@example.com", "admin")
    assert (await client.get("/api/v1/model-gateway/policy", headers=headers)).status_code == 200
    assert (
        await client.put(
            "/api/v1/model-gateway/policy", headers=headers, json=_payload(expected_revision=1)
        )
    ).status_code == 403

    await join("gateway-member@example.com", "member")
    assert (await client.get("/api/v1/model-gateway/policy", headers=headers)).status_code == 403
    assert (
        await client.put(
            "/api/v1/model-gateway/policy", headers=headers, json=_payload(expected_revision=1)
        )
    ).status_code == 403

    await client.post("/api/v1/auth/logout")
    foreign_owner = await _register_and_login(client, "gateway-foreign@example.com")
    foreign_headers = {"X-Tenant-ID": str(foreign_owner["workspaces"][0]["id"])}
    assert (
        await client.get("/api/v1/model-gateway/policy", headers=headers)
    ).status_code == 404
    assert (
        await client.put(
            "/api/v1/model-gateway/policy", headers=headers, json=_payload(expected_revision=1)
        )
    ).status_code == 404
    assert foreign_headers != headers


@pytest.mark.asyncio
async def test_policy_changes_are_recorded_in_the_platform_audit_log(
    model_gateway_client: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    """C16 完成定义：模型配置与凭据变更必须接入 C14 统一审计。"""
    client, sessions = model_gateway_client
    owner = await _register_and_login(client, "gateway-audit-owner@example.com")
    tenant_id = UUID(str(owner["workspaces"][0]["id"]))
    headers = {"X-Tenant-ID": str(tenant_id)}

    await client.put("/api/v1/model-gateway/policy", headers=headers, json=_payload())
    await client.put(
        "/api/v1/model-gateway/policy", headers=headers, json=_payload(expected_revision=1)
    )

    async with sessions() as session:
        events = (
            (
                await session.execute(
                    select(AuditEventRecord)
                    .where(AuditEventRecord.tenant_id == tenant_id)
                    .order_by(AuditEventRecord.sequence)
                )
            )
            .scalars()
            .all()
        )
    gateway_events = [
        event for event in events if event.resource_type == "model_gateway_policy"
    ]
    assert [event.action for event in gateway_events] == [
        "model_gateway.policy_updated",
        "model_gateway.policy_updated",
    ]
    assert [event.metadata_json["revision"] for event in gateway_events] == [1, 2]
    assert gateway_events[0].actor_user_id == UUID(str(owner["id"]))
    assert gateway_events[0].outcome == "succeeded"


@pytest.mark.asyncio
async def test_owner_can_rotate_the_tenant_key_and_the_rotation_is_audited(
    model_gateway_client: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, sessions = model_gateway_client
    owner = await _register_and_login(client, "gateway-rotate-owner@example.com")
    tenant_id = UUID(str(owner["workspaces"][0]["id"]))
    headers = {"X-Tenant-ID": str(tenant_id)}
    await client.put("/api/v1/model-gateway/policy", headers=headers, json=_payload())
    # 轮换前必须已签发（首次对账才建 Key 行）
    async with sessions() as session:
        session.add(
            TenantModelGatewayKeyRecord(
                tenant_id=tenant_id,
                key_version=1,
                retired_key_version=None,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
        await session.commit()

    response = await client.post("/api/v1/model-gateway/key/rotate", headers=headers)

    assert response.status_code == 200
    body = response.json()
    # 轮换递增 desired revision，重新进入待对账
    assert body["revision"] == 2
    assert body["status"] == "pending"
    _assert_forbidden_fields_absent(body)
    async with sessions() as session:
        key = await session.get(TenantModelGatewayKeyRecord, tenant_id)
        events = (
            (
                await session.execute(
                    select(AuditEventRecord).where(
                        AuditEventRecord.resource_type == "model_gateway_key"
                    )
                )
            )
            .scalars()
            .all()
        )
    assert key is not None
    assert key.key_version == 2
    assert key.retired_key_version == 1
    assert [event.action for event in events] == ["model_gateway.key_rotated"]
    assert events[0].metadata_json == {"key_version": 2}


@pytest.mark.asyncio
async def test_rotation_never_exposes_key_material_in_the_response_or_audit(
    model_gateway_client: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    """Key 明文绝不出接口、绝不进审计 metadata。"""
    client, sessions = model_gateway_client
    owner = await _register_and_login(client, "gateway-secret-owner@example.com")
    tenant_id = UUID(str(owner["workspaces"][0]["id"]))
    headers = {"X-Tenant-ID": str(tenant_id)}
    await client.put("/api/v1/model-gateway/policy", headers=headers, json=_payload())
    async with sessions() as session:
        session.add(
            TenantModelGatewayKeyRecord(
                tenant_id=tenant_id,
                key_version=1,
                retired_key_version=None,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
        await session.commit()

    response = await client.post("/api/v1/model-gateway/key/rotate", headers=headers)

    derived = derive_tenant_gateway_key(
        secret=SecretStr(INSECURE_DEV_MODEL_GATEWAY_KEY_SECRET),
        tenant_id=tenant_id,
        key_version=2,
    ).get_secret_value()
    assert derived not in response.text
    assert "sk-" not in response.text
    async with sessions() as session:
        events = (
            (await session.execute(select(AuditEventRecord))).scalars().all()
        )
    for event in events:
        assert derived not in json.dumps(event.metadata_json)


@pytest.mark.asyncio
async def test_rotation_is_rejected_while_a_previous_version_awaits_retirement(
    model_gateway_client: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    """未回收上一版本时再次轮换会让旧 Key 成为网关侧孤儿：必须受控拒绝。"""
    client, sessions = model_gateway_client
    owner = await _register_and_login(client, "gateway-rotate-twice@example.com")
    tenant_id = UUID(str(owner["workspaces"][0]["id"]))
    headers = {"X-Tenant-ID": str(tenant_id)}
    await client.put("/api/v1/model-gateway/policy", headers=headers, json=_payload())
    async with sessions() as session:
        session.add(
            TenantModelGatewayKeyRecord(
                tenant_id=tenant_id,
                key_version=2,
                retired_key_version=1,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
        await session.commit()

    response = await client.post("/api/v1/model-gateway/key/rotate", headers=headers)

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "model_gateway_key_rotation_in_progress"


@pytest.mark.asyncio
async def test_rotation_requires_an_already_provisioned_key(
    model_gateway_client: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, _ = model_gateway_client
    owner = await _register_and_login(client, "gateway-rotate-unprovisioned@example.com")
    headers = {"X-Tenant-ID": str(owner["workspaces"][0]["id"])}
    await client.put("/api/v1/model-gateway/policy", headers=headers, json=_payload())

    response = await client.post("/api/v1/model-gateway/key/rotate", headers=headers)

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "model_gateway_key_not_provisioned"


@pytest.mark.asyncio
async def test_key_rotation_permission_matrix_and_cross_tenant_isolation(
    model_gateway_client: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    """轮换是凭据操作：只有 Owner（models.manage）可以执行。"""
    client, sessions = model_gateway_client
    owner = await _register_and_login(client, "rotate-rbac-owner@example.com")
    tenant_id = UUID(str(owner["workspaces"][0]["id"]))
    headers = {"X-Tenant-ID": str(tenant_id)}
    await client.put("/api/v1/model-gateway/policy", headers=headers, json=_payload())
    async with sessions() as session:
        session.add(
            TenantModelGatewayKeyRecord(
                tenant_id=tenant_id,
                key_version=1,
                retired_key_version=None,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
        await session.commit()

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

    await join("rotate-admin@example.com", "admin")
    assert (
        await client.post("/api/v1/model-gateway/key/rotate", headers=headers)
    ).status_code == 403

    await join("rotate-member@example.com", "member")
    assert (
        await client.post("/api/v1/model-gateway/key/rotate", headers=headers)
    ).status_code == 403

    await client.post("/api/v1/auth/logout")
    await _register_and_login(client, "rotate-foreign@example.com")
    assert (
        await client.post("/api/v1/model-gateway/key/rotate", headers=headers)
    ).status_code == 404
