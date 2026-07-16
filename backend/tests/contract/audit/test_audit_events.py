from collections import Counter
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from io import BytesIO
from typing import Any
from uuid import UUID, uuid4
from zipfile import ZipFile

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agent_platform.api.app import create_app
from agent_platform.config import AppSettings
from agent_platform.infrastructure.database.base import Base
from agent_platform.infrastructure.database.models import load_database_models
from agent_platform.infrastructure.database.repositories.audit import (
    AuditEventCreate,
    AuditEventRecord,
    SqlAlchemyAuditEventRepository,
    emit_audit_event,
)
from agent_platform.infrastructure.database.repositories.tenants import TenantMembershipRecord
from agent_platform.platform.knowledge.models import KnowledgeDataset


class AllowAllRateLimiter:
    async def ensure_allowed(self, *, scope: str, key: str) -> None:
        del scope, key


class FakeKnowledgeProvider:
    provider_name = "fake-knowledge"

    async def create_dataset(
        self,
        *,
        name: str,
        description: str = "",
        chunk_method: str = "naive",
    ) -> KnowledgeDataset:
        del description, chunk_method
        return KnowledgeDataset(provider_id=f"dataset-{name}", name=name)

    async def delete_dataset(self, provider_id: str) -> None:
        del provider_id


class InMemorySkillStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def put(self, *, key: str, content: bytes) -> None:
        self.objects[key] = content

    async def get(self, *, key: str) -> bytes:
        return self.objects[key]

    async def delete(self, *, key: str) -> None:
        self.objects.pop(key, None)


@dataclass(frozen=True, slots=True)
class AuditHarness:
    client: AsyncClient
    session_factory: async_sessionmaker[AsyncSession]


@pytest_asyncio.fixture
async def audit_harness() -> AsyncIterator[AuditHarness]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    load_database_models()
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    app = create_app(
        settings=AppSettings(auth_cookie_secure=False),
        session_factory=session_factory,
        auth_rate_limiter=AllowAllRateLimiter(),
        knowledge_provider=FakeKnowledgeProvider(),
        skill_storage=InMemorySkillStorage(),
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        yield AuditHarness(client=client, session_factory=session_factory)

    await engine.dispose()


async def _register_and_login(client: AsyncClient, email: str) -> dict[str, Any]:
    credentials = {"email": email, "password": "correct horse battery staple"}
    register = await client.post("/api/v1/auth/register", json=credentials)
    assert register.status_code == 201
    login = await client.post("/api/v1/auth/login", json=credentials)
    assert login.status_code == 200
    current = await client.get("/api/v1/auth/me")
    assert current.status_code == 200
    return current.json()


def _employee_definition(name: str) -> dict[str, object]:
    return {
        "name": name,
        "role_description": "用于审计契约验证",
        "work_mode": "autonomous",
        "system_prompt": "只执行审计验证。",
        "model": {"kind": "gateway_alias", "alias": "general-purpose"},
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object"},
        "capabilities": {
            "conversation": False,
            "scheduled_tasks": False,
            "file_upload": False,
        },
    }


def _skill_zip(*, name: str = "audit-helper", description: str = "审计 Skill") -> bytes:
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        archive.writestr(
            f"{name}/SKILL.md",
            f"---\nname: {name}\ndescription: {description}\n---\n\n# Audit helper\n",
        )
    return output.getvalue()


@pytest.mark.asyncio
async def test_audit_events_capture_auth_employee_and_run_actions(
    audit_harness: AuditHarness,
) -> None:
    audit_client = audit_harness.client
    current_user = await _register_and_login(
        audit_client,
        f"audit-owner-{uuid4()}@example.com",
    )
    tenant_id = current_user["workspaces"][0]["id"]
    headers = {"X-Tenant-ID": tenant_id}
    employee = (
        await audit_client.post(
            "/api/v1/employees",
            headers=headers,
            json=_employee_definition("审计员工"),
        )
    ).json()
    assert (
        await audit_client.post(f"/api/v1/employees/{employee['id']}/publish", headers=headers)
    ).status_code == 200
    run = (
        await audit_client.post(
            f"/api/v1/employees/{employee['id']}/runs",
            headers=headers,
            json={"input": {"secret": "password=must-not-enter-audit"}},
        )
    ).json()
    assert (
        await audit_client.post(
            f"/api/v1/runs/{run['id']}/control",
            headers=headers,
            json={"action": "cancel", "reason": "token=must-not-enter-audit"},
        )
    ).status_code == 202

    response = await audit_client.get("/api/v1/audit/events", headers=headers)

    assert response.status_code == 200
    events = response.json()
    actions = [event["action"] for event in events]
    assert actions[:5] == [
        "run.control_requested",
        "run.created",
        "employee.published",
        "employee.created",
        "auth.login_succeeded",
    ]
    assert "auth.registered" in actions
    assert "tenant.member_added" in actions
    assert "tenant.role_assigned" in actions
    assert all(event["tenant_id"] == tenant_id for event in events)
    assert all(event["actor_user_id"] == current_user["id"] for event in events)
    rendered = repr(events)
    assert "password=must-not-enter-audit" not in rendered
    assert "token=must-not-enter-audit" not in rendered
    assert events[0]["resource_type"] == "run"
    assert events[0]["resource_id"] == run["id"]
    assert events[0]["metadata"] == {
        "requested_action": "cancel",
        "reason_present": True,
    }


@pytest.mark.asyncio
async def test_registration_emits_exactly_one_audit_event_per_semantic(
    audit_harness: AuditHarness,
) -> None:
    credentials = {
        "email": f"audit-dedupe-{uuid4()}@example.com",
        "password": "correct horse battery staple",
    }
    register = await audit_harness.client.post("/api/v1/auth/register", json=credentials)
    assert register.status_code == 201
    tenant_id = UUID(register.json()["workspaces"][0]["id"])

    async with audit_harness.session_factory() as session:
        result = await session.execute(
            select(AuditEventRecord).where(AuditEventRecord.tenant_id == tenant_id)
        )
        events = list(result.scalars())

    assert Counter((event.action, event.resource_type) for event in events) == Counter(
        {
            ("auth.registered", "user"): 1,
            ("tenant.member_added", "tenant_membership"): 1,
            ("tenant.role_assigned", "tenant_membership"): 1,
        }
    )


@pytest.mark.asyncio
async def test_audit_events_capture_knowledge_skill_and_tool_management(
    audit_harness: AuditHarness,
) -> None:
    audit_client = audit_harness.client
    current_user = await _register_and_login(
        audit_client,
        f"audit-platform-{uuid4()}@example.com",
    )
    tenant_id = current_user["workspaces"][0]["id"]
    headers = {"X-Tenant-ID": tenant_id}

    knowledge_base = (
        await audit_client.post(
            "/api/v1/knowledge-bases",
            headers=headers,
            json={
                "name": "审计知识库",
                "description": "api_key=must-not-enter-audit",
            },
        )
    ).json()
    skill = (
        await audit_client.post(
            "/api/v1/skills",
            headers=headers,
            files={
                "bundle": (
                    "audit-helper.zip",
                    _skill_zip(description="password=must-not-enter-audit"),
                    "application/zip",
                )
            },
        )
    ).json()
    assert (
        await audit_client.post(
            f"/api/v1/skills/{skill['id']}/versions/1/publish",
            headers=headers,
        )
    ).status_code == 200
    server = (
        await audit_client.post(
            "/api/v1/mcp-servers",
            headers=headers,
            json={
                "name": "audit-mcp",
                "transport": "streamable_http",
                "endpoint": "https://mcp.internal.example/audit",
                "secret_reference": "vault://tenant/must-not-enter-audit",
            },
        )
    ).json()
    tool = (
        await audit_client.post(
            "/api/v1/tools",
            headers=headers,
            json={
                "server_id": server["id"],
                "name": "audit_lookup",
                "input_schema": {
                    "type": "object",
                    "properties": {"api_key": {"type": "string"}},
                },
                "risk_level": "read",
            },
        )
    ).json()
    assert (
        await audit_client.patch(
            f"/api/v1/tools/{tool['id']}",
            headers=headers,
            json={"enabled": False},
        )
    ).status_code == 200

    response = await audit_client.get("/api/v1/audit/events", headers=headers)

    assert response.status_code == 200
    events = response.json()
    actions = [event["action"] for event in events]
    for action in [
        "tool.updated",
        "tool.created",
        "mcp_server.created",
        "skill.published",
        "skill.created",
        "knowledge_base.created",
    ]:
        assert action in actions
    rendered = repr(events)
    assert "api_key=must-not-enter-audit" not in rendered
    assert "password=must-not-enter-audit" not in rendered
    assert "vault://tenant/must-not-enter-audit" not in rendered
    resources = {(event["action"], event["resource_id"]) for event in events}
    assert ("knowledge_base.created", knowledge_base["id"]) in resources
    assert ("skill.created", skill["id"]) in resources
    assert ("tool.created", tool["id"]) in resources


@pytest.mark.asyncio
async def test_audit_events_are_tenant_scoped_and_require_operations_permission(
    audit_harness: AuditHarness,
) -> None:
    audit_client = audit_harness.client
    first = await _register_and_login(audit_client, f"audit-first-{uuid4()}@example.com")
    first_tenant_id = first["workspaces"][0]["id"]
    first_headers = {"X-Tenant-ID": first_tenant_id}
    assert (
        await audit_client.post(
            "/api/v1/employees",
            headers=first_headers,
            json=_employee_definition("第一租户员工"),
        )
    ).status_code == 201

    second_credentials = {
        "email": f"audit-second-{uuid4()}@example.com",
        "password": "correct horse battery staple",
    }
    register_response = await audit_client.post(
        "/api/v1/auth/register",
        json=second_credentials,
    )
    assert register_response.status_code == 201
    login_response = await audit_client.post("/api/v1/auth/login", json=second_credentials)
    assert login_response.status_code == 200
    second = (await audit_client.get("/api/v1/auth/me")).json()
    async with audit_harness.session_factory() as session:
        session.add(
            TenantMembershipRecord(
                id=uuid4(),
                tenant_id=UUID(first_tenant_id),
                user_id=UUID(second["id"]),
                role="member",
                created_at=datetime.now(UTC),
            )
        )
        await session.commit()
    second_headers = {"X-Tenant-ID": second["workspaces"][0]["id"]}

    second_events = await audit_client.get("/api/v1/audit/events", headers=second_headers)
    assert second_events.status_code == 200
    assert all(
        event["tenant_id"] == second["workspaces"][0]["id"]
        for event in second_events.json()
    )

    forbidden = await audit_client.get("/api/v1/audit/events", headers=first_headers)
    assert forbidden.status_code == 403
    assert forbidden.json()["detail"]["code"] == "permission_denied"


@pytest.mark.asyncio
async def test_audit_export_supports_jsonl_without_sensitive_metadata(
    audit_harness: AuditHarness,
) -> None:
    audit_client = audit_harness.client
    current_user = await _register_and_login(
        audit_client,
        f"audit-export-{uuid4()}@example.com",
    )
    headers = {"X-Tenant-ID": current_user["workspaces"][0]["id"]}

    response = await audit_client.get(
        "/api/v1/audit/events/export",
        headers=headers,
        params={"format": "jsonl"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    lines = [line for line in response.text.splitlines() if line]
    assert lines
    assert "correct horse battery staple" not in response.text


@pytest.mark.asyncio
async def test_request_correlation_id_is_server_generated_and_copied_to_audit(
    audit_harness: AuditHarness,
) -> None:
    current_user = await _register_and_login(
        audit_harness.client,
        f"audit-correlation-{uuid4()}@example.com",
    )
    headers = {
        "X-Tenant-ID": current_user["workspaces"][0]["id"],
        "X-Request-ID": "caller-controlled-value",
    }

    created = await audit_harness.client.post(
        "/api/v1/employees",
        headers=headers,
        json=_employee_definition("关联 ID 员工"),
    )
    assert created.status_code == 201
    correlation_id = created.headers["X-Request-ID"]
    assert correlation_id != "caller-controlled-value"
    assert len(correlation_id) == 32

    events = (
        await audit_harness.client.get("/api/v1/audit/events", headers=headers)
    ).json()
    employee_event = next(event for event in events if event["action"] == "employee.created")
    assert employee_event["correlation_id"] == correlation_id


@pytest.mark.asyncio
async def test_audit_repository_redacts_nested_sensitive_metadata_and_detects_tampering(
    audit_harness: AuditHarness,
) -> None:
    current_user = await _register_and_login(
        audit_harness.client,
        f"audit-integrity-{uuid4()}@example.com",
    )
    tenant_id = UUID(current_user["workspaces"][0]["id"])
    actor_id = UUID(current_user["id"])

    async with audit_harness.session_factory() as session:
        event = await SqlAlchemyAuditEventRepository(session).add(
            AuditEventCreate(
                tenant_id=tenant_id,
                actor_user_id=actor_id,
                action="test.sensitive_metadata",
                resource_type="test",
                metadata={
                    "safe": "kept",
                    "password": "must-not-enter-audit",
                    "nested": {
                        "api_key": "must-not-enter-audit",
                        "visible": True,
                    },
                    "items": [{"token": "must-not-enter-audit", "count": 2}],
                },
            )
        )
        await session.commit()

    assert event.metadata == {
        "safe": "kept",
        "password": "[redacted]",
        "nested": {"api_key": "[redacted]", "visible": True},
        "items": [{"token": "[redacted]", "count": 2}],
    }
    assert event.sequence > 0
    assert len(event.event_hash) == 64

    headers = {"X-Tenant-ID": str(tenant_id)}
    integrity = await audit_harness.client.get(
        "/api/v1/audit/events/integrity",
        headers=headers,
    )
    assert integrity.status_code == 200
    assert integrity.json()["valid"] is True

    async with audit_harness.session_factory() as session:
        await session.execute(
            update(AuditEventRecord)
            .where(AuditEventRecord.id == event.id)
            .values(metadata_json={"safe": "tampered"})
        )
        await session.commit()

    integrity = await audit_harness.client.get(
        "/api/v1/audit/events/integrity",
        headers=headers,
    )
    assert integrity.status_code == 200
    assert integrity.json()["valid"] is False
    assert integrity.json()["checked_events"] == event.sequence - 1
    assert integrity.json()["first_invalid_sequence"] == event.sequence


@pytest.mark.asyncio
async def test_retention_purges_only_a_chain_prefix_and_preserves_verification(
    audit_harness: AuditHarness,
) -> None:
    current_user = await _register_and_login(
        audit_harness.client,
        f"audit-retention-{uuid4()}@example.com",
    )
    tenant_id = UUID(current_user["workspaces"][0]["id"])
    actor_id = UUID(current_user["id"])

    async with audit_harness.session_factory() as session:
        repository = SqlAlchemyAuditEventRepository(session)
        first = await repository.add(
            AuditEventCreate(
                tenant_id=tenant_id,
                actor_user_id=actor_id,
                action="retention.first",
                resource_type="test",
            )
        )
        second = await repository.add(
            AuditEventCreate(
                tenant_id=tenant_id,
                actor_user_id=actor_id,
                action="retention.second",
                resource_type="test",
            )
        )
        purged = await repository.purge_before(
            tenant_id=tenant_id,
            cutoff=second.occurred_at,
            limit=100,
        )
        verification = await repository.verify_integrity(tenant_id=tenant_id)
        await session.commit()

    assert purged >= 1
    assert verification.valid is True
    assert verification.checked_events >= 1
    async with audit_harness.session_factory() as session:
        remaining = await SqlAlchemyAuditEventRepository(session).list(
            tenant_id=tenant_id,
            limit=100,
        )
    assert first.id not in {event.id for event in remaining}
    assert second.id in {event.id for event in remaining}


@pytest.mark.asyncio
async def test_chain_head_detects_tail_deletion_and_retention_keeps_sequence_monotonic(
    audit_harness: AuditHarness,
) -> None:
    current_user = await _register_and_login(
        audit_harness.client,
        f"audit-chain-head-{uuid4()}@example.com",
    )
    tenant_id = UUID(current_user["workspaces"][0]["id"])
    actor_id = UUID(current_user["id"])

    async with audit_harness.session_factory() as session:
        repository = SqlAlchemyAuditEventRepository(session)
        last_before_purge = await repository.add(
            AuditEventCreate(
                tenant_id=tenant_id,
                actor_user_id=actor_id,
                action="chain.before_purge",
                resource_type="test",
            )
        )
        await repository.purge_before(
            tenant_id=tenant_id,
            cutoff=datetime.now(UTC),
            limit=10_000,
        )
        after_purge = await repository.add(
            AuditEventCreate(
                tenant_id=tenant_id,
                actor_user_id=actor_id,
                action="chain.after_purge",
                resource_type="test",
            )
        )
        assert after_purge.sequence == last_before_purge.sequence + 1
        assert after_purge.previous_hash == last_before_purge.event_hash
        assert (await repository.verify_integrity(tenant_id=tenant_id)).valid is True
        await session.commit()

    async with audit_harness.session_factory() as session:
        await session.execute(
            delete(AuditEventRecord).where(AuditEventRecord.id == after_purge.id)
        )
        await session.commit()

    async with audit_harness.session_factory() as session:
        verification = await SqlAlchemyAuditEventRepository(session).verify_integrity(
            tenant_id=tenant_id
        )
    assert verification.valid is False
    assert verification.first_invalid_sequence == after_purge.sequence


@pytest.mark.asyncio
async def test_audit_metadata_is_sanitized_at_repository_boundary(
    audit_harness: AuditHarness,
) -> None:
    audit_client = audit_harness.client
    current_user = await _register_and_login(
        audit_client,
        f"audit-redaction-{uuid4()}@example.com",
    )
    tenant_id = UUID(current_user["workspaces"][0]["id"])
    user_id = UUID(current_user["id"])
    headers = {"X-Tenant-ID": str(tenant_id)}

    async with audit_harness.session_factory() as session:
        await emit_audit_event(
            session,
            tenant_id=tenant_id,
            actor_user_id=user_id,
            action="redaction.boundary_test",
            resource_type="audit",
            resource_id=None,
            metadata={
                "safe": "kept",
                "api_key": "sk-must-not-enter-audit",
                "nested": {
                    "token": "tok-must-not-enter-audit",
                    "message": "password=must-not-enter-audit",
                },
                "items": [
                    {"authorization": "Bearer must-not-enter-audit"},
                    "cookie=must-not-enter-audit",
                ],
            },
        )
        await session.commit()

    response = await audit_client.get(
        "/api/v1/audit/events",
        headers=headers,
        params={"action": "redaction.boundary_test"},
    )

    assert response.status_code == 200
    [event] = response.json()
    assert event["metadata"]["safe"] == "kept"
    assert event["metadata"]["api_key"] == "[redacted]"
    assert event["metadata"]["nested"]["token"] == "[redacted]"
    assert event["metadata"]["nested"]["message"] == "password=[redacted]"
    assert event["metadata"]["items"] == [
        {"authorization": "[redacted]"},
        "cookie=[redacted]",
    ]
    assert "must-not-enter-audit" not in repr(event)
