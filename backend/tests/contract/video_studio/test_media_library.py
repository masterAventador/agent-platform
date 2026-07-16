from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agent_platform.api.app import create_app
from agent_platform.api.routes.video_studio import router as video_studio_router
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
    app = create_app(
        settings=AppSettings(auth_cookie_secure=False),
        session_factory=sessions,
        auth_rate_limiter=AllowAllRateLimiter(),
        extra_routers=(video_studio_router,),
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
    credentials = {"email": email, "password": "correct horse battery staple"}
    assert (await client.post("/api/v1/auth/register", json=credentials)).status_code == 201
    assert (await client.post("/api/v1/auth/login", json=credentials)).status_code == 200
    return (await client.get("/api/v1/auth/me")).json()


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
