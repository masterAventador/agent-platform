from collections.abc import AsyncIterator
from datetime import UTC, datetime
from io import BytesIO
from uuid import UUID, uuid4
from zipfile import ZipFile

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_platform.api.app import create_app
from agent_platform.config import AppSettings
from agent_platform.infrastructure.database.base import Base
from agent_platform.infrastructure.database.models import load_database_models
from agent_platform.infrastructure.database.repositories.tenants import TenantMembershipRecord


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


def _skill_zip(*, description: str, reference: str, name: str = "report-writer") -> bytes:
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        archive.writestr(
            f"{name}/SKILL.md",
            f"---\nname: {name}\n"
            f"description: {description}\n---\n\n# Report writer\n",
        )
        archive.writestr(f"{name}/references/guide.md", reference)
    return output.getvalue()


@pytest_asyncio.fixture
async def skill_client() -> AsyncIterator[
    tuple[AsyncClient, InMemorySkillStorage, async_sessionmaker]
]:
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
        yield client, storage, async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _register_workspace(client: AsyncClient, email: str) -> dict[str, str]:
    credentials = {"email": email, "password": "correct horse battery staple"}
    assert (await client.post("/api/v1/auth/register", json=credentials)).status_code == 201
    assert (await client.post("/api/v1/auth/login", json=credentials)).status_code == 200
    tenant_id = (await client.get("/api/v1/auth/me")).json()["workspaces"][0]["id"]
    return {"X-Tenant-ID": tenant_id}


@pytest.mark.asyncio
async def test_skill_version_publish_file_and_rollback_flow(skill_client) -> None:
    client, storage, _ = skill_client
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
    client, _, _ = skill_client
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
    client, _, _ = skill_client
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


@pytest.mark.asyncio
async def test_member_sees_only_published_skill_and_published_version(skill_client) -> None:
    client, _, session_factory = skill_client
    owner_headers = await _register_workspace(client, "skill-rbac-owner@example.com")
    tenant_id = UUID(owner_headers["X-Tenant-ID"])
    published = await client.post(
        "/api/v1/skills",
        headers=owner_headers,
        files={
            "bundle": (
                "report-writer.zip",
                _skill_zip(description="Published skill.", reference="Version one"),
                "application/zip",
            )
        },
    )
    assert published.status_code == 201
    skill_id = published.json()["id"]
    assert (
        await client.post(f"/api/v1/skills/{skill_id}/versions/1/publish", headers=owner_headers)
    ).status_code == 200
    assert (
        await client.post(
            f"/api/v1/skills/{skill_id}/versions",
            headers=owner_headers,
            files={
                "bundle": (
                    "report-writer-v2.zip",
                    _skill_zip(description="Draft version.", reference="Version two"),
                    "application/zip",
                )
            },
        )
    ).status_code == 201
    unpublished = await client.post(
        "/api/v1/skills",
        headers=owner_headers,
        files={
            "bundle": (
                "draft.zip",
                _skill_zip(
                    description="Unpublished skill.",
                    reference="Hidden",
                    name="draft-skill",
                ),
                "application/zip",
            )
        },
    )
    assert unpublished.status_code == 201
    owner_view = await client.get(f"/api/v1/skills/{skill_id}", headers=owner_headers)
    assert owner_view.status_code == 200
    assert owner_view.json()["description"] == "Draft version."
    assert owner_view.json()["latest_version"] == 2

    await client.post("/api/v1/auth/logout")
    await _register_workspace(client, "skill-rbac-member@example.com")
    member = (await client.get("/api/v1/auth/me")).json()
    async with session_factory() as session:
        session.add(
            TenantMembershipRecord(
                id=uuid4(),
                tenant_id=tenant_id,
                user_id=UUID(member["id"]),
                role="member",
                created_at=datetime.now(UTC),
            )
        )
        await session.commit()
    member_headers = {"X-Tenant-ID": str(tenant_id)}

    listed = await client.get("/api/v1/skills", headers=member_headers)
    assert listed.status_code == 200
    assert [skill["id"] for skill in listed.json()] == [skill_id]
    assert listed.json()[0]["description"] == "Published skill."
    assert listed.json()[0]["latest_version"] == 1
    member_view = await client.get(f"/api/v1/skills/{skill_id}", headers=member_headers)
    assert member_view.status_code == 200
    assert member_view.json()["description"] == "Published skill."
    assert member_view.json()["latest_version"] == 1
    assert (
        await client.get(
            f"/api/v1/skills/{unpublished.json()['id']}", headers=member_headers
        )
    ).status_code == 404
    versions = await client.get(
        f"/api/v1/skills/{skill_id}/versions", headers=member_headers
    )
    assert versions.status_code == 200
    assert [version["version"] for version in versions.json()] == [1]
    assert (
        await client.get(
            f"/api/v1/skills/{skill_id}/versions/1/files/references/guide.md",
            headers=member_headers,
        )
    ).status_code == 200
    assert (
        await client.get(
            f"/api/v1/skills/{skill_id}/versions/2/files/references/guide.md",
            headers=member_headers,
        )
    ).status_code == 404
    assert (
        await client.post(
            f"/api/v1/skills/{skill_id}/versions",
            headers=member_headers,
            files={
                "bundle": (
                    "forbidden.zip",
                    _skill_zip(description="Forbidden.", reference="Forbidden"),
                    "application/zip",
                )
            },
        )
    ).status_code == 403

    async with session_factory() as session:
        membership = (
            await session.execute(
                select(TenantMembershipRecord).where(
                    TenantMembershipRecord.tenant_id == tenant_id,
                    TenantMembershipRecord.user_id == UUID(member["id"]),
                )
            )
        ).scalar_one()
        membership.role = "admin"
        await session.commit()
    admin_view = await client.get(f"/api/v1/skills/{skill_id}", headers=member_headers)
    assert admin_view.status_code == 200
    assert admin_view.json()["description"] == "Draft version."
    assert admin_view.json()["latest_version"] == 2
