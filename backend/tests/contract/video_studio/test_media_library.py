from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agent_platform.api.app import create_app
from agent_platform.capabilities.video_studio.media_library import (
    MAX_MATERIAL_SIZE_BYTES,
    InMemoryMaterialRepository,
    MaterialReference,
    MaterialReferenceAlreadyExistsError,
)
from agent_platform.capabilities.video_studio.persistence import (
    SqlAlchemyMediaLibraryRepository,
)
from agent_platform.capabilities.video_studio.storage_credentials import (
    IssuedMaterialPreview,
    IssuedUploadCredentials,
    StoredMaterialObject,
)
from agent_platform.config import AppSettings
from agent_platform.infrastructure.database.base import Base
from agent_platform.infrastructure.database.models import load_database_models


class AllowAllRateLimiter:
    async def ensure_allowed(self, *, scope: str, key: str) -> None:
        del scope, key


class ConfigurableObjectVerifier:
    def __init__(self) -> None:
        self.objects: dict[tuple[UUID, str], StoredMaterialObject] = {}

    async def inspect_uploaded_object(
        self,
        *,
        tenant_id: UUID,
        object_key: str,
    ) -> StoredMaterialObject:
        return self.objects[(tenant_id, object_key)]


class RecordingPreviewIssuer:
    async def issue_preview_url(self, *, tenant_id, object_key: str, expires_at):
        return IssuedMaterialPreview(
            url=f"https://preview.invalid/{tenant_id}/{object_key}",
            expires_at=expires_at,
        )


class RecordingCredentialIssuer:
    async def issue_upload_credentials(
        self,
        *,
        tenant_id,
        key_prefix: str,
        expires_at,
        allowed_actions,
    ) -> IssuedUploadCredentials:
        del tenant_id, allowed_actions
        return IssuedUploadCredentials(
            provider="tencent-cos",
            bucket="agent-platform-materials",
            region="ap-beijing",
            key_prefix=key_prefix,
            tmp_secret_id="test-tmp-id",
            tmp_secret_key="test-tmp-key",
            session_token="test-session-token",
            expires_at=expires_at,
        )


@pytest_asyncio.fixture
async def media_library_api() -> AsyncIterator[
    tuple[
        FastAPI,
        async_sessionmaker[AsyncSession],
        AsyncClient,
        AsyncClient,
        ConfigurableObjectVerifier,
    ]
]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    load_database_models()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    # 生产装配：video-studio 经安装清单 + capability gate 挂载，不再测试侧挂路由。
    app = create_app(
        settings=AppSettings(
            auth_cookie_secure=False,
            installed_capabilities=("social-operations", "video-studio"),
        ),
        session_factory=sessions,
        auth_rate_limiter=AllowAllRateLimiter(),
    )
    verifier = ConfigurableObjectVerifier()
    app.state.video_material_upload_credential_issuer = RecordingCredentialIssuer()
    app.state.video_material_object_verifier = verifier
    app.state.video_material_preview_url_issuer = RecordingPreviewIssuer()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    transport = ASGITransport(app=app)
    async with (
        AsyncClient(transport=transport, base_url="http://testserver") as owner,
        AsyncClient(transport=transport, base_url="http://testserver") as outsider,
    ):
        yield app, sessions, owner, outsider, verifier
    await engine.dispose()


async def register(client: AsyncClient, email: str) -> dict[str, Any]:
    """注册并登录一个新 Owner，同时为其默认工作区授予 video-studio Entitlement。"""

    credentials = {"email": email, "password": "correct horse battery staple"}
    assert (await client.post("/api/v1/auth/register", json=credentials)).status_code == 201
    assert (await client.post("/api/v1/auth/login", json=credentials)).status_code == 200
    identity = (await client.get("/api/v1/auth/me")).json()
    tenant_id = identity["workspaces"][0]["id"]
    grant = await client.put(
        "/api/v1/capabilities/entitlements/video-studio",
        headers={"X-Tenant-ID": tenant_id},
        json={},
    )
    assert grant.status_code == 200
    return identity


@pytest.mark.asyncio
async def test_material_upload_complete_list_and_cross_tenant_rejection(
    media_library_api: tuple[
        FastAPI,
        async_sessionmaker[AsyncSession],
        AsyncClient,
        AsyncClient,
        ConfigurableObjectVerifier,
    ],
) -> None:
    _, _, owner, outsider, verifier = media_library_api
    owner_identity = await register(owner, "video-owner@example.com")
    outsider_identity = await register(outsider, "video-outsider@example.com")
    tenant_id = owner_identity["workspaces"][0]["id"]
    outsider_tenant_id = outsider_identity["workspaces"][0]["id"]
    headers = {"X-Tenant-ID": tenant_id}

    folder_response = await owner.post(
        "/api/v1/video-studio/material-folders",
        headers=headers,
        json={"name": "广告素材"},
    )
    assert folder_response.status_code == 201
    folder = folder_response.json()
    assert folder["name"] == "广告素材"

    folders = await owner.get("/api/v1/video-studio/material-folders", headers=headers)
    assert folders.status_code == 200
    assert [item["id"] for item in folders.json()["items"]] == [folder["id"]]

    credential_response = await owner.post(
        "/api/v1/video-studio/materials/upload-credentials",
        headers=headers,
        json={
            "name": "campaign.mp4",
            "kind": "video",
            "media_type": "video/mp4",
            "size_bytes": 128 * 1024 * 1024,
            "sha256": "e" * 64,
            "folder_id": folder["id"],
            "tag_names": ["广告", "7月"],
        },
    )
    assert credential_response.status_code == 201
    payload = credential_response.json()
    material = payload["material"]
    assert material["status"] == "pending_upload"
    assert material["folder_id"] == folder["id"]
    assert payload["credentials"]["key_prefix"] == f"materials/{tenant_id}/{material['id']}/"
    assert payload["credentials"]["tmp_secret_key"] != ""
    assert "cos_secret_key" not in payload["credentials"]

    verifier.objects[(UUID(tenant_id), material["storage_key"])] = StoredMaterialObject(
        size_bytes=128 * 1024 * 1024,
        sha256="e" * 64,
    )

    completed = await owner.post(
        f"/api/v1/video-studio/materials/{material['id']}/complete-upload",
        headers=headers,
    )
    assert completed.status_code == 200
    assert completed.json()["status"] == "available"

    preview = await owner.get(
        f"/api/v1/video-studio/materials/{material['id']}/preview",
        headers=headers,
    )
    assert preview.status_code == 200
    assert preview.json()["url"].startswith("https://preview.invalid/")
    assert preview.json()["expires_at"] is not None

    listed = await owner.get("/api/v1/video-studio/materials", headers=headers)
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["items"]] == [material["id"]]
    assert listed.json()["items"][0]["tags"] == ["7月", "广告"]

    rejected = await outsider.get(
        f"/api/v1/video-studio/materials/{material['id']}",
        headers={"X-Tenant-ID": outsider_tenant_id},
    )
    assert rejected.status_code == 404

    abort_draft_response = await owner.post(
        "/api/v1/video-studio/materials/upload-credentials",
        headers=headers,
        json={
            "name": "abort.mp4",
            "kind": "video",
            "media_type": "video/mp4",
            "size_bytes": 1000,
            "sha256": "8" * 64,
        },
    )
    abort_material = abort_draft_response.json()["material"]
    aborted = await owner.post(
        f"/api/v1/video-studio/materials/{abort_material['id']}/abort-upload",
        headers=headers,
    )
    assert aborted.status_code == 200
    assert aborted.json()["status"] == "upload_failed"
    assert aborted.json()["cleanup_required"] is True


@pytest.mark.asyncio
async def test_download_task_create_progress_and_retry_api(
    media_library_api: tuple[
        FastAPI,
        async_sessionmaker[AsyncSession],
        AsyncClient,
        AsyncClient,
        ConfigurableObjectVerifier,
    ],
) -> None:
    _, _, owner, _, verifier = media_library_api
    identity = await register(owner, "download-owner@example.com")
    tenant_id = identity["workspaces"][0]["id"]
    headers = {"X-Tenant-ID": tenant_id}
    credential_response = await owner.post(
        "/api/v1/video-studio/materials/upload-credentials",
        headers=headers,
        json={
            "name": "render.mp4",
            "kind": "video",
            "media_type": "video/mp4",
            "size_bytes": 1000,
            "sha256": "f" * 64,
        },
    )
    assert credential_response.status_code == 201
    material = credential_response.json()["material"]
    verifier.objects[(UUID(tenant_id), material["storage_key"])] = StoredMaterialObject(
        size_bytes=1000,
        sha256="f" * 64,
    )
    assert (
        await owner.post(
            f"/api/v1/video-studio/materials/{material['id']}/complete-upload",
            headers=headers,
        )
    ).status_code == 200

    created = await owner.post(
        "/api/v1/video-studio/download-tasks",
        headers=headers,
        json={"source_type": "material", "source_id": material["id"]},
    )
    assert created.status_code == 201
    task = created.json()
    assert task["status"] == "queued"
    assert UUID(task["id"])

    # Worker-facing progress endpoints are still API protected and deterministic in tests.
    assert (
        await owner.post(
            f"/api/v1/video-studio/download-tasks/{task['id']}/start",
            headers=headers,
        )
    ).status_code == 200
    failed = await owner.post(
        f"/api/v1/video-studio/download-tasks/{task['id']}/fail",
        headers=headers,
        json={
            "downloaded_bytes": 400,
            "resume_token": "bytes=400-",
            "error_code": "network_timeout",
            "retryable": True,
        },
    )
    assert failed.status_code == 200
    assert failed.json()["status"] == "failed"
    assert failed.json()["progress"] == 40

    retried = await owner.post(
        f"/api/v1/video-studio/download-tasks/{task['id']}/retry",
        headers=headers,
    )
    assert retried.status_code == 200
    assert retried.json()["status"] == "queued"
    assert retried.json()["retry_count"] == 1
    assert retried.json()["resume_token"] == "bytes=400-"

    assert (
        await owner.post(
            f"/api/v1/video-studio/download-tasks/{task['id']}/start",
            headers=headers,
        )
    ).status_code == 200
    completed = await owner.post(
        f"/api/v1/video-studio/download-tasks/{task['id']}/complete",
        headers=headers,
    )
    assert completed.status_code == 200
    assert completed.json()["status"] == "succeeded"
    assert completed.json()["progress"] == 100
    assert completed.json()["completed_at"] is not None

    cancellable = await owner.post(
        "/api/v1/video-studio/download-tasks",
        headers=headers,
        json={"source_type": "material", "source_id": material["id"]},
    )
    cancelled = await owner.post(
        f"/api/v1/video-studio/download-tasks/{cancellable.json()['id']}/cancel",
        headers=headers,
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"


@pytest.mark.asyncio
async def test_storage_ports_fail_closed_instead_of_issuing_fake_credentials(
    media_library_api: tuple[
        FastAPI,
        async_sessionmaker[AsyncSession],
        AsyncClient,
        AsyncClient,
        ConfigurableObjectVerifier,
    ],
) -> None:
    app, _, owner, _, verifier = media_library_api
    identity = await register(owner, "storage-port-owner@example.com")
    tenant_id = identity["workspaces"][0]["id"]
    headers = {"X-Tenant-ID": tenant_id}
    request_body = {
        "name": "fail-closed.mp4",
        "kind": "video",
        "media_type": "video/mp4",
        "size_bytes": 1000,
        "sha256": "a" * 64,
    }

    issuer = app.state.video_material_upload_credential_issuer
    del app.state.video_material_upload_credential_issuer
    missing_sts = await owner.post(
        "/api/v1/video-studio/materials/upload-credentials",
        headers=headers,
        json=request_body,
    )
    assert missing_sts.status_code == 503
    assert missing_sts.json()["detail"]["code"] == "video_material_sts_not_configured"
    app.state.video_material_upload_credential_issuer = issuer

    draft_response = await owner.post(
        "/api/v1/video-studio/materials/upload-credentials",
        headers=headers,
        json=request_body,
    )
    assert draft_response.status_code == 201
    material = draft_response.json()["material"]
    verifier.objects[(UUID(tenant_id), material["storage_key"])] = StoredMaterialObject(
        size_bytes=1000,
        sha256="a" * 64,
    )
    del app.state.video_material_object_verifier
    missing_storage = await owner.post(
        f"/api/v1/video-studio/materials/{material['id']}/complete-upload",
        headers=headers,
    )
    assert missing_storage.status_code == 503
    assert (
        missing_storage.json()["detail"]["code"]
        == "video_material_storage_not_configured"
    )


async def create_available_material(
    client: AsyncClient,
    tenant_id: str,
    verifier: ConfigurableObjectVerifier,
    *,
    name: str,
    sha: str,
) -> dict[str, Any]:
    headers = {"X-Tenant-ID": tenant_id}
    credential_response = await client.post(
        "/api/v1/video-studio/materials/upload-credentials",
        headers=headers,
        json={
            "name": name,
            "kind": "video",
            "media_type": "video/mp4",
            "size_bytes": 1000,
            "sha256": sha * 64,
        },
    )
    assert credential_response.status_code == 201
    material = credential_response.json()["material"]
    verifier.objects[(UUID(tenant_id), material["storage_key"])] = StoredMaterialObject(
        size_bytes=1000,
        sha256=sha * 64,
    )
    completed = await client.post(
        f"/api/v1/video-studio/materials/{material['id']}/complete-upload",
        headers=headers,
    )
    assert completed.status_code == 200
    return completed.json()


@pytest.mark.asyncio
async def test_material_reference_create_list_and_delete_protection(
    media_library_api: tuple[
        FastAPI,
        async_sessionmaker[AsyncSession],
        AsyncClient,
        AsyncClient,
        ConfigurableObjectVerifier,
    ],
) -> None:
    _, _, owner, outsider, verifier = media_library_api
    owner_identity = await register(owner, "reference-owner@example.com")
    outsider_identity = await register(outsider, "reference-outsider@example.com")
    tenant_id = owner_identity["workspaces"][0]["id"]
    outsider_tenant_id = outsider_identity["workspaces"][0]["id"]
    headers = {"X-Tenant-ID": tenant_id}

    material = await create_available_material(
        owner, tenant_id, verifier, name="referenced.mp4", sha="a"
    )
    reference_target = str(uuid4())

    created = await owner.post(
        f"/api/v1/video-studio/materials/{material['id']}/references",
        headers=headers,
        json={"reference_type": "timeline_clip", "reference_id": reference_target},
    )
    assert created.status_code == 201
    reference = created.json()
    assert reference["reference_type"] == "timeline_clip"
    assert reference["reference_id"] == reference_target
    assert reference["material_id"] == material["id"]
    assert reference["created_at"] is not None

    listed = await owner.get(
        f"/api/v1/video-studio/materials/{material['id']}/references",
        headers=headers,
    )
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["items"]] == [reference["id"]]

    invalid = await owner.post(
        f"/api/v1/video-studio/materials/{material['id']}/references",
        headers=headers,
        json={"reference_type": "Bad-Type!", "reference_id": str(uuid4())},
    )
    assert invalid.status_code == 422
    assert invalid.json()["detail"]["code"] == "invalid_video_material_input"
    assert invalid.json()["detail"]["message"] == "素材引用类型无效"

    cross_tenant = await outsider.post(
        f"/api/v1/video-studio/materials/{material['id']}/references",
        headers={"X-Tenant-ID": outsider_tenant_id},
        json={"reference_type": "timeline_clip", "reference_id": str(uuid4())},
    )
    assert cross_tenant.status_code == 404
    cross_tenant_list = await outsider.get(
        f"/api/v1/video-studio/materials/{material['id']}/references",
        headers={"X-Tenant-ID": outsider_tenant_id},
    )
    assert cross_tenant_list.status_code == 404

    blocked_delete = await owner.delete(
        f"/api/v1/video-studio/materials/{material['id']}",
        headers=headers,
    )
    assert blocked_delete.status_code == 409
    assert blocked_delete.json()["detail"]["code"] == "material_in_use"

    missing_material = await owner.post(
        f"/api/v1/video-studio/materials/{uuid4()}/references",
        headers=headers,
        json={"reference_type": "timeline_clip", "reference_id": str(uuid4())},
    )
    assert missing_material.status_code == 404


@pytest.mark.asyncio
async def test_duplicate_material_reference_returns_controlled_conflict(
    media_library_api: tuple[
        FastAPI,
        async_sessionmaker[AsyncSession],
        AsyncClient,
        AsyncClient,
        ConfigurableObjectVerifier,
    ],
) -> None:
    _, _, owner, _, verifier = media_library_api
    identity = await register(owner, "duplicate-reference-owner@example.com")
    tenant_id = identity["workspaces"][0]["id"]
    headers = {"X-Tenant-ID": tenant_id}
    material = await create_available_material(
        owner, tenant_id, verifier, name="duplicated.mp4", sha="b"
    )
    payload = {"reference_type": "timeline_clip", "reference_id": str(uuid4())}

    first = await owner.post(
        f"/api/v1/video-studio/materials/{material['id']}/references",
        headers=headers,
        json=payload,
    )
    assert first.status_code == 201

    duplicate = await owner.post(
        f"/api/v1/video-studio/materials/{material['id']}/references",
        headers=headers,
        json=payload,
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["code"] == "reference_already_exists"

    listed = await owner.get(
        f"/api/v1/video-studio/materials/{material['id']}/references",
        headers=headers,
    )
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["items"]] == [first.json()["id"]]


@pytest.mark.asyncio
@pytest.mark.parametrize("repository_kind", ["in_memory", "sqlalchemy"])
async def test_duplicate_reference_error_semantics_match_across_repositories(
    repository_kind: str,
) -> None:
    """InMemory 与 SQL 仓储对唯一约束冲突必须抛出同一领域错误。"""

    from datetime import UTC, datetime

    tenant_id = uuid4()
    material_id = uuid4()
    reference_id = uuid4()

    def build_reference() -> MaterialReference:
        return MaterialReference(
            id=uuid4(),
            tenant_id=tenant_id,
            material_id=material_id,
            reference_type="timeline_clip",
            reference_id=reference_id,
            created_at=datetime.now(UTC),
        )

    if repository_kind == "in_memory":
        repository: Any = InMemoryMaterialRepository()
        await repository.add_reference(build_reference())
        with pytest.raises(MaterialReferenceAlreadyExistsError):
            await repository.add_reference(build_reference())
        return

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    load_database_models()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with sessions() as session:
            sql_repository = SqlAlchemyMediaLibraryRepository(session)
            await sql_repository.add_reference(build_reference())
            with pytest.raises(MaterialReferenceAlreadyExistsError):
                await sql_repository.add_reference(build_reference())
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_upload_credentials_reject_oversized_material_with_precise_error(
    media_library_api: tuple[
        FastAPI,
        async_sessionmaker[AsyncSession],
        AsyncClient,
        AsyncClient,
        ConfigurableObjectVerifier,
    ],
) -> None:
    _, _, owner, _, _ = media_library_api
    identity = await register(owner, "oversize-owner@example.com")
    tenant_id = identity["workspaces"][0]["id"]

    rejected = await owner.post(
        "/api/v1/video-studio/materials/upload-credentials",
        headers={"X-Tenant-ID": tenant_id},
        json={
            "name": "oversized.mp4",
            "kind": "video",
            "media_type": "video/mp4",
            "size_bytes": MAX_MATERIAL_SIZE_BYTES + 1,
            "sha256": "9" * 64,
        },
    )
    assert rejected.status_code == 422
    assert rejected.json()["detail"] == {
        "code": "invalid_video_material_input",
        "message": "素材大小无效",
    }

    listed = await owner.get(
        "/api/v1/video-studio/materials",
        headers={"X-Tenant-ID": tenant_id},
    )
    assert listed.status_code == 200
    assert listed.json()["items"] == []


@pytest.mark.asyncio
async def test_upload_credentials_accept_material_at_exact_size_limit(
    media_library_api: tuple[
        FastAPI,
        async_sessionmaker[AsyncSession],
        AsyncClient,
        AsyncClient,
        ConfigurableObjectVerifier,
    ],
) -> None:
    _, _, owner, _, _ = media_library_api
    identity = await register(owner, "size-limit-owner@example.com")
    tenant_id = identity["workspaces"][0]["id"]

    accepted = await owner.post(
        "/api/v1/video-studio/materials/upload-credentials",
        headers={"X-Tenant-ID": tenant_id},
        json={
            "name": "exactly-max.mp4",
            "kind": "video",
            "media_type": "video/mp4",
            "size_bytes": MAX_MATERIAL_SIZE_BYTES,
            "sha256": "7" * 64,
        },
    )
    assert accepted.status_code == 201
    material = accepted.json()["material"]
    assert material["size_bytes"] == MAX_MATERIAL_SIZE_BYTES
    assert material["status"] == "pending_upload"


async def _insert_artifact(
    sessions: async_sessionmaker[AsyncSession],
    *,
    tenant_id: str,
    created_by: str,
    size_bytes: int,
) -> str:
    """直接落一条成片（Core Artifact）测试数据：run + artifact。"""

    from datetime import UTC, datetime

    from agent_platform.infrastructure.database.repositories.artifacts import ArtifactRecord
    from agent_platform.infrastructure.database.repositories.runs import RunRecord

    now = datetime.now(UTC)
    run_id = uuid4()
    artifact_id = uuid4()
    async with sessions() as session:
        session.add(
            RunRecord(
                id=run_id,
                tenant_id=UUID(tenant_id),
                employee_id=uuid4(),
                employee_version=1,
                created_by=UUID(created_by),
                thread_id=f"contract-thread-{run_id}",
                input_data={},
                status="completed",
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            ArtifactRecord(
                id=artifact_id,
                tenant_id=UUID(tenant_id),
                run_id=run_id,
                created_by=UUID(created_by),
                name="render-output.mp4",
                media_type="video/mp4",
                size_bytes=size_bytes,
                sha256="f" * 64,
                storage_key=f"artifacts/{tenant_id}/{artifact_id}/render-output.mp4",
                created_at=now,
            )
        )
        await session.commit()
    return str(artifact_id)


@pytest.mark.asyncio
async def test_download_task_supports_artifact_source_over_api(
    media_library_api: tuple[
        FastAPI,
        async_sessionmaker[AsyncSession],
        AsyncClient,
        AsyncClient,
        ConfigurableObjectVerifier,
    ],
) -> None:
    """成片下载真实链路：创建 → 开始 → 完成；跨租户成片与未知来源受控拒绝。"""

    _, sessions, owner, outsider, _ = media_library_api
    owner_identity = await register(owner, "artifact-download-owner@example.com")
    outsider_identity = await register(outsider, "artifact-download-outsider@example.com")
    tenant_id = owner_identity["workspaces"][0]["id"]
    outsider_tenant_id = outsider_identity["workspaces"][0]["id"]
    headers = {"X-Tenant-ID": tenant_id}

    artifact_id = await _insert_artifact(
        sessions,
        tenant_id=tenant_id,
        created_by=owner_identity["id"],
        size_bytes=2048,
    )

    created = await owner.post(
        "/api/v1/video-studio/download-tasks",
        headers=headers,
        json={"source_type": "artifact", "source_id": artifact_id},
    )
    assert created.status_code == 201
    task = created.json()
    assert task["source_type"] == "artifact"
    assert task["total_bytes"] == 2048
    assert task["status"] == "queued"

    assert (
        await owner.post(
            f"/api/v1/video-studio/download-tasks/{task['id']}/start",
            headers=headers,
        )
    ).status_code == 200
    completed = await owner.post(
        f"/api/v1/video-studio/download-tasks/{task['id']}/complete",
        headers=headers,
    )
    assert completed.status_code == 200
    assert completed.json()["status"] == "succeeded"
    assert completed.json()["downloaded_bytes"] == 2048

    cross_tenant = await outsider.post(
        "/api/v1/video-studio/download-tasks",
        headers={"X-Tenant-ID": outsider_tenant_id},
        json={"source_type": "artifact", "source_id": artifact_id},
    )
    assert cross_tenant.status_code == 404

    unknown_source = await owner.post(
        "/api/v1/video-studio/download-tasks",
        headers=headers,
        json={"source_type": "bogus", "source_id": artifact_id},
    )
    assert unknown_source.status_code == 422
    assert unknown_source.json()["detail"]["code"] == "invalid_video_material_input"


@pytest.mark.asyncio
async def test_sts_issue_failure_maps_to_controlled_503(
    media_library_api: tuple[
        FastAPI,
        async_sessionmaker[AsyncSession],
        AsyncClient,
        AsyncClient,
        ConfigurableObjectVerifier,
    ],
) -> None:
    """STS 上游签发失败必须受控 503，而不是把上游异常放大成 500。"""

    from agent_platform.capabilities.video_studio.storage_credentials import (
        MaterialStorageCredentialsUnavailable,
    )

    class BrokenIssuer:
        async def issue_upload_credentials(self, **kwargs):
            raise MaterialStorageCredentialsUnavailable("腾讯云 STS 临时凭证签发失败")

    app, _, owner, _, _ = media_library_api
    identity = await register(owner, "sts-failure-owner@example.com")
    tenant_id = identity["workspaces"][0]["id"]
    app.state.video_material_upload_credential_issuer = BrokenIssuer()

    response = await owner.post(
        "/api/v1/video-studio/materials/upload-credentials",
        headers={"X-Tenant-ID": tenant_id},
        json={
            "name": "sts-broken.mp4",
            "kind": "video",
            "media_type": "video/mp4",
            "size_bytes": 1000,
            "sha256": "e" * 64,
        },
    )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "video_material_sts_unavailable"

    # 失败后素材草稿不得残留为可见素材。
    listed = await owner.get(
        "/api/v1/video-studio/materials",
        headers={"X-Tenant-ID": tenant_id},
    )
    assert listed.json()["items"] == []


@pytest.mark.asyncio
async def test_upload_credentials_retry_after_audit_flush_failure_reuses_draft(
    media_library_api: tuple[
        FastAPI,
        async_sessionmaker[AsyncSession],
        AsyncClient,
        AsyncClient,
        ConfigurableObjectVerifier,
    ],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """M-1：审计 flush 失败 500 后客户端重试，不得重复产生草稿（重签凭证但复用草稿）。"""

    import agent_platform.api.dependencies.capabilities as capability_dependencies

    _, _, owner, _, _ = media_library_api
    identity = await register(owner, "audit-retry-owner@example.com")
    tenant_id = identity["workspaces"][0]["id"]
    headers = {"X-Tenant-ID": tenant_id}
    payload = {
        "name": "audit-retry.mp4",
        "kind": "video",
        "media_type": "video/mp4",
        "size_bytes": 1000,
        "sha256": "d" * 64,
    }

    async def broken_emit(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("audit store unavailable")

    with pytest.MonkeyPatch.context() as first_attempt:
        first_attempt.setattr(capability_dependencies, "emit_audit_event", broken_emit)
        failed = await owner.post(
            "/api/v1/video-studio/materials/upload-credentials",
            headers=headers,
            json=payload,
        )
    assert failed.status_code == 500
    assert failed.json()["detail"]["code"] == "capability_audit_flush_failed"

    retried = await owner.post(
        "/api/v1/video-studio/materials/upload-credentials",
        headers=headers,
        json=payload,
    )
    assert retried.status_code == 201
    material = retried.json()["material"]
    assert material["status"] == "pending_upload"
    assert retried.json()["credentials"]["tmp_secret_key"] != ""

    listed = await owner.get("/api/v1/video-studio/materials", headers=headers)
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["items"]] == [material["id"]]


@pytest.mark.asyncio
async def test_sts_issuance_is_rate_limited_per_tenant(
    media_library_api: tuple[
        FastAPI,
        async_sessionmaker[AsyncSession],
        AsyncClient,
        AsyncClient,
        ConfigurableObjectVerifier,
    ],
) -> None:
    """L-1：STS 签发按租户频控，超限受控 429，不再无限打真实 STS。"""

    from agent_platform.platform.auth.errors import RateLimitExceeded

    class CountingRateLimiter:
        def __init__(self) -> None:
            self.scoped_calls: list[tuple[str, str]] = []

        async def ensure_allowed(self, *, scope: str, key: str) -> None:
            self.scoped_calls.append((scope, key))
            if scope != "video_sts_issue":
                return
            issued = sum(1 for called_scope, _ in self.scoped_calls if called_scope == scope)
            if issued > 2:
                raise RateLimitExceeded

    app, _, owner, _, _ = media_library_api
    identity = await register(owner, "sts-rate-limit-owner@example.com")
    tenant_id = identity["workspaces"][0]["id"]
    headers = {"X-Tenant-ID": tenant_id}
    limiter = CountingRateLimiter()
    app.state.auth_rate_limiter = limiter

    def payload(index: int) -> dict[str, Any]:
        return {
            "name": f"rate-{index}.mp4",
            "kind": "video",
            "media_type": "video/mp4",
            "size_bytes": 1000 + index,
            "sha256": str(index) * 64,
        }

    for index in range(2):
        accepted = await owner.post(
            "/api/v1/video-studio/materials/upload-credentials",
            headers=headers,
            json=payload(index),
        )
        assert accepted.status_code == 201

    limited = await owner.post(
        "/api/v1/video-studio/materials/upload-credentials",
        headers=headers,
        json=payload(3),
    )
    assert limited.status_code == 429
    assert limited.json()["detail"]["code"] == "video_sts_rate_limited"
    # 频控 key 必须是租户维度。
    assert ("video_sts_issue", tenant_id) in limiter.scoped_calls

    # 超限请求不得残留草稿。
    listed = await owner.get("/api/v1/video-studio/materials", headers=headers)
    assert len(listed.json()["items"]) == 2


@pytest.mark.asyncio
async def test_complete_upload_maps_storage_outage_to_controlled_503(
    media_library_api: tuple[
        FastAPI,
        async_sessionmaker[AsyncSession],
        AsyncClient,
        AsyncClient,
        ConfigurableObjectVerifier,
    ],
) -> None:
    """M-3：对象存储上游故障 → 受控 503，草稿保持 pending_upload 可重试。"""

    from agent_platform.capabilities.video_studio.storage_credentials import (
        MaterialStorageUnavailable,
    )

    class OutageVerifier:
        async def inspect_uploaded_object(self, *, tenant_id, object_key: str):
            raise MaterialStorageUnavailable("素材对象存储暂时不可用")

    app, _, owner, _, _ = media_library_api
    identity = await register(owner, "storage-outage-owner@example.com")
    tenant_id = identity["workspaces"][0]["id"]
    headers = {"X-Tenant-ID": tenant_id}

    draft = await owner.post(
        "/api/v1/video-studio/materials/upload-credentials",
        headers=headers,
        json={
            "name": "outage.mp4",
            "kind": "video",
            "media_type": "video/mp4",
            "size_bytes": 1000,
            "sha256": "9" * 64,
        },
    )
    material = draft.json()["material"]
    app.state.video_material_object_verifier = OutageVerifier()

    response = await owner.post(
        f"/api/v1/video-studio/materials/{material['id']}/complete-upload",
        headers=headers,
    )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "video_material_storage_unavailable"

    listed = await owner.get("/api/v1/video-studio/materials", headers=headers)
    (item,) = listed.json()["items"]
    assert item["status"] == "pending_upload"
    assert item["cleanup_required"] is False
