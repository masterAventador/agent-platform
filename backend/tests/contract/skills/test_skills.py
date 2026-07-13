from collections.abc import AsyncIterator
from io import BytesIO
from zipfile import ZipFile

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_platform.api.app import create_app
from agent_platform.config import AppSettings
from agent_platform.infrastructure.database.base import Base
from agent_platform.infrastructure.database.models import load_database_models


class AllowAllRateLimiter:
    async def ensure_allowed(self, *, scope: str, key: str) -> None:
        del scope, key


class InMemorySkillStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def put(self, *, key: str, content: bytes) -> None:
        self.objects[key] = content

    async def get(self, *, key: str) -> bytes:
        return self.objects[key]

    async def delete(self, *, key: str) -> None:
        self.objects.pop(key, None)


def _skill_zip(*, description: str, reference: str) -> bytes:
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        archive.writestr(
            "report-writer/SKILL.md",
            "---\nname: report-writer\n"
            f"description: {description}\n---\n\n# Report writer\n",
        )
        archive.writestr("report-writer/references/guide.md", reference)
    return output.getvalue()


@pytest_asyncio.fixture
async def skill_client() -> AsyncIterator[tuple[AsyncClient, InMemorySkillStorage]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    load_database_models()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    storage = InMemorySkillStorage()
    app = create_app(
        settings=AppSettings(auth_cookie_secure=False),
        session_factory=async_sessionmaker(engine, expire_on_commit=False),
        auth_rate_limiter=AllowAllRateLimiter(),
        skill_storage=storage,
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client, storage
    await engine.dispose()


async def _register_workspace(client: AsyncClient, email: str) -> dict[str, str]:
    credentials = {"email": email, "password": "correct horse battery staple"}
    assert (await client.post("/api/v1/auth/register", json=credentials)).status_code == 201
    assert (await client.post("/api/v1/auth/login", json=credentials)).status_code == 200
    tenant_id = (await client.get("/api/v1/auth/me")).json()["workspaces"][0]["id"]
    return {"X-Tenant-ID": tenant_id}


@pytest.mark.asyncio
async def test_skill_version_publish_file_and_rollback_flow(skill_client) -> None:
    client, storage = skill_client
    headers = await _register_workspace(client, "skill-owner@example.com")

    created = await client.post(
        "/api/v1/skills",
        headers=headers,
        files={
            "bundle": (
                "report-writer.zip",
                _skill_zip(description="Create reports.", reference="Version one"),
                "application/zip",
            )
        },
    )
    assert created.status_code == 201
    skill = created.json()
    assert skill["name"] == "report-writer"
    assert skill["latest_version"] == 1
    assert skill["published_version"] is None
    assert "storage_key" not in skill
    assert len(storage.objects) == 1

    listed = await client.get("/api/v1/skills", headers=headers)
    assert listed.json() == [skill]
    file_response = await client.get(
        f"/api/v1/skills/{skill['id']}/versions/1/files/references/guide.md",
        headers=headers,
    )
    assert file_response.text == "Version one"

    published_v1 = await client.post(
        f"/api/v1/skills/{skill['id']}/versions/1/publish", headers=headers
    )
    assert published_v1.json()["published_version"] == 1

    version_two = await client.post(
        f"/api/v1/skills/{skill['id']}/versions",
        headers=headers,
        files={
            "bundle": (
                "report-writer-v2.zip",
                _skill_zip(description="Create better reports.", reference="Version two"),
                "application/zip",
            )
        },
    )
    assert version_two.status_code == 201
    assert version_two.json()["version"] == 2

    published_v2 = await client.post(
        f"/api/v1/skills/{skill['id']}/versions/2/publish", headers=headers
    )
    assert published_v2.json()["published_version"] == 2
    rolled_back = await client.post(
        f"/api/v1/skills/{skill['id']}/versions/1/publish", headers=headers
    )
    assert rolled_back.json()["published_version"] == 1


@pytest.mark.asyncio
async def test_skill_is_hidden_from_another_tenant(skill_client) -> None:
    client, _ = skill_client
    owner_headers = await _register_workspace(client, "first-skill-owner@example.com")
    created = await client.post(
        "/api/v1/skills",
        headers=owner_headers,
        files={
            "bundle": (
                "report-writer.zip",
                _skill_zip(description="Create reports.", reference="Private"),
                "application/zip",
            )
        },
    )
    skill_id = created.json()["id"]
    await client.post("/api/v1/auth/logout")
    other_headers = await _register_workspace(client, "second-skill-owner@example.com")

    response = await client.get(f"/api/v1/skills/{skill_id}", headers=other_headers)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_employee_can_only_bind_published_tenant_skills(skill_client) -> None:
    client, _ = skill_client
    headers = await _register_workspace(client, "skill-binding-owner@example.com")
    created = await client.post(
        "/api/v1/skills",
        headers=headers,
        files={
            "bundle": (
                "report-writer.zip",
                _skill_zip(description="Create reports.", reference="Binding"),
                "application/zip",
            )
        },
    )
    skill_id = created.json()["id"]
    definition = {
        "name": "报告专员",
        "role_description": "根据资料编写报告",
        "work_mode": "autonomous",
        "system_prompt": "使用已绑定 Skill 完成报告。",
        "model": {"provider": "openai", "name": "gpt-5"},
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object"},
        "capabilities": {
            "conversation": True,
            "scheduled_tasks": False,
            "file_upload": False,
        },
        "skill_ids": [skill_id],
    }

    unpublished = await client.post("/api/v1/employees", headers=headers, json=definition)
    assert unpublished.status_code == 422
    assert unpublished.json()["detail"]["code"] == "skill_not_bindable"

    await client.post(f"/api/v1/skills/{skill_id}/versions/1/publish", headers=headers)
    definition["name"] = "已发布报告专员"
    published = await client.post("/api/v1/employees", headers=headers, json=definition)
    assert published.status_code == 201
    assert published.json()["definition"]["skill_ids"] == [skill_id]

    other_headers = await _register_workspace(client, "other-skill-owner@example.com")
    cross_tenant = await client.post(
        "/api/v1/employees",
        headers=other_headers,
        json={**definition, "name": "其他企业报告专员"},
    )
    assert cross_tenant.status_code == 422
    assert cross_tenant.json()["detail"]["code"] == "skill_not_bindable"
