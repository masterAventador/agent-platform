from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agent_platform.api.app import create_app
from agent_platform.config import AppSettings
from agent_platform.infrastructure.database.base import Base
from agent_platform.infrastructure.database.models import load_database_models
from agent_platform.infrastructure.database.repositories.employees import (
    EmployeeVersionRecord,
    SqlAlchemyEmployeeRepository,
    SqlAlchemyEmployeeVersionRepository,
)
from agent_platform.infrastructure.database.repositories.knowledge import KnowledgeBaseRecord
from agent_platform.infrastructure.database.repositories.runs import (
    RunCommandRecord,
    RunRecord,
)
from agent_platform.infrastructure.database.repositories.tenants import (
    SqlAlchemyTenantRepository,
    TenantMembershipRecord,
)
from agent_platform.platform.employees.entities import (
    Employee,
    EmployeeDraft,
    EmployeeStatus,
    EmployeeVisibility,
    RuntimeType,
)
from agent_platform.platform.tenants.entities import Tenant


class AllowAllRateLimiter:
    async def ensure_allowed(self, *, scope: str, key: str) -> None:
        del scope, key


@pytest_asyncio.fixture
async def employee_clients() -> AsyncIterator[tuple[AsyncClient, AsyncClient]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    load_database_models()
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    app = create_app(
        settings=AppSettings(auth_cookie_secure=False),
        session_factory=session_factory,
        auth_rate_limiter=AllowAllRateLimiter(),
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    transport = ASGITransport(app=app)
    async with (
        AsyncClient(transport=transport, base_url="http://testserver") as owner,
        AsyncClient(transport=transport, base_url="http://testserver") as outsider,
    ):
        yield owner, outsider

    await engine.dispose()


@pytest_asyncio.fixture
async def employee_api() -> AsyncIterator[
    tuple[FastAPI, async_sessionmaker[AsyncSession], AsyncClient]
]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    load_database_models()
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    app = create_app(
        settings=AppSettings(auth_cookie_secure=False),
        session_factory=session_factory,
        auth_rate_limiter=AllowAllRateLimiter(),
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        yield app, session_factory, client

    await engine.dispose()


async def register_and_login(client: AsyncClient, email: str) -> dict[str, object]:
    credentials = {"email": email, "password": "correct horse battery staple"}
    assert (await client.post("/api/v1/auth/register", json=credentials)).status_code == 201
    assert (await client.post("/api/v1/auth/login", json=credentials)).status_code == 200
    response = await client.get("/api/v1/auth/me")
    assert response.status_code == 200
    return response.json()


def employee_definition(
    *,
    name: str = "配置真实员工",
    work_mode: str = "autonomous",
    conversation: bool = True,
    scheduled_tasks: bool = False,
    file_upload: bool = False,
) -> dict[str, object]:
    return {
        "name": name,
        "role_description": "验证当前可运行配置",
        "work_mode": work_mode,
        "system_prompt": "仅使用当前可运行能力。",
        "model": {"kind": "gateway_alias", "alias": "general-purpose"},
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object"},
        "capabilities": {
            "conversation": conversation,
            "scheduled_tasks": scheduled_tasks,
            "file_upload": file_upload,
        },
    }


async def seed_knowledge_base(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    tenant_id: UUID,
    user_id: UUID,
    name: str = "员工知识库",
) -> UUID:
    knowledge_base_id = uuid4()
    async with session_factory() as session:
        session.add(
            KnowledgeBaseRecord(
                id=knowledge_base_id,
                tenant_id=tenant_id,
                name=name,
                description="用于员工绑定校验",
                provider="ragflow",
                provider_id=f"ragflow-{knowledge_base_id.hex}",
                created_by=user_id,
                created_at=datetime.now(UTC),
            )
        )
        await session.commit()
    return knowledge_base_id


def legacy_draft(*, name: str, work_mode: RuntimeType = RuntimeType.WORKFLOW) -> EmployeeDraft:
    return EmployeeDraft(
        name=name,
        avatar_url=None,
        role_description="历史配置",
        visibility=EmployeeVisibility.TENANT,
        runtime_type=work_mode,
        system_prompt="历史配置只允许读取。",
        model_settings={"kind": "gateway_alias", "alias": "general-purpose"},
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        capabilities={
            "conversation": True,
            "scheduled_tasks": True,
            "file_upload": True,
        },
        skill_ids=[],
        tool_ids=[],
        knowledge_base_ids=[],
        approval_policy={},
        release_strategy={"mode": "all"},
    )


@pytest.mark.asyncio
async def test_create_update_publish_and_list_employee_versions(
    employee_clients: tuple[AsyncClient, AsyncClient],
) -> None:
    owner, _ = employee_clients
    current_user = await register_and_login(owner, "employee-owner@example.com")
    workspace = current_user["workspaces"][0]
    assert workspace["role"] == "owner"

    create_response = await owner.post(
        "/api/v1/employees",
        headers={"X-Tenant-ID": workspace["id"]},
        json={
            "name": "研究助理",
            "avatar_url": "https://assets.example.com/researcher.png",
            "role_description": "负责企业资料调研与报告整理",
            "visibility": "tenant",
            "work_mode": "autonomous",
            "system_prompt": "先核实信息来源，再形成结构化报告。",
            "model": {"kind": "gateway_alias", "alias": "general-purpose"},
            "input_schema": {"type": "object", "required": ["topic"]},
            "output_schema": {"type": "object", "required": ["report"]},
            "capabilities": {
                "conversation": True,
                "scheduled_tasks": False,
                "file_upload": False,
            },
            "skill_ids": [],
            "tool_ids": [],
            "knowledge_base_ids": [],
            "approval_policy": {"high_risk_tools": "required"},
            "release_strategy": {"mode": "all"},
        },
    )

    assert create_response.status_code == 201
    employee = create_response.json()
    assert employee["name"] == "研究助理"
    assert employee["status"] == "draft"
    assert employee["published_version"] is None
    assert "runtime_type" not in employee
    assert employee["definition"]["avatar_url"].endswith("researcher.png")
    assert employee["definition"]["approval_policy"]["high_risk_tools"] == "required"

    update_response = await owner.put(
        f"/api/v1/employees/{employee['id']}",
        headers={"X-Tenant-ID": workspace["id"]},
        json={**create_response.json()["definition"], "name": "高级研究助理"},
    )
    assert update_response.status_code == 200
    assert update_response.json()["name"] == "高级研究助理"

    publish_response = await owner.post(
        f"/api/v1/employees/{employee['id']}/publish",
        headers={"X-Tenant-ID": workspace["id"]},
    )
    assert publish_response.status_code == 200
    assert publish_response.json()["status"] == "published"
    assert publish_response.json()["published_version"] == 1

    second_update = await owner.put(
        f"/api/v1/employees/{employee['id']}",
        headers={"X-Tenant-ID": workspace["id"]},
        json={**update_response.json()["definition"], "name": "首席研究助理"},
    )
    assert second_update.status_code == 200
    assert second_update.json()["status"] == "draft"
    assert second_update.json()["published_version"] == 1
    second_publish = await owner.post(
        f"/api/v1/employees/{employee['id']}/publish",
        headers={"X-Tenant-ID": workspace["id"]},
    )
    assert second_publish.status_code == 200
    assert second_publish.json()["published_version"] == 2

    versions_response = await owner.get(
        f"/api/v1/employees/{employee['id']}/versions",
        headers={"X-Tenant-ID": workspace["id"]},
    )
    assert versions_response.status_code == 200
    assert [version["version"] for version in versions_response.json()] == [2, 1]
    assert versions_response.json()[0]["definition"]["name"] == "首席研究助理"
    assert versions_response.json()[1]["definition"]["name"] == "高级研究助理"

    list_response = await owner.get(
        "/api/v1/employees",
        headers={"X-Tenant-ID": workspace["id"]},
    )
    assert list_response.status_code == 200
    assert [item["name"] for item in list_response.json()] == ["首席研究助理"]


@pytest.mark.asyncio
async def test_employee_is_not_visible_across_tenants(
    employee_clients: tuple[AsyncClient, AsyncClient],
) -> None:
    owner, outsider = employee_clients
    owner_user = await register_and_login(owner, "tenant-one@example.com")
    outsider_user = await register_and_login(outsider, "tenant-two@example.com")
    owner_workspace = owner_user["workspaces"][0]
    outsider_workspace = outsider_user["workspaces"][0]

    created = await owner.post(
        "/api/v1/employees",
        headers={"X-Tenant-ID": owner_workspace["id"]},
        json={
            "name": "租户一员工",
            "role_description": "仅属于租户一",
            "work_mode": "autonomous",
            "system_prompt": "按固定步骤执行。",
            "model": {"kind": "gateway_alias", "alias": "general-purpose"},
            "input_schema": {"type": "object"},
            "output_schema": {"type": "object"},
            "capabilities": {
                "conversation": False,
                "scheduled_tasks": False,
                "file_upload": False,
            },
        },
    )
    assert created.status_code == 201

    hidden = await outsider.get(
        f"/api/v1/employees/{created.json()['id']}",
        headers={"X-Tenant-ID": outsider_workspace["id"]},
    )
    assert hidden.status_code == 404

    outsider_list = await outsider.get(
        "/api/v1/employees",
        headers={"X-Tenant-ID": outsider_workspace["id"]},
    )
    assert outsider_list.status_code == 200
    assert outsider_list.json() == []


@pytest.mark.parametrize(
    ("work_mode", "scheduled_tasks", "file_upload"),
    [
        ("workflow", False, False),
        ("hybrid", False, False),
        ("autonomous", True, False),
    ],
)
@pytest.mark.asyncio
async def test_create_rejects_configuration_not_currently_runnable(
    employee_clients: tuple[AsyncClient, AsyncClient],
    work_mode: str,
    scheduled_tasks: bool,
    file_upload: bool,
) -> None:
    owner, _ = employee_clients
    current_user = await register_and_login(owner, "employee-write-contract@example.com")
    tenant_id = current_user["workspaces"][0]["id"]

    response = await owner.post(
        "/api/v1/employees",
        headers={"X-Tenant-ID": tenant_id},
        json=employee_definition(
            work_mode=work_mode,
            scheduled_tasks=scheduled_tasks,
            file_upload=file_upload,
        ),
    )

    assert response.status_code == 422


@pytest.mark.parametrize(
    ("work_mode", "scheduled_tasks", "file_upload"),
    [
        ("workflow", False, False),
        ("hybrid", False, False),
        ("autonomous", True, False),
    ],
)
@pytest.mark.asyncio
async def test_update_rejects_configuration_not_currently_runnable(
    employee_clients: tuple[AsyncClient, AsyncClient],
    work_mode: str,
    scheduled_tasks: bool,
    file_upload: bool,
) -> None:
    owner, _ = employee_clients
    current_user = await register_and_login(owner, "employee-update-contract@example.com")
    tenant_id = current_user["workspaces"][0]["id"]
    created = await owner.post(
        "/api/v1/employees",
        headers={"X-Tenant-ID": tenant_id},
        json=employee_definition(),
    )
    assert created.status_code == 201

    response = await owner.put(
        f"/api/v1/employees/{created.json()['id']}",
        headers={"X-Tenant-ID": tenant_id},
        json=employee_definition(
            work_mode=work_mode,
            scheduled_tasks=scheduled_tasks,
            file_upload=file_upload,
        ),
    )

    assert response.status_code == 422


def test_openapi_exposes_current_employee_write_contract(
    employee_api: tuple[FastAPI, async_sessionmaker[AsyncSession], AsyncClient],
) -> None:
    app, _, _ = employee_api
    schema = app.openapi()
    definition = schema["components"]["schemas"]["EmployeeDefinitionRequest"]
    capabilities_ref = definition["properties"]["capabilities"]["$ref"]
    capabilities = schema["components"]["schemas"][capabilities_ref.rsplit("/", 1)[-1]]

    assert definition["properties"]["work_mode"]["const"] == "autonomous"
    assert capabilities["properties"]["scheduled_tasks"]["const"] is False
    assert capabilities["properties"]["file_upload"]["type"] == "boolean"
    assert capabilities["properties"]["conversation"]["type"] == "boolean"


@pytest.mark.asyncio
async def test_autonomous_employee_accepts_file_upload_capability(
    employee_clients: tuple[AsyncClient, AsyncClient],
) -> None:
    owner, _ = employee_clients
    current_user = await register_and_login(owner, "employee-file-upload@example.com")
    tenant_id = current_user["workspaces"][0]["id"]

    response = await owner.post(
        "/api/v1/employees",
        headers={"X-Tenant-ID": tenant_id},
        json=employee_definition(file_upload=True),
    )

    assert response.status_code == 201
    assert response.json()["definition"]["capabilities"]["file_upload"] is True


@pytest.mark.asyncio
async def test_employee_knowledge_base_references_must_be_bindable_in_current_tenant(
    employee_api: tuple[FastAPI, async_sessionmaker[AsyncSession], AsyncClient],
) -> None:
    _, session_factory, client = employee_api
    current_user = await register_and_login(client, "employee-knowledge-bind@example.com")
    tenant_id = UUID(current_user["workspaces"][0]["id"])
    user_id = UUID(current_user["id"])
    headers = {"X-Tenant-ID": str(tenant_id)}
    bindable_id = await seed_knowledge_base(
        session_factory,
        tenant_id=tenant_id,
        user_id=user_id,
        name="可绑定知识库",
    )

    missing_id = uuid4()
    rejected_create = await client.post(
        "/api/v1/employees",
        headers=headers,
        json={
            **employee_definition(name="缺失知识库员工"),
            "knowledge_base_ids": [str(missing_id)],
        },
    )
    assert rejected_create.status_code == 422
    assert rejected_create.json()["detail"] == {
        "code": "employee_knowledge_base_not_bindable",
        "message": "数字员工只能绑定当前企业可用的知识库",
    }

    created = await client.post(
        "/api/v1/employees",
        headers=headers,
        json={
            **employee_definition(name="可绑定知识库员工"),
            "knowledge_base_ids": [str(bindable_id)],
        },
    )
    assert created.status_code == 201
    assert created.json()["definition"]["knowledge_base_ids"] == [str(bindable_id)]

    async with session_factory() as session:
        record = await session.get(KnowledgeBaseRecord, bindable_id)
        assert record is not None
        await session.delete(record)
        await session.commit()

    rejected_publish = await client.post(
        f"/api/v1/employees/{created.json()['id']}/publish",
        headers=headers,
    )
    assert rejected_publish.status_code == 422
    assert rejected_publish.json()["detail"]["code"] == "employee_knowledge_base_not_bindable"


DEFAULT_KNOWLEDGE_RETRIEVAL_SNAPSHOT: dict[str, object] = {
    "page_size": 5,
    "similarity_threshold": 0.2,
    "vector_similarity_weight": 0.3,
    "top_k": 1024,
    "keyword": False,
    "rerank_id": None,
    "metadata_condition": None,
}


@pytest.mark.asyncio
async def test_knowledge_retrieval_config_full_chain_create_publish_and_version_freeze(
    employee_api: tuple[FastAPI, async_sessionmaker[AsyncSession], AsyncClient],
) -> None:
    _, session_factory, client = employee_api
    current_user = await register_and_login(client, "employee-retrieval-config@example.com")
    tenant_id = UUID(current_user["workspaces"][0]["id"])
    user_id = UUID(current_user["id"])
    headers = {"X-Tenant-ID": str(tenant_id)}
    knowledge_base_id = await seed_knowledge_base(
        session_factory,
        tenant_id=tenant_id,
        user_id=user_id,
        name="检索配置知识库",
    )

    configured = {
        "page_size": 8,
        "similarity_threshold": 0.35,
        "vector_similarity_weight": 0.7,
        "top_k": 256,
        "keyword": True,
        "rerank_id": "BAAI/bge-reranker-v2-m3",
        "metadata_condition": {
            "logic": "and",
            "conditions": [
                {"name": "department", "comparison_operator": "=", "value": "HR"},
            ],
        },
    }
    created = await client.post(
        "/api/v1/employees",
        headers=headers,
        json={
            **employee_definition(name="检索配置员工"),
            "knowledge_base_ids": [str(knowledge_base_id)],
            "knowledge_retrieval": configured,
        },
    )
    assert created.status_code == 201
    assert created.json()["definition"]["knowledge_retrieval"] == configured
    employee_id = created.json()["id"]

    published = await client.post(f"/api/v1/employees/{employee_id}/publish", headers=headers)
    assert published.status_code == 200
    versions = await client.get(f"/api/v1/employees/{employee_id}/versions", headers=headers)
    assert versions.status_code == 200
    frozen = versions.json()[0]["definition"]["knowledge_retrieval"]
    assert frozen == configured


@pytest.mark.asyncio
async def test_knowledge_retrieval_defaults_apply_when_config_is_omitted(
    employee_api: tuple[FastAPI, async_sessionmaker[AsyncSession], AsyncClient],
) -> None:
    _, _, client = employee_api
    current_user = await register_and_login(client, "employee-retrieval-default@example.com")
    tenant_id = UUID(current_user["workspaces"][0]["id"])
    headers = {"X-Tenant-ID": str(tenant_id)}

    created = await client.post(
        "/api/v1/employees",
        headers=headers,
        json=employee_definition(name="默认检索配置员工"),
    )
    assert created.status_code == 201
    assert (
        created.json()["definition"]["knowledge_retrieval"]
        == DEFAULT_KNOWLEDGE_RETRIEVAL_SNAPSHOT
    )

    published = await client.post(
        f"/api/v1/employees/{created.json()['id']}/publish", headers=headers
    )
    assert published.status_code == 200
    versions = await client.get(
        f"/api/v1/employees/{created.json()['id']}/versions", headers=headers
    )
    assert (
        versions.json()[0]["definition"]["knowledge_retrieval"]
        == DEFAULT_KNOWLEDGE_RETRIEVAL_SNAPSHOT
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_config",
    [
        {"page_size": 0},
        {"unknown_option": True},
        {"metadata_condition": {"logic": "xor", "conditions": []}},
        {
            "metadata_condition": {
                "conditions": [{"name": "a", "comparison_operator": "matches", "value": "x"}],
            }
        },
        "not-an-object",
    ],
)
async def test_invalid_knowledge_retrieval_config_is_rejected_fail_closed(
    employee_api: tuple[FastAPI, async_sessionmaker[AsyncSession], AsyncClient],
    invalid_config: object,
) -> None:
    _, _, client = employee_api
    current_user = await register_and_login(client, "employee-retrieval-invalid@example.com")
    tenant_id = UUID(current_user["workspaces"][0]["id"])
    headers = {"X-Tenant-ID": str(tenant_id)}

    rejected = await client.post(
        "/api/v1/employees",
        headers=headers,
        json={
            **employee_definition(name="非法检索配置员工"),
            "knowledge_retrieval": invalid_config,
        },
    )
    assert rejected.status_code == 422
    detail = rejected.json()["detail"]
    assert detail["code"] == "invalid_knowledge_retrieval"
    assert "path" in detail
    assert detail["message"]

    assert (await client.get("/api/v1/employees", headers=headers)).json() == []


@pytest.mark.asyncio
async def test_legacy_draft_is_readable_but_publish_fails_before_version_creation(
    employee_api: tuple[FastAPI, async_sessionmaker[AsyncSession], AsyncClient],
) -> None:
    _, session_factory, client = employee_api
    current_user = await register_and_login(client, "legacy-draft@example.com")
    tenant_id = UUID(current_user["workspaces"][0]["id"])
    user_id = UUID(current_user["id"])
    employee = Employee.create(
        tenant_id=tenant_id,
        created_by=user_id,
        draft=legacy_draft(name="历史流程员工"),
    )
    async with session_factory() as session:
        await SqlAlchemyEmployeeRepository(session).add(employee)
        await session.commit()

    response = await client.get(
        f"/api/v1/employees/{employee.id}",
        headers={"X-Tenant-ID": str(tenant_id)},
    )
    assert response.status_code == 200
    assert response.json()["definition"]["work_mode"] == "workflow"
    assert response.json()["definition"]["capabilities"]["file_upload"] is True

    publish = await client.post(
        f"/api/v1/employees/{employee.id}/publish",
        headers={"X-Tenant-ID": str(tenant_id)},
    )
    assert publish.status_code == 409
    assert publish.json()["detail"]["code"] == "employee_configuration_unavailable"
    async with session_factory() as session:
        persisted = await SqlAlchemyEmployeeRepository(session).get(
            tenant_id=tenant_id,
            employee_id=employee.id,
        )
        assert persisted is not None and persisted.status is EmployeeStatus.DRAFT
        version_count = (
            await session.execute(
                select(func.count())
                .select_from(EmployeeVersionRecord)
                .where(EmployeeVersionRecord.employee_id == employee.id)
            )
        ).scalar_one()
        assert version_count == 0


@pytest.mark.asyncio
async def test_legacy_published_employee_cannot_create_run_or_command(
    employee_api: tuple[FastAPI, async_sessionmaker[AsyncSession], AsyncClient],
) -> None:
    _, session_factory, client = employee_api
    current_user = await register_and_login(client, "legacy-published@example.com")
    tenant_id = UUID(current_user["workspaces"][0]["id"])
    user_id = UUID(current_user["id"])
    draft = Employee.create(
        tenant_id=tenant_id,
        created_by=user_id,
        draft=legacy_draft(name="历史混合员工", work_mode=RuntimeType.HYBRID),
    )
    published, version = draft.publish(published_by=user_id)
    async with session_factory() as session:
        await SqlAlchemyEmployeeRepository(session).add(published)
        await SqlAlchemyEmployeeVersionRepository(session).add(version)
        await session.commit()

    readable = await client.get(
        f"/api/v1/employees/{published.id}",
        headers={"X-Tenant-ID": str(tenant_id)},
    )
    assert readable.status_code == 200
    assert readable.json()["definition"]["work_mode"] == "hybrid"
    assert readable.json()["definition"]["capabilities"]["scheduled_tasks"] is True

    response = await client.post(
        f"/api/v1/employees/{published.id}/runs",
        headers={"X-Tenant-ID": str(tenant_id)},
        json={"input": {"task": "must not start"}},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "employee_configuration_unavailable"
    async with session_factory() as session:
        run_count = (
            await session.execute(
                select(func.count())
                .select_from(RunRecord)
                .where(RunRecord.employee_id == published.id)
            )
        ).scalar_one()
        command_count = (
            await session.execute(select(func.count()).select_from(RunCommandRecord))
        ).scalar_one()
        assert run_count == command_count == 0


@pytest.mark.asyncio
async def test_multiple_workspaces_require_exact_tenant_header(
    employee_api: tuple[FastAPI, async_sessionmaker[AsyncSession], AsyncClient],
) -> None:
    _, session_factory, client = employee_api
    current_user = await register_and_login(client, "multi-workspace-employee@example.com")
    user_id = UUID(current_user["id"])
    original_tenant_id = UUID(current_user["workspaces"][0]["id"])
    second = Tenant.create(name="第二工作区", slug=f"second-{uuid4().hex}")
    foreign = Tenant.create(name="无权工作区", slug=f"foreign-{uuid4().hex}")
    async with session_factory() as session:
        tenants = SqlAlchemyTenantRepository(session)
        await tenants.add(second)
        await tenants.add(foreign)
        session.add(
            TenantMembershipRecord(
                id=uuid4(),
                tenant_id=second.id,
                user_id=user_id,
                role="owner",
                created_at=datetime.now(UTC),
            )
        )
        await session.commit()

    missing_header = await client.post("/api/v1/employees", json=employee_definition())
    assert missing_header.status_code == 400
    assert missing_header.json()["detail"]["code"] == "tenant_required"
    foreign_header = await client.post(
        "/api/v1/employees",
        headers={"X-Tenant-ID": str(foreign.id)},
        json=employee_definition(),
    )
    assert foreign_header.status_code == 404
    created = await client.post(
        "/api/v1/employees",
        headers={"X-Tenant-ID": str(second.id)},
        json=employee_definition(),
    )
    assert created.status_code == 201
    assert created.json()["tenant_id"] == str(second.id)
    original_list = await client.get(
        "/api/v1/employees",
        headers={"X-Tenant-ID": str(original_tenant_id)},
    )
    second_list = await client.get(
        "/api/v1/employees",
        headers={"X-Tenant-ID": str(second.id)},
    )
    assert original_list.json() == []
    assert [item["id"] for item in second_list.json()] == [created.json()["id"]]


@pytest.mark.asyncio
async def test_admin_manages_employees_while_member_only_sees_published_tenant_entries(
    employee_api: tuple[FastAPI, async_sessionmaker[AsyncSession], AsyncClient],
) -> None:
    _, session_factory, client = employee_api
    owner = await register_and_login(client, "employee-rbac-owner@example.com")
    tenant_id = UUID(owner["workspaces"][0]["id"])
    headers = {"X-Tenant-ID": str(tenant_id)}

    draft = (
        await client.post(
            "/api/v1/employees",
            headers=headers,
            json=employee_definition(name="RBAC 草稿员工"),
        )
    ).json()
    published = (
        await client.post(
            "/api/v1/employees",
            headers=headers,
            json=employee_definition(name="RBAC 已发布员工"),
        )
    ).json()
    await client.post(f"/api/v1/employees/{published['id']}/publish", headers=headers)
    private = (
        await client.post(
            "/api/v1/employees",
            headers=headers,
            json={
                **employee_definition(name="RBAC 私有员工"),
                "visibility": "private",
            },
        )
    ).json()
    await client.post(f"/api/v1/employees/{private['id']}/publish", headers=headers)

    async def join_workspace(email: str, role: str) -> dict[str, object]:
        await client.post("/api/v1/auth/logout")
        user = await register_and_login(client, email)
        async with session_factory() as session:
            session.add(
                TenantMembershipRecord(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    user_id=UUID(user["id"]),
                    role=role,
                    created_at=datetime.now(UTC),
                )
            )
            await session.commit()
        return user

    await join_workspace("employee-rbac-admin@example.com", "admin")
    admin_update = await client.put(
        f"/api/v1/employees/{draft['id']}",
        headers=headers,
        json=employee_definition(name="RBAC 管理员已更新草稿"),
    )
    assert admin_update.status_code == 200

    await join_workspace("employee-rbac-member@example.com", "member")
    listed = await client.get("/api/v1/employees", headers=headers)
    assert listed.status_code == 200
    assert [employee["id"] for employee in listed.json()] == [published["id"]]
    assert (
        await client.get(f"/api/v1/employees/{published['id']}", headers=headers)
    ).status_code == 200
    assert (
        await client.get(f"/api/v1/employees/{draft['id']}", headers=headers)
    ).status_code == 404
    assert (
        await client.get(f"/api/v1/employees/{private['id']}", headers=headers)
    ).status_code == 404
    assert (
        await client.get(
            f"/api/v1/employees/{published['id']}/versions",
            headers=headers,
        )
    ).status_code == 403


@pytest.mark.asyncio
async def test_employee_model_is_strictly_a_provider_neutral_gateway_alias(
    employee_clients: tuple[AsyncClient, AsyncClient],
) -> None:
    owner, _ = employee_clients
    current_user = await register_and_login(owner, "model-contract-owner@example.com")
    headers = {"X-Tenant-ID": current_user["workspaces"][0]["id"]}

    invalid_models = [
        {"provider": "openai", "name": "gpt-5"},
        {"provider": "dashscope", "name": "qwen-plus"},
        {"kind": "gateway_alias", "alias": "dashscope/qwen-plus"},
        {"kind": "gateway_alias", "alias": "volcengine:doubao"},
        {"kind": "gateway_alias", "alias": "UpperCase"},
        {"kind": "gateway_alias", "alias": "-leading-separator"},
        {"kind": "gateway_alias", "alias": "a" * 65},
        {"kind": "gateway_alias", "alias": "unconfigured-alias"},
        {"kind": "direct_provider", "alias": "general-purpose"},
        {"kind": "gateway_alias", "alias": "general-purpose", "api_key": "secret"},
        {"kind": "gateway_alias", "alias": "general-purpose", "base_url": "https://llm"},
    ]
    for index, model in enumerate(invalid_models):
        response = await owner.post(
            "/api/v1/employees",
            headers=headers,
            json={**employee_definition(name=f"非法模型 {index}"), "model": model},
        )
        assert response.status_code == 422

    valid = await owner.post(
        "/api/v1/employees",
        headers=headers,
        json={
            **employee_definition(name="网关别名员工"),
            "model": {"kind": "gateway_alias", "alias": "general-purpose"},
        },
    )
    assert valid.status_code == 201
    assert valid.json()["definition"]["model"] == {
        "kind": "gateway_alias",
        "alias": "general-purpose",
    }


@pytest.mark.asyncio
async def test_model_allowlist_does_not_bypass_authentication(
    employee_clients: tuple[AsyncClient, AsyncClient],
) -> None:
    _, unauthenticated = employee_clients

    response = await unauthenticated.post(
        "/api/v1/employees",
        json={
            **employee_definition(name="未认证模型探测"),
            "model": {"kind": "gateway_alias", "alias": "unconfigured-alias"},
        },
    )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_model_alias_allowlist_is_authoritative_for_update_and_publish(
    employee_api: tuple[FastAPI, async_sessionmaker[AsyncSession], AsyncClient],
) -> None:
    app, _, owner = employee_api
    current_user = await register_and_login(owner, "model-allowlist-owner@example.com")
    headers = {"X-Tenant-ID": current_user["workspaces"][0]["id"]}
    created = await owner.post(
        "/api/v1/employees",
        headers=headers,
        json=employee_definition(name="模型白名单员工"),
    )
    assert created.status_code == 201
    employee_id = created.json()["id"]

    rejected_update = await owner.put(
        f"/api/v1/employees/{employee_id}",
        headers=headers,
        json={
            **employee_definition(name="模型白名单员工"),
            "model": {"kind": "gateway_alias", "alias": "unconfigured-alias"},
        },
    )
    assert rejected_update.status_code == 422
    assert rejected_update.json()["detail"] == {
        "code": "employee_model_alias_unavailable",
        "message": "所选模型当前未由平台启用",
    }

    app.state.settings.llm_gateway_allowed_aliases = frozenset({"temporary-test"})
    rejected_publish = await owner.post(
        f"/api/v1/employees/{employee_id}/publish",
        headers=headers,
    )
    assert rejected_publish.status_code == 422
    assert rejected_publish.json()["detail"]["code"] == ("employee_model_alias_unavailable")
