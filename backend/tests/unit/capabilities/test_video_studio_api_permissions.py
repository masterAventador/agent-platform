"""video.* 权限域在 video-studio API 端点上的强制执行。

能力 gate 负责「已安装 ∩ 租户已授权 ∩ 用户有权限集」；端点自身必须按
manifest 声明的 `video.read` / `video.manage` / `video.execute` 权限码做
细粒度校验，禁止退回 Core RUNS_* 权限域。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_platform.api.dependencies.capabilities import wrap_capability_router
from agent_platform.capabilities.request_context import (
    CapabilityRequestContext,
    bind_capability_request_context,
    reset_capability_request_context,
)
from agent_platform.capabilities.video_studio.registration import (
    VIDEO_STUDIO_BACKEND_REGISTRATION,
)
from agent_platform.capabilities.video_studio.storage_credentials import (
    IssuedUploadCredentials,
    StoredMaterialObject,
)
from agent_platform.infrastructure.database.base import Base
from agent_platform.infrastructure.database.models import load_database_models


class _StubCredentialIssuer:
    async def issue_upload_credentials(
        self,
        *,
        tenant_id: UUID,
        key_prefix: str,
        expires_at,
        allowed_actions,
    ) -> IssuedUploadCredentials:
        del tenant_id, allowed_actions
        return IssuedUploadCredentials(
            provider="tencent-cos",
            bucket="unit-test",
            region="ap-beijing",
            key_prefix=key_prefix,
            tmp_secret_id="unit-tmp-id",
            tmp_secret_key="unit-tmp-key",
            session_token="unit-session-token",
            expires_at=expires_at,
        )


class _StubObjectVerifier:
    async def inspect_uploaded_object(
        self,
        *,
        tenant_id: UUID,
        object_key: str,
    ) -> StoredMaterialObject:
        raise AssertionError("unit permission tests must not reach object verification")


@dataclass(slots=True)
class _Actor:
    tenant_id: UUID
    user_id: UUID
    permissions: frozenset[str]


@pytest_asyncio.fixture
async def permission_client() -> AsyncIterator[tuple[AsyncClient, _Actor]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    load_database_models()
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    actor = _Actor(tenant_id=uuid4(), user_id=uuid4(), permissions=frozenset())

    async def bind_context():
        context = CapabilityRequestContext(
            capability_id="video-studio",
            tenant_id=actor.tenant_id,
            user_id=actor.user_id,
            permissions=actor.permissions,
            session_factory=session_factory,
        )
        token = bind_capability_request_context(context)
        try:
            yield
        finally:
            reset_capability_request_context(token)

    app = FastAPI()
    (video_router,) = VIDEO_STUDIO_BACKEND_REGISTRATION.routers
    app.include_router(
        wrap_capability_router(video_router),
        dependencies=[Depends(bind_context)],
    )
    app.state.session_factory = session_factory
    app.state.video_material_upload_credential_issuer = _StubCredentialIssuer()
    app.state.video_material_object_verifier = _StubObjectVerifier()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client, actor
    await engine.dispose()


@pytest.mark.asyncio
async def test_read_permission_allows_listing_but_not_managing(
    permission_client: tuple[AsyncClient, _Actor],
) -> None:
    client, actor = permission_client
    actor.permissions = frozenset({"video.read"})

    listed = await client.get("/api/v1/video-studio/materials")
    assert listed.status_code == 200

    denied = await client.post(
        "/api/v1/video-studio/material-folders",
        json={"name": "无权限文件夹"},
    )
    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "permission_denied"

    denied_download = await client.post(
        "/api/v1/video-studio/download-tasks",
        json={"source_type": "material", "source_id": str(uuid4())},
    )
    assert denied_download.status_code == 403
    assert denied_download.json()["detail"]["code"] == "permission_denied"


@pytest.mark.asyncio
async def test_manage_permission_does_not_imply_read_listing(
    permission_client: tuple[AsyncClient, _Actor],
) -> None:
    client, actor = permission_client
    actor.permissions = frozenset({"video.manage"})

    created = await client.post(
        "/api/v1/video-studio/material-folders",
        json={"name": "管理权限文件夹"},
    )
    assert created.status_code == 201

    denied = await client.get("/api/v1/video-studio/materials")
    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "permission_denied"


@pytest.mark.asyncio
async def test_execute_permission_scopes_download_task_actions(
    permission_client: tuple[AsyncClient, _Actor],
) -> None:
    client, actor = permission_client
    actor.permissions = frozenset({"video.execute"})

    missing_source = await client.post(
        "/api/v1/video-studio/download-tasks",
        json={"source_type": "material", "source_id": str(uuid4())},
    )
    # 有 video.execute 时授权通过，落到业务校验（素材不存在 → 404）。
    assert missing_source.status_code == 404

    denied = await client.post(
        "/api/v1/video-studio/materials/upload-credentials",
        json={
            "name": "denied.mp4",
            "kind": "video",
            "media_type": "video/mp4",
            "size_bytes": 1000,
            "sha256": "a" * 64,
            "crc64ecma": "700",
        },
    )
    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "permission_denied"
