from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agent_platform.api.app import create_app
from agent_platform.config import AppSettings
from agent_platform.infrastructure.database.base import Base
from agent_platform.infrastructure.database.models import load_database_models
from agent_platform.infrastructure.database.repositories.employees import EmployeeVersionRecord


class AllowAllRateLimiter:
    async def ensure_allowed(self, *, scope: str, key: str) -> None:
        del scope, key


class MemoryArtifactStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def put(self, *, key: str, content: bytes, media_type: str) -> None:
        del media_type
        self.objects[key] = content

    async def get(self, *, key: str) -> bytes:
        return self.objects[key]

    async def delete(self, *, key: str) -> None:
        self.objects.pop(key, None)


@dataclass(frozen=True)
class DynamicIOHarness:
    client: AsyncClient
    session_factory: async_sessionmaker[AsyncSession]


@pytest_asyncio.fixture
async def dynamic_io_harness() -> AsyncIterator[DynamicIOHarness]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    load_database_models()
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    app = create_app(
        settings=AppSettings(auth_cookie_secure=False),
        session_factory=session_factory,
        auth_rate_limiter=AllowAllRateLimiter(),
        artifact_storage=MemoryArtifactStorage(),
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        yield DynamicIOHarness(client=client, session_factory=session_factory)
    await engine.dispose()


@pytest_asyncio.fixture
async def dynamic_io_client(dynamic_io_harness: DynamicIOHarness) -> AsyncClient:
    return dynamic_io_harness.client


async def _register_and_login(client: AsyncClient, email: str) -> dict[str, Any]:
    credentials = {"email": email, "password": "correct horse battery staple"}
    assert (await client.post("/api/v1/auth/register", json=credentials)).status_code == 201
    assert (await client.post("/api/v1/auth/login", json=credentials)).status_code == 200
    response = await client.get("/api/v1/auth/me")
    assert response.status_code == 200
    return response.json()


def _employee_definition(
    *,
    name: str,
    input_schema: dict[str, object],
    output_schema: dict[str, object] | None = None,
    file_upload: bool = False,
) -> dict[str, object]:
    return {
        "name": name,
        "role_description": "验证动态输入输出契约",
        "work_mode": "autonomous",
        "system_prompt": "严格按结构化输入执行。",
        "model": {"kind": "gateway_alias", "alias": "general-purpose"},
        "input_schema": input_schema,
        "output_schema": output_schema or {"type": "object"},
        "capabilities": {
            "conversation": False,
            "scheduled_tasks": False,
            "file_upload": file_upload,
        },
    }


@pytest.mark.asyncio
async def test_employee_definition_rejects_invalid_json_schemas(
    dynamic_io_client: AsyncClient,
) -> None:
    current_user = await _register_and_login(dynamic_io_client, "dynamic-schema@example.com")
    headers = {"X-Tenant-ID": current_user["workspaces"][0]["id"]}

    response = await dynamic_io_client.post(
        "/api/v1/employees",
        headers=headers,
        json=_employee_definition(
            name="无效 Schema 员工",
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {"priority": {"type": "definitely-not-a-json-schema-type"}},
            },
        ),
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_employee_schema"


@pytest.mark.asyncio
async def test_employee_definition_rejects_file_input_when_file_upload_is_disabled(
    dynamic_io_client: AsyncClient,
) -> None:
    current_user = await _register_and_login(dynamic_io_client, "dynamic-file-schema@example.com")
    headers = {"X-Tenant-ID": current_user["workspaces"][0]["id"]}

    response = await dynamic_io_client.post(
        "/api/v1/employees",
        headers=headers,
        json=_employee_definition(
            name="未启用文件能力的文件表单员工",
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["source_file"],
                "properties": {
                    "source_file": {
                        "type": "string",
                        "title": "资料文件",
                        "x-agent-platform-control": "file",
                    }
                },
            },
        ),
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "invalid_employee_schema"
    assert detail["schema"] == "input_schema"
    assert detail["path"] == ["properties", "source_file", "x-agent-platform-control"]


@pytest.mark.parametrize(
    ("field_schema", "expected_path"),
    [
        (
            {
                "type": "string",
                "x-agent-platform-control": "file",
                "pattern": "^allowed-",
            },
            ["properties", "source_file", "pattern"],
        ),
        (
            {
                "type": "string",
                "x-agent-platform-control": "file",
                "enum": ["file-1"],
            },
            ["properties", "source_file", "enum"],
        ),
        (
            {
                "type": "string",
                "x-agent-platform-control": "file",
                "minLength": 3,
            },
            ["properties", "source_file", "minLength"],
        ),
        (
            {
                "type": "string",
                "x-agent-platform-control": "file",
                "maxLength": 128,
            },
            ["properties", "source_file", "maxLength"],
        ),
    ],
)
@pytest.mark.asyncio
async def test_employee_definition_rejects_file_constraints_not_supported_by_dynamic_form(
    dynamic_io_client: AsyncClient,
    field_schema: dict[str, object],
    expected_path: list[str],
) -> None:
    current_user = await _register_and_login(
        dynamic_io_client,
        f"dynamic-file-constraint-{uuid4()}@example.com",
    )
    headers = {"X-Tenant-ID": current_user["workspaces"][0]["id"]}

    response = await dynamic_io_client.post(
        "/api/v1/employees",
        headers=headers,
        json=_employee_definition(
            name=f"文件约束员工 {uuid4()}",
            file_upload=True,
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["source_file"],
                "properties": {"source_file": field_schema},
            },
        ),
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "invalid_employee_schema"
    assert detail["schema"] == "input_schema"
    assert detail["path"] == expected_path


@pytest.mark.parametrize(
    ("item_schema", "expected_path"),
    [
        ({"type": "string", "format": "binary"}, ["properties", "files", "items", "type"]),
        (
            {"type": "string", "contentMediaType": "application/pdf"},
            ["properties", "files", "items", "type"],
        ),
        (
            {"type": "string", "x-agent-platform-control": "file"},
            ["properties", "files", "items", "type"],
        ),
    ],
)
@pytest.mark.asyncio
async def test_employee_definition_rejects_file_semantics_inside_array_items(
    dynamic_io_client: AsyncClient,
    item_schema: dict[str, object],
    expected_path: list[str],
) -> None:
    current_user = await _register_and_login(
        dynamic_io_client,
        f"dynamic-array-file-{uuid4()}@example.com",
    )
    headers = {"X-Tenant-ID": current_user["workspaces"][0]["id"]}

    response = await dynamic_io_client.post(
        "/api/v1/employees",
        headers=headers,
        json=_employee_definition(
            name=f"数组文件员工 {uuid4()}",
            file_upload=True,
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "files": {
                        "type": "array",
                        "items": item_schema,
                    }
                },
            },
        ),
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "invalid_employee_schema"
    assert detail["schema"] == "input_schema"
    assert detail["path"] == expected_path


@pytest.mark.asyncio
async def test_employee_definition_rejects_input_schema_that_the_dynamic_form_cannot_render(
    dynamic_io_client: AsyncClient,
) -> None:
    current_user = await _register_and_login(dynamic_io_client, "dynamic-nested-schema@example.com")
    headers = {"X-Tenant-ID": current_user["workspaces"][0]["id"]}

    response = await dynamic_io_client.post(
        "/api/v1/employees",
        headers=headers,
        json=_employee_definition(
            name="嵌套对象输入员工",
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["profile"],
                "properties": {
                    "profile": {
                        "type": "object",
                        "required": ["name"],
                        "properties": {"name": {"type": "string"}},
                    }
                },
            },
        ),
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "invalid_employee_schema"
    assert detail["schema"] == "input_schema"
    assert detail["path"] == ["properties", "profile", "type"]


@pytest.mark.parametrize(
    "input_schema",
    [
        {
            "type": "object",
            "properties": {"topic": {"type": "string"}},
        },
        {
            "type": "object",
            "additionalProperties": True,
            "properties": {"topic": {"type": "string"}},
        },
    ],
)
@pytest.mark.asyncio
async def test_employee_definition_rejects_dynamic_properties_without_strict_additional_properties(
    dynamic_io_client: AsyncClient,
    input_schema: dict[str, object],
) -> None:
    current_user = await _register_and_login(
        dynamic_io_client,
        f"dynamic-additional-properties-{uuid4()}@example.com",
    )
    headers = {"X-Tenant-ID": current_user["workspaces"][0]["id"]}

    response = await dynamic_io_client.post(
        "/api/v1/employees",
        headers=headers,
        json=_employee_definition(
            name=f"额外字段拒绝员工 {uuid4()}",
            input_schema=input_schema,
        ),
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "invalid_employee_schema"
    assert detail["schema"] == "input_schema"
    assert detail["path"] == ["additionalProperties"]


@pytest.mark.parametrize(
    ("input_schema", "expected_path"),
    [
        (
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {"topic": {"type": "string", "const": "固定主题"}},
            },
            ["properties", "topic", "const"],
        ),
        (
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "topic": {
                        "type": "string",
                        "oneOf": [{"const": "A"}, {"const": "B"}],
                    }
                },
            },
            ["properties", "topic", "oneOf"],
        ),
        (
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "topic": {
                        "type": "string",
                        "anyOf": [{"pattern": "^A"}, {"pattern": "^B"}],
                    }
                },
            },
            ["properties", "topic", "anyOf"],
        ),
        (
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "topic": {
                        "type": "string",
                        "allOf": [{"minLength": 2}, {"maxLength": 10}],
                    }
                },
            },
            ["properties", "topic", "allOf"],
        ),
        (
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {"topic": {"type": "string"}},
                "dependentRequired": {"topic": ["priority"]},
            },
            ["dependentRequired"],
        ),
        (
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {"topic": {"type": "string"}},
                "patternProperties": {"^x-": {"type": "string"}},
            },
            ["patternProperties"],
        ),
        (
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {"topic": {"type": "string"}},
                "if": {"required": ["topic"]},
                "then": {"required": ["priority"]},
                "else": {"required": ["fallback"]},
            },
            ["if"],
        ),
    ],
)
@pytest.mark.asyncio
async def test_employee_definition_rejects_json_schema_keywords_not_supported_by_dynamic_form(
    dynamic_io_client: AsyncClient,
    input_schema: dict[str, object],
    expected_path: list[str],
) -> None:
    current_user = await _register_and_login(
        dynamic_io_client,
        f"dynamic-unsupported-{uuid4()}@example.com",
    )
    headers = {"X-Tenant-ID": current_user["workspaces"][0]["id"]}

    response = await dynamic_io_client.post(
        "/api/v1/employees",
        headers=headers,
        json=_employee_definition(
            name=f"不支持关键字员工 {uuid4()}",
            input_schema=input_schema,
        ),
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "invalid_employee_schema"
    assert detail["schema"] == "input_schema"
    assert detail["path"] == expected_path


@pytest.mark.parametrize(
    ("input_schema", "expected_path"),
    [
        (
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {"topic": {"type": "string", "pattern": "(?P<topic>a)"}},
            },
            ["properties", "topic", "pattern"],
        ),
        (
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "topics": {
                        "type": "array",
                        "items": {"type": "string", "pattern": "(?P<topic>a)"},
                    }
                },
            },
            ["properties", "topics", "items", "pattern"],
        ),
    ],
)
@pytest.mark.asyncio
async def test_employee_definition_rejects_pattern_that_browser_regexp_cannot_compile(
    dynamic_io_client: AsyncClient,
    input_schema: dict[str, object],
    expected_path: list[str],
) -> None:
    current_user = await _register_and_login(
        dynamic_io_client,
        f"dynamic-incompatible-pattern-{uuid4()}@example.com",
    )
    headers = {"X-Tenant-ID": current_user["workspaces"][0]["id"]}

    response = await dynamic_io_client.post(
        "/api/v1/employees",
        headers=headers,
        json=_employee_definition(
            name=f"不兼容正则员工 {uuid4()}",
            input_schema=input_schema,
        ),
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "invalid_employee_schema"
    assert detail["schema"] == "input_schema"
    assert detail["path"] == expected_path


@pytest.mark.asyncio
async def test_create_run_validates_input_against_published_version_schema(
    dynamic_io_client: AsyncClient,
) -> None:
    current_user = await _register_and_login(dynamic_io_client, "dynamic-run@example.com")
    tenant_id = current_user["workspaces"][0]["id"]
    headers = {"X-Tenant-ID": tenant_id}
    output_schema = {
        "type": "object",
        "x-agent-platform-view": "cards",
        "required": ["cards"],
        "properties": {
            "cards": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["title"],
                    "properties": {"title": {"type": "string"}},
                },
            },
        },
    }
    create = await dynamic_io_client.post(
        "/api/v1/employees",
        headers=headers,
        json=_employee_definition(
            name="动态输入员工",
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["topic", "priority"],
                "properties": {
                    "topic": {"type": "string", "minLength": 1},
                    "priority": {"type": "string", "enum": ["low", "high"]},
                },
            },
            output_schema=output_schema,
        ),
    )
    assert create.status_code == 201
    employee = create.json()
    assert (
        await dynamic_io_client.post(
            f"/api/v1/employees/{employee['id']}/publish",
            headers=headers,
        )
    ).status_code == 200

    invalid = await dynamic_io_client.post(
        f"/api/v1/employees/{employee['id']}/runs",
        headers=headers,
        json={"input": {"topic": "", "priority": "urgent", "unexpected": True}},
    )
    assert invalid.status_code == 422
    assert invalid.json()["detail"]["code"] == "run_input_schema_validation_failed"

    valid = await dynamic_io_client.post(
        f"/api/v1/employees/{employee['id']}/runs",
        headers=headers,
        json={"input": {"topic": "竞品分析", "priority": "high"}},
    )
    assert valid.status_code == 201
    assert valid.json()["input"] == {"topic": "竞品分析", "priority": "high"}
    detail = await dynamic_io_client.get(f"/api/v1/runs/{valid.json()['id']}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["output_schema"] == output_schema


@pytest.mark.parametrize(
    ("input_schema", "run_input"),
    [
        ({"type": "object"}, {"topic": "兼容自由输入", "unexpected": True}),
        ({"type": "object", "additionalProperties": False}, {}),
    ],
)
@pytest.mark.asyncio
async def test_create_run_keeps_legacy_free_form_and_zero_field_schema_compatible(
    dynamic_io_client: AsyncClient,
    input_schema: dict[str, object],
    run_input: dict[str, Any],
) -> None:
    current_user = await _register_and_login(
        dynamic_io_client,
        f"dynamic-compatible-schema-{uuid4()}@example.com",
    )
    tenant_id = current_user["workspaces"][0]["id"]
    headers = {"X-Tenant-ID": tenant_id}
    create = await dynamic_io_client.post(
        "/api/v1/employees",
        headers=headers,
        json=_employee_definition(
            name=f"兼容动态 Schema 员工 {uuid4()}",
            input_schema=input_schema,
        ),
    )
    assert create.status_code == 201
    employee = create.json()
    assert (
        await dynamic_io_client.post(
            f"/api/v1/employees/{employee['id']}/publish",
            headers=headers,
        )
    ).status_code == 200

    response = await dynamic_io_client.post(
        f"/api/v1/employees/{employee['id']}/runs",
        headers=headers,
        json={"input": run_input},
    )

    assert response.status_code == 201
    assert response.json()["input"] == run_input


@pytest.mark.asyncio
async def test_create_run_rejects_existing_published_dynamic_schema_that_fails_closed(
    dynamic_io_harness: DynamicIOHarness,
) -> None:
    client = dynamic_io_harness.client
    current_user = await _register_and_login(
        client,
        f"dynamic-legacy-schema-{uuid4()}@example.com",
    )
    tenant_id = current_user["workspaces"][0]["id"]
    headers = {"X-Tenant-ID": tenant_id}
    create = await client.post(
        "/api/v1/employees",
        headers=headers,
        json=_employee_definition(
            name=f"历史动态 Schema 员工 {uuid4()}",
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {"topic": {"type": "string"}},
            },
        ),
    )
    assert create.status_code == 201
    employee = create.json()
    publish = await client.post(
        f"/api/v1/employees/{employee['id']}/publish",
        headers=headers,
    )
    assert publish.status_code == 200

    legacy_definition = {
        **employee["definition"],
        "input_schema": {
            "type": "object",
            "properties": {"topic": {"type": "string"}},
        },
    }
    async with dynamic_io_harness.session_factory() as session:
        await session.execute(
            update(EmployeeVersionRecord)
            .where(
                EmployeeVersionRecord.tenant_id == UUID(tenant_id),
                EmployeeVersionRecord.employee_id == UUID(employee["id"]),
                EmployeeVersionRecord.version == 1,
            )
            .values(definition=legacy_definition)
        )
        await session.commit()

    response = await client.post(
        f"/api/v1/employees/{employee['id']}/runs",
        headers=headers,
        json={"input": {"topic": "竞品分析", "unexpected": "不应被放行"}},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "employee_configuration_unavailable"


@pytest.mark.asyncio
async def test_create_run_rejects_existing_published_file_schema_without_file_upload(
    dynamic_io_harness: DynamicIOHarness,
) -> None:
    client = dynamic_io_harness.client
    current_user = await _register_and_login(
        client,
        f"dynamic-legacy-file-schema-{uuid4()}@example.com",
    )
    tenant_id = current_user["workspaces"][0]["id"]
    headers = {"X-Tenant-ID": tenant_id}
    create = await client.post(
        "/api/v1/employees",
        headers=headers,
        json=_employee_definition(
            name=f"历史文件 Schema 员工 {uuid4()}",
            input_schema={"type": "object"},
        ),
    )
    assert create.status_code == 201
    employee = create.json()
    publish = await client.post(
        f"/api/v1/employees/{employee['id']}/publish",
        headers=headers,
    )
    assert publish.status_code == 200

    legacy_definition = {
        **employee["definition"],
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "source_file": {
                    "type": "string",
                    "x-agent-platform-control": "file",
                }
            },
        },
        "capabilities": {
            **employee["definition"]["capabilities"],
            "file_upload": False,
        },
    }
    async with dynamic_io_harness.session_factory() as session:
        await session.execute(
            update(EmployeeVersionRecord)
            .where(
                EmployeeVersionRecord.tenant_id == UUID(tenant_id),
                EmployeeVersionRecord.employee_id == UUID(employee["id"]),
                EmployeeVersionRecord.version == 1,
            )
            .values(definition=legacy_definition)
        )
        await session.commit()

    response = await client.post(
        f"/api/v1/employees/{employee['id']}/runs",
        headers=headers,
        json={"input": {}},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "employee_configuration_unavailable"


@pytest.mark.asyncio
async def test_dynamic_file_inputs_must_be_bound_to_current_run_attachments(
    dynamic_io_client: AsyncClient,
) -> None:
    current_user = await _register_and_login(dynamic_io_client, "dynamic-file-run@example.com")
    tenant_id = current_user["workspaces"][0]["id"]
    headers = {"X-Tenant-ID": tenant_id}
    create = await dynamic_io_client.post(
        "/api/v1/employees",
        headers=headers,
        json=_employee_definition(
            name="动态文件绑定员工",
            file_upload=True,
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["source_file"],
                "properties": {
                    "source_file": {
                        "type": "string",
                        "title": "资料文件",
                        "x-agent-platform-control": "file",
                    }
                },
            },
        ),
    )
    assert create.status_code == 201
    employee = create.json()
    assert (
        await dynamic_io_client.post(
            f"/api/v1/employees/{employee['id']}/publish",
            headers=headers,
        )
    ).status_code == 200
    file_one = await dynamic_io_client.post(
        "/api/v1/files",
        headers=headers,
        files={"file": ("source.txt", b"source", "text/plain")},
    )
    assert file_one.status_code == 201
    file_two = await dynamic_io_client.post(
        "/api/v1/files",
        headers=headers,
        files={"file": ("other.txt", b"other", "text/plain")},
    )
    assert file_two.status_code == 201
    file_one_id = file_one.json()["id"]
    file_two_id = file_two.json()["id"]

    missing_attachment = await dynamic_io_client.post(
        f"/api/v1/employees/{employee['id']}/runs",
        headers=headers,
        json={"input": {"source_file": file_one_id}, "attachment_ids": []},
    )
    assert missing_attachment.status_code == 422
    assert missing_attachment.json()["detail"]["code"] == "run_input_schema_validation_failed"

    mismatched_attachment = await dynamic_io_client.post(
        f"/api/v1/employees/{employee['id']}/runs",
        headers=headers,
        json={"input": {"source_file": file_one_id}, "attachment_ids": [file_two_id]},
    )
    assert mismatched_attachment.status_code == 422
    assert mismatched_attachment.json()["detail"]["code"] == "run_input_schema_validation_failed"

    valid = await dynamic_io_client.post(
        f"/api/v1/employees/{employee['id']}/runs",
        headers=headers,
        json={"input": {"source_file": file_one_id}, "attachment_ids": [file_one_id]},
    )
    assert valid.status_code == 201
    assert valid.json()["input"] == {"source_file": file_one_id}


@pytest.mark.asyncio
async def test_run_creation_uses_fixed_published_schema_after_draft_changes(
    dynamic_io_client: AsyncClient,
) -> None:
    current_user = await _register_and_login(dynamic_io_client, "dynamic-version@example.com")
    tenant_id = current_user["workspaces"][0]["id"]
    headers = {"X-Tenant-ID": tenant_id}
    create = await dynamic_io_client.post(
        "/api/v1/employees",
        headers=headers,
        json=_employee_definition(
            name="发布版本固定员工",
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["topic"],
                "properties": {"topic": {"type": "string", "minLength": 1}},
            },
        ),
    )
    assert create.status_code == 201
    employee = create.json()
    assert (
        await dynamic_io_client.post(
            f"/api/v1/employees/{employee['id']}/publish",
            headers=headers,
        )
    ).status_code == 200

    update = await dynamic_io_client.put(
        f"/api/v1/employees/{employee['id']}",
        headers=headers,
        json={
            **employee["definition"],
            "name": "发布版本固定员工 v2 草稿",
            "input_schema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["brief"],
                "properties": {"brief": {"type": "string", "minLength": 1}},
            },
        },
    )
    assert update.status_code == 200
    assert update.json()["status"] == "draft"
    assert update.json()["published_version"] == 1

    old_schema_payload = await dynamic_io_client.post(
        f"/api/v1/employees/{employee['id']}/runs",
        headers=headers,
        json={"input": {"topic": "继续使用已发布版本"}},
    )
    assert old_schema_payload.status_code == 201
    assert old_schema_payload.json()["employee_version"] == 1

    draft_schema_payload = await dynamic_io_client.post(
        f"/api/v1/employees/{employee['id']}/runs",
        headers=headers,
        json={"input": {"brief": "草稿 schema 尚未发布"}},
    )
    assert draft_schema_payload.status_code == 422
    assert draft_schema_payload.json()["detail"]["code"] == "run_input_schema_validation_failed"


@pytest.mark.asyncio
async def test_idempotent_run_replay_uses_original_employee_version_schema(
    dynamic_io_client: AsyncClient,
) -> None:
    current_user = await _register_and_login(
        dynamic_io_client,
        f"dynamic-idempotent-version-{uuid4()}@example.com",
    )
    tenant_id = current_user["workspaces"][0]["id"]
    idempotency_key = str(uuid4())
    headers = {"X-Tenant-ID": tenant_id, "Idempotency-Key": idempotency_key}
    tenant_headers = {"X-Tenant-ID": tenant_id}
    v1_output_schema = {
        "type": "object",
        "required": ["summary"],
        "properties": {"summary": {"type": "string"}},
    }

    create = await dynamic_io_client.post(
        "/api/v1/employees",
        headers=tenant_headers,
        json=_employee_definition(
            name="幂等版本固定员工",
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["topic"],
                "properties": {"topic": {"type": "string", "minLength": 1}},
            },
            output_schema=v1_output_schema,
        ),
    )
    assert create.status_code == 201
    employee = create.json()
    assert (
        await dynamic_io_client.post(
            f"/api/v1/employees/{employee['id']}/publish",
            headers=tenant_headers,
        )
    ).status_code == 200

    first = await dynamic_io_client.post(
        f"/api/v1/employees/{employee['id']}/runs",
        headers=headers,
        json={"input": {"topic": "第一版输入"}},
    )
    assert first.status_code == 201
    first_payload = first.json()
    assert first_payload["employee_version"] == 1
    assert first_payload["output_schema"] == v1_output_schema

    v2_output_schema = {
        "type": "object",
        "required": ["cards"],
        "properties": {
            "cards": {
                "type": "array",
                "items": {"type": "object"},
            }
        },
    }
    update = await dynamic_io_client.put(
        f"/api/v1/employees/{employee['id']}",
        headers=tenant_headers,
        json={
            **employee["definition"],
            "name": "幂等版本固定员工 v2",
            "input_schema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["brief"],
                "properties": {"brief": {"type": "string", "minLength": 1}},
            },
            "output_schema": v2_output_schema,
        },
    )
    assert update.status_code == 200
    assert (
        await dynamic_io_client.post(
            f"/api/v1/employees/{employee['id']}/publish",
            headers=tenant_headers,
        )
    ).status_code == 200

    replay = await dynamic_io_client.post(
        f"/api/v1/employees/{employee['id']}/runs",
        headers=headers,
        json={"input": {"topic": "第一版输入"}},
    )

    assert replay.status_code == 201
    replay_payload = replay.json()
    assert replay_payload["id"] == first_payload["id"]
    assert replay_payload["employee_version"] == 1
    assert replay_payload["input"] == {"topic": "第一版输入"}
    assert replay_payload["output_schema"] == v1_output_schema
