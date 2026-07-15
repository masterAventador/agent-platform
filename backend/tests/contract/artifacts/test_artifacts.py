from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agent_platform.api.app import create_app
from agent_platform.config import AppSettings
from agent_platform.infrastructure.database.base import Base
from agent_platform.infrastructure.database.models import load_database_models
from agent_platform.infrastructure.database.repositories.artifacts import (
    SqlAlchemyArtifactRepository,
)
from agent_platform.platform.artifacts.entities import Artifact


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


@pytest_asyncio.fixture
async def artifact_api() -> AsyncIterator[
    tuple[
        FastAPI,
        async_sessionmaker[AsyncSession],
        AsyncClient,
        AsyncClient,
        MemoryArtifactStorage,
    ]
]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    load_database_models()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    storage = MemoryArtifactStorage()
    app = create_app(
        settings=AppSettings(auth_cookie_secure=False),
        session_factory=sessions,
        auth_rate_limiter=AllowAllRateLimiter(),
        artifact_storage=storage,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    transport = ASGITransport(app=app)
    async with (
        AsyncClient(transport=transport, base_url="http://testserver") as owner,
        AsyncClient(transport=transport, base_url="http://testserver") as outsider,
    ):
        yield app, sessions, owner, outsider, storage
    await engine.dispose()


async def register(client: AsyncClient, email: str) -> dict[str, Any]:
    credentials = {"email": email, "password": "correct horse battery staple"}
    assert (await client.post("/api/v1/auth/register", json=credentials)).status_code == 201
    assert (await client.post("/api/v1/auth/login", json=credentials)).status_code == 200
    return (await client.get("/api/v1/auth/me")).json()


async def create_file_run(
    client: AsyncClient, tenant_id: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    headers = {"X-Tenant-ID": tenant_id}
    employee = (
        await client.post(
            "/api/v1/employees",
            headers=headers,
            json={
                "name": "文件员工",
                "role_description": "读取文件并创建产物",
                "work_mode": "autonomous",
                "system_prompt": "仅处理授权附件。",
                "model": {"kind": "gateway_alias", "alias": "general-purpose"},
                "input_schema": {"type": "object"},
                "output_schema": {"type": "object"},
                "capabilities": {
                    "conversation": False,
                    "scheduled_tasks": False,
                    "file_upload": True,
                },
            },
        )
    ).json()
    assert (
        await client.post(f"/api/v1/employees/{employee['id']}/publish", headers=headers)
    ).status_code == 200
    uploaded = await client.post(
        "/api/v1/files",
        headers=headers,
        files={"file": ("brief.txt", b"brief", "text/plain")},
    )
    assert uploaded.status_code == 201
    stored_file = uploaded.json()
    assert "storage_key" not in stored_file
    run_response = await client.post(
        f"/api/v1/employees/{employee['id']}/runs",
        headers=headers,
        json={"input": {"task": "summarize"}, "attachment_ids": [stored_file["id"]]},
    )
    assert run_response.status_code == 201
    return stored_file, run_response.json()


@pytest.mark.asyncio
async def test_upload_attach_and_download_are_tenant_and_owner_scoped(
    artifact_api: tuple[
        FastAPI,
        async_sessionmaker[AsyncSession],
        AsyncClient,
        AsyncClient,
        MemoryArtifactStorage,
    ],
) -> None:
    _, sessions, owner, outsider, storage = artifact_api
    owner_user = await register(owner, "artifact-owner@example.com")
    tenant_id = owner_user["workspaces"][0]["id"]
    stored_file, run = await create_file_run(owner, tenant_id)
    headers = {"X-Tenant-ID": tenant_id}

    attachments = await owner.get(f"/api/v1/runs/{run['id']}/attachments", headers=headers)
    assert attachments.status_code == 200
    assert attachments.json()[0]["file"]["id"] == stored_file["id"]
    content = await owner.get(f"/api/v1/files/{stored_file['id']}/content", headers=headers)
    assert content.content == b"brief"
    assert content.headers["content-type"].startswith("text/plain")
    assert list(storage.objects.values()) == [b"brief"]

    outsider_user = await register(outsider, "artifact-outsider@example.com")
    outsider_headers = {"X-Tenant-ID": outsider_user["workspaces"][0]["id"]}
    assert (
        await outsider.get(
            f"/api/v1/files/{stored_file['id']}/content", headers=outsider_headers
        )
    ).status_code == 404
    assert (
        await outsider.get(
            f"/api/v1/runs/{run['id']}/attachments", headers=outsider_headers
        )
    ).status_code == 404

    async with sessions() as session:
        artifact = Artifact.create(
            tenant_id=UUID(tenant_id),
            run_id=UUID(run["id"]),
            created_by=UUID(owner_user["id"]),
            name="result.txt",
            media_type="text/plain",
            content=b"result",
        )
        await storage.put(
            key=artifact.storage_key, content=b"result", media_type=artifact.media_type
        )
        await SqlAlchemyArtifactRepository(session).add(artifact)
        await session.commit()

    listed = await owner.get(f"/api/v1/runs/{run['id']}/artifacts", headers=headers)
    assert listed.status_code == 200
    assert listed.json()[0]["name"] == "result.txt"
    assert "storage_key" not in listed.json()[0]
    downloaded = await owner.get(f"/api/v1/artifacts/{artifact.id}/content", headers=headers)
    assert downloaded.content == b"result"
    assert "attachment;" in downloaded.headers["content-disposition"]
    deleted = await owner.delete(f"/api/v1/artifacts/{artifact.id}", headers=headers)
    assert deleted.status_code == 204
    assert artifact.storage_key not in storage.objects


@pytest.mark.asyncio
async def test_upload_rejects_type_and_size_before_object_storage(
    artifact_api: tuple[
        FastAPI,
        async_sessionmaker[AsyncSession],
        AsyncClient,
        AsyncClient,
        MemoryArtifactStorage,
    ],
) -> None:
    _, _, owner, _, storage = artifact_api
    user = await register(owner, "artifact-validation@example.com")
    headers = {"X-Tenant-ID": user["workspaces"][0]["id"]}

    executable = await owner.post(
        "/api/v1/files",
        headers=headers,
        files={"file": ("payload.exe", b"MZ", "application/x-msdownload")},
    )

    assert executable.status_code == 422
    assert executable.json()["detail"]["code"] == "invalid_artifact_input"
    assert storage.objects == {}
