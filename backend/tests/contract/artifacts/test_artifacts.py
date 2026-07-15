from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
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
from agent_platform.infrastructure.database.repositories.artifacts import (
    SqlAlchemyArtifactRepository,
    SqlAlchemyArtifactStorageOperationRepository,
    SqlAlchemyFileRepository,
    SqlAlchemyTaskAttachmentRepository,
    TaskAttachmentRecord,
)
from agent_platform.infrastructure.database.repositories.runs import RunCommandRecord, RunRecord
from agent_platform.infrastructure.database.repositories.tenants import (
    TenantMembershipRecord,
)
from agent_platform.platform.artifacts.entities import MAX_FILE_SIZE_BYTES, Artifact
from agent_platform.platform.artifacts.services import ArtifactService, TaskAttachmentService


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


async def invoke_raw_upload(
    app: FastAPI,
    *,
    body_chunks: list[bytes],
    content_length: bytes | list[bytes] | None,
) -> tuple[int, bytes]:
    headers = [(b"content-type", b"multipart/form-data; boundary=upload-boundary")]
    if content_length is not None:
        values = content_length if isinstance(content_length, list) else [content_length]
        headers.extend((b"content-length", value) for value in values)
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/v1/files",
        "raw_path": b"/api/v1/files",
        "query_string": b"",
        "root_path": "",
        "headers": headers,
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }
    messages = [
        {
            "type": "http.request",
            "body": chunk,
            "more_body": index < len(body_chunks) - 1,
        }
        for index, chunk in enumerate(body_chunks)
    ]
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        if messages:
            return messages.pop(0)
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await app(scope, receive, send)
    start = next(message for message in sent if message["type"] == "http.response.start")
    response_body = b"".join(
        message.get("body", b"") for message in sent if message["type"] == "http.response.body"
    )
    return int(start["status"]), response_body


async def invoke_upload_that_must_not_consume_body(
    app: FastAPI,
    *,
    content_length: bytes | None,
) -> tuple[int, bytes]:
    headers = [(b"content-type", b"multipart/form-data; boundary=upload-boundary")]
    if content_length is not None:
        headers.append((b"content-length", content_length))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/v1/files",
        "raw_path": b"/api/v1/files",
        "query_string": b"",
        "root_path": "",
        "headers": headers,
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        raise AssertionError("upload body must not be consumed")

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await app(scope, receive, send)
    start = next(message for message in sent if message["type"] == "http.response.start")
    response_body = b"".join(
        message.get("body", b"") for message in sent if message["type"] == "http.response.body"
    )
    return int(start["status"]), response_body


async def create_file_run(
    client: AsyncClient,
    tenant_id: str,
    *,
    content: bytes = b"brief",
    name: str = "brief.txt",
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
        files={"file": (name, content, "text/plain")},
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


class MemoryWorkspace:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}

    async def write_file(self, *, path: str, content: bytes) -> None:
        self.files[path] = content

    async def read_file(self, *, path: str) -> bytes:
        return self.files[path]


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
        await outsider.get(f"/api/v1/files/{stored_file['id']}/content", headers=outsider_headers)
    ).status_code == 404
    assert (
        await outsider.get(f"/api/v1/runs/{run['id']}/attachments", headers=outsider_headers)
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


@pytest.mark.asyncio
@pytest.mark.parametrize("content_length", [None, b"1"])
async def test_upload_rejects_chunked_or_forged_length_before_multipart_parsing(
    artifact_api: tuple[
        FastAPI,
        async_sessionmaker[AsyncSession],
        AsyncClient,
        AsyncClient,
        MemoryArtifactStorage,
    ],
    content_length: bytes | None,
) -> None:
    app, _, _, _, storage = artifact_api
    status_code, body = await invoke_raw_upload(
        app,
        body_chunks=[
            b"x" * (MAX_FILE_SIZE_BYTES // 2),
            b"y" * (MAX_FILE_SIZE_BYTES // 2 + 65 * 1024),
        ],
        content_length=content_length,
    )

    assert status_code == 413
    assert b"request_body_too_large" in body
    assert storage.objects == {}


@pytest.mark.asyncio
async def test_upload_rejects_unauthenticated_declared_oversize_without_reading_body(
    artifact_api: tuple[
        FastAPI,
        async_sessionmaker[AsyncSession],
        AsyncClient,
        AsyncClient,
        MemoryArtifactStorage,
    ],
) -> None:
    app, _, _, _, storage = artifact_api
    status_code, body = await invoke_upload_that_must_not_consume_body(
        app,
        content_length=str(MAX_FILE_SIZE_BYTES + 65 * 1024).encode(),
    )

    assert status_code == 413
    assert b"request_body_too_large" in body
    assert storage.objects == {}


@pytest.mark.asyncio
async def test_upload_without_content_length_is_rejected_without_reading_body(
    artifact_api: tuple[
        FastAPI,
        async_sessionmaker[AsyncSession],
        AsyncClient,
        AsyncClient,
        MemoryArtifactStorage,
    ],
) -> None:
    app, _, _, _, storage = artifact_api
    status_code, body = await invoke_upload_that_must_not_consume_body(
        app,
        content_length=None,
    )

    assert status_code == 413
    assert b"request_body_too_large" in body
    assert storage.objects == {}


@pytest.mark.asyncio
async def test_upload_rejects_impossible_multipart_content_length_without_reading_body(
    artifact_api: tuple[
        FastAPI,
        async_sessionmaker[AsyncSession],
        AsyncClient,
        AsyncClient,
        MemoryArtifactStorage,
    ],
) -> None:
    app, _, _, _, storage = artifact_api
    status_code, body = await invoke_upload_that_must_not_consume_body(
        app,
        content_length=b"1",
    )

    assert status_code == 413
    assert b"request_body_too_large" in body
    assert storage.objects == {}


@pytest.mark.asyncio
async def test_upload_rejects_duplicate_content_length_headers(
    artifact_api: tuple[
        FastAPI,
        async_sessionmaker[AsyncSession],
        AsyncClient,
        AsyncClient,
        MemoryArtifactStorage,
    ],
) -> None:
    app, _, _, _, storage = artifact_api
    status_code, body = await invoke_raw_upload(
        app,
        body_chunks=[b"not parsed"],
        content_length=[b"10", b"10"],
    )

    assert status_code == 400
    assert b"invalid_content_length" in body
    assert storage.objects == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("size_bytes", [9 * 1024 * 1024, MAX_FILE_SIZE_BYTES])
async def test_api_accepted_attachment_materializes_at_nine_mib_and_public_boundary(
    artifact_api: tuple[
        FastAPI,
        async_sessionmaker[AsyncSession],
        AsyncClient,
        AsyncClient,
        MemoryArtifactStorage,
    ],
    size_bytes: int,
) -> None:
    _, sessions, owner, _, storage = artifact_api
    user = await register(owner, f"artifact-boundary-{size_bytes}@example.com")
    tenant_id = user["workspaces"][0]["id"]
    content = b"a" * size_bytes
    _, run = await create_file_run(owner, tenant_id, content=content, name="boundary.txt")
    workspace = MemoryWorkspace()

    async with sessions() as session:
        await TaskAttachmentService(
            file_repository=SqlAlchemyFileRepository(session),
            attachment_repository=SqlAlchemyTaskAttachmentRepository(session),
            storage=storage,
        ).materialize(
            tenant_id=UUID(tenant_id),
            run_id=UUID(run["id"]),
            workspace=workspace,
        )

    assert list(workspace.files.values()) == [content]


@pytest.mark.asyncio
async def test_unbound_file_delete_is_idempotent_but_never_deletes_a_bound_attachment(
    artifact_api: tuple[
        FastAPI,
        async_sessionmaker[AsyncSession],
        AsyncClient,
        AsyncClient,
        MemoryArtifactStorage,
    ],
) -> None:
    _, _, owner, _, storage = artifact_api
    user = await register(owner, "artifact-compensation@example.com")
    tenant_id = user["workspaces"][0]["id"]
    headers = {"X-Tenant-ID": tenant_id}
    orphan = (
        await owner.post(
            "/api/v1/files",
            headers=headers,
            files={"file": ("orphan.txt", b"orphan", "text/plain")},
        )
    ).json()

    deleted = await owner.delete(f"/api/v1/files/{orphan['id']}", headers=headers)
    repeated = await owner.delete(f"/api/v1/files/{orphan['id']}", headers=headers)

    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": True}
    assert repeated.status_code == 200
    assert repeated.json() == {"deleted": True}
    assert storage.objects == {}

    bound_file, _ = await create_file_run(owner, tenant_id)
    bound_storage_keys = set(storage.objects)
    protected = await owner.delete(f"/api/v1/files/{bound_file['id']}", headers=headers)

    assert protected.status_code == 200
    assert protected.json() == {"deleted": False}
    assert set(storage.objects) == bound_storage_keys


@pytest.mark.asyncio
async def test_unbound_file_ttl_reaper_deletes_real_metadata_and_object_but_keeps_bound_files(
    artifact_api: tuple[
        FastAPI,
        async_sessionmaker[AsyncSession],
        AsyncClient,
        AsyncClient,
        MemoryArtifactStorage,
    ],
) -> None:
    _, sessions, owner, _, storage = artifact_api
    user = await register(owner, "artifact-ttl@example.com")
    tenant_id = user["workspaces"][0]["id"]
    headers = {"X-Tenant-ID": tenant_id}
    orphan = (
        await owner.post(
            "/api/v1/files",
            headers=headers,
            files={"file": ("ttl-orphan.txt", b"orphan", "text/plain")},
        )
    ).json()
    bound_file, _ = await create_file_run(owner, tenant_id)

    async with sessions() as session:
        service = ArtifactService(
            file_repository=SqlAlchemyFileRepository(session),
            operation_repository=SqlAlchemyArtifactStorageOperationRepository(
                session,
                heartbeat_session_factory=sessions,
            ),
            storage=storage,
        )
        cleaned = await service.cleanup_unbound_files(
            older_than=datetime.now(UTC) + timedelta(days=1),
            commit=session.commit,
        )

    assert cleaned == 1
    async with sessions() as session:
        files = SqlAlchemyFileRepository(session)
        assert await files.get(tenant_id=UUID(tenant_id), file_id=UUID(orphan["id"])) is None
        assert (
            await files.get(tenant_id=UUID(tenant_id), file_id=UUID(bound_file["id"])) is not None
        )
    assert len(storage.objects) == 1


@pytest.mark.asyncio
async def test_create_run_idempotency_key_replays_one_run_and_rejects_payload_drift(
    artifact_api: tuple[
        FastAPI,
        async_sessionmaker[AsyncSession],
        AsyncClient,
        AsyncClient,
        MemoryArtifactStorage,
    ],
) -> None:
    _, sessions, owner, _, _ = artifact_api
    user = await register(owner, "run-idempotency@example.com")
    tenant_id = user["workspaces"][0]["id"]
    stored_file, seed_run = await create_file_run(owner, tenant_id)
    key = str(uuid4())
    headers = {"X-Tenant-ID": tenant_id, "Idempotency-Key": key}
    url = f"/api/v1/employees/{seed_run['employee_id']}/runs"
    payload = {"input": {"task": "idempotent"}, "attachment_ids": [stored_file["id"]]}

    first = await owner.post(url, headers=headers, json=payload)
    replay = await owner.post(url, headers=headers, json=payload)
    conflict = await owner.post(
        url,
        headers=headers,
        json={"input": {"task": "changed"}, "attachment_ids": [stored_file["id"]]},
    )

    assert first.status_code == 201
    assert replay.status_code == 201
    assert replay.json()["id"] == first.json()["id"]
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "idempotency_key_reused"
    async with sessions() as session:
        run_count = await session.scalar(
            select(func.count())
            .select_from(RunRecord)
            .where(RunRecord.id == UUID(first.json()["id"]))
        )
        command_count = await session.scalar(
            select(func.count())
            .select_from(RunCommandRecord)
            .where(RunCommandRecord.run_id == UUID(first.json()["id"]))
        )
        attachment_count = await session.scalar(
            select(func.count())
            .select_from(TaskAttachmentRecord)
            .where(TaskAttachmentRecord.run_id == UUID(first.json()["id"]))
        )
    assert (run_count, command_count, attachment_count) == (1, 1, 1)


@pytest.mark.asyncio
async def test_run_idempotency_and_tenant_headers_are_allowed_by_cors(
    artifact_api: tuple[
        FastAPI,
        async_sessionmaker[AsyncSession],
        AsyncClient,
        AsyncClient,
        MemoryArtifactStorage,
    ],
) -> None:
    app, _, _, _, _ = artifact_api
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.options(
            "/api/v1/employees/00000000-0000-4000-8000-000000000404/runs",
            headers={
                "Origin": "tauri://localhost",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Idempotency-Key,X-Tenant-ID",
            },
        )

    assert response.status_code == 200
    allowed_headers = response.headers["access-control-allow-headers"].lower()
    assert "idempotency-key" in allowed_headers
    assert "x-tenant-id" in allowed_headers


@pytest.mark.asyncio
async def test_artifact_permissions_cover_owner_member_admin_and_cross_tenant(
    artifact_api: tuple[
        FastAPI,
        async_sessionmaker[AsyncSession],
        AsyncClient,
        AsyncClient,
        MemoryArtifactStorage,
    ],
) -> None:
    app, sessions, owner, outsider, storage = artifact_api
    owner_user = await register(owner, "artifact-matrix-owner@example.com")
    tenant_id = owner_user["workspaces"][0]["id"]
    stored_file, run = await create_file_run(owner, tenant_id)
    headers = {"X-Tenant-ID": tenant_id}
    transport = ASGITransport(app=app)

    async with (
        AsyncClient(transport=transport, base_url="http://testserver") as member,
        AsyncClient(transport=transport, base_url="http://testserver") as admin,
    ):
        member_user = await register(member, "artifact-matrix-member@example.com")
        admin_user = await register(admin, "artifact-matrix-admin@example.com")
        outsider_user = await register(outsider, "artifact-matrix-outsider@example.com")
        async with sessions() as session:
            session.add_all(
                [
                    TenantMembershipRecord(
                        id=uuid4(),
                        tenant_id=UUID(tenant_id),
                        user_id=UUID(member_user["id"]),
                        role="member",
                        created_at=datetime.now(UTC),
                    ),
                    TenantMembershipRecord(
                        id=uuid4(),
                        tenant_id=UUID(tenant_id),
                        user_id=UUID(admin_user["id"]),
                        role="admin",
                        created_at=datetime.now(UTC),
                    ),
                ]
            )
            artifact = Artifact.create(
                tenant_id=UUID(tenant_id),
                run_id=UUID(run["id"]),
                created_by=UUID(owner_user["id"]),
                name="matrix-result.txt",
                media_type="text/plain",
                content=b"matrix result",
            )
            await SqlAlchemyArtifactRepository(session).add(artifact)
            await session.commit()
        await storage.put(
            key=artifact.storage_key,
            content=b"matrix result",
            media_type=artifact.media_type,
        )

        member_requests = [
            await member.get(f"/api/v1/runs/{run['id']}/attachments", headers=headers),
            await member.get(f"/api/v1/runs/{run['id']}/artifacts", headers=headers),
            await member.get(f"/api/v1/files/{stored_file['id']}/content", headers=headers),
            await member.get(f"/api/v1/artifacts/{artifact.id}/content", headers=headers),
            await member.delete(f"/api/v1/artifacts/{artifact.id}", headers=headers),
        ]
        assert [response.status_code for response in member_requests] == [404] * 5

        outsider_headers = {"X-Tenant-ID": outsider_user["workspaces"][0]["id"]}
        assert (
            await outsider.get(f"/api/v1/artifacts/{artifact.id}/content", headers=outsider_headers)
        ).status_code == 404

        assert (
            await admin.get(f"/api/v1/runs/{run['id']}/attachments", headers=headers)
        ).status_code == 200
        assert (
            await admin.get(f"/api/v1/runs/{run['id']}/artifacts", headers=headers)
        ).status_code == 200
        assert (
            await admin.get(f"/api/v1/files/{stored_file['id']}/content", headers=headers)
        ).content == b"brief"
        assert (
            await admin.get(f"/api/v1/artifacts/{artifact.id}/content", headers=headers)
        ).content == b"matrix result"
        assert (
            await admin.delete(f"/api/v1/artifacts/{artifact.id}", headers=headers)
        ).status_code == 204
