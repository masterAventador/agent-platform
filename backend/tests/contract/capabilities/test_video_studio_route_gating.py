"""video-studio 路由的三层授权矩阵与 Core+视频 组合 Profile 契约。

与 social-operations 的 gating 契约并列：未认证 401、未安装 404、
未授权 403 fail-closed、无权限 403、撤销后 403，以及授权后的全链路可用、
`video.*` 权限码经登录接口下发、关键操作桥接 C14 审计。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from capability_harness import CapabilityHarness

_VIDEO_PERMISSIONS = {"video.read", "video.manage", "video.execute"}


class _StubCredentialIssuer:
    async def issue_upload_credentials(
        self,
        *,
        tenant_id: UUID,
        key_prefix: str,
        expires_at: datetime,
        allowed_actions: tuple[str, ...],
    ) -> Any:
        from agent_platform.capabilities.video_studio.storage_credentials import (
            IssuedUploadCredentials,
        )

        del tenant_id, allowed_actions
        return IssuedUploadCredentials(
            provider="tencent-cos",
            bucket="contract-test",
            region="ap-beijing",
            key_prefix=key_prefix,
            tmp_secret_id="contract-tmp-id",
            tmp_secret_key="contract-tmp-key",
            session_token="contract-session-token",
            expires_at=expires_at,
        )


class _StubObjectVerifier:
    def __init__(self) -> None:
        self.objects: dict[tuple[UUID, str], Any] = {}

    async def inspect_uploaded_object(self, *, tenant_id: UUID, object_key: str) -> Any:
        return self.objects[(tenant_id, object_key)]


def _install_stub_providers(harness: CapabilityHarness) -> _StubObjectVerifier:
    verifier = _StubObjectVerifier()
    harness.app.state.video_material_upload_credential_issuer = _StubCredentialIssuer()
    harness.app.state.video_material_object_verifier = verifier
    return verifier


async def _grant_video(harness: CapabilityHarness, headers: dict[str, str]) -> None:
    grant = await harness.client.put(
        "/api/v1/capabilities/entitlements/video-studio",
        headers=headers,
        json={},
    )
    assert grant.status_code == 200


@pytest.mark.asyncio
async def test_video_routes_require_authentication(video_harness: CapabilityHarness) -> None:
    response = await video_harness.client.get("/api/v1/video-studio/materials")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_unentitled_tenant_is_rejected_fail_closed(
    video_harness: CapabilityHarness,
) -> None:
    current = await video_harness.register_and_login(f"owner-{uuid4()}@example.com")
    headers = {"X-Tenant-ID": current["workspaces"][0]["id"]}

    response = await video_harness.client.get(
        "/api/v1/video-studio/materials",
        headers=headers,
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "capability_not_entitled"


@pytest.mark.asyncio
async def test_entitled_owner_gets_video_permissions_and_full_chain(
    video_harness: CapabilityHarness,
) -> None:
    verifier = _install_stub_providers(video_harness)
    current = await video_harness.register_and_login(f"owner-{uuid4()}@example.com")
    tenant_id = current["workspaces"][0]["id"]
    headers = {"X-Tenant-ID": tenant_id}
    await _grant_video(video_harness, headers)

    # 登录态权限：Entitlement + Owner 角色附加 video.* 权限码。
    me = await video_harness.client.get("/api/v1/auth/me")
    assert set(me.json()["workspaces"][0]["permissions"]) >= _VIDEO_PERMISSIONS

    # registry 三层裁剪返回 video 完整条目。
    registry = await video_harness.client.get(
        "/api/v1/capabilities/registry",
        headers=headers,
    )
    entries = {entry["capability_id"]: entry for entry in registry.json()["capabilities"]}
    video_entry = entries["video-studio"]
    assert video_entry["deployment_installed"] is True
    assert video_entry["tenant_entitled"] is True
    assert set(video_entry["permissions"]) == _VIDEO_PERMISSIONS
    assert set(video_entry["frontend_entries"]) == {"video.routes.v1"}

    # 全链路：上传凭证 → 服务端核验 → 列表可见。
    credential_response = await video_harness.client.post(
        "/api/v1/video-studio/materials/upload-credentials",
        headers=headers,
        json={
            "name": "combo.mp4",
            "kind": "video",
            "media_type": "video/mp4",
            "size_bytes": 1000,
            "sha256": "c" * 64,
        },
    )
    assert credential_response.status_code == 201
    material = credential_response.json()["material"]

    from agent_platform.capabilities.video_studio.storage_credentials import (
        StoredMaterialObject,
    )

    verifier.objects[(UUID(tenant_id), material["storage_key"])] = StoredMaterialObject(
        size_bytes=1000,
        sha256="c" * 64,
    )
    completed = await video_harness.client.post(
        f"/api/v1/video-studio/materials/{material['id']}/complete-upload",
        headers=headers,
    )
    assert completed.status_code == 200
    assert completed.json()["status"] == "available"

    listed = await video_harness.client.get(
        "/api/v1/video-studio/materials",
        headers=headers,
    )
    assert [item["id"] for item in listed.json()["items"]] == [material["id"]]


@pytest.mark.asyncio
async def test_member_without_video_permissions_is_rejected(
    video_harness: CapabilityHarness,
) -> None:
    owner = await video_harness.register_and_login(f"owner-{uuid4()}@example.com")
    tenant_id = owner["workspaces"][0]["id"]
    headers = {"X-Tenant-ID": tenant_id}
    await _grant_video(video_harness, headers)
    await video_harness.client.post("/api/v1/auth/logout")

    member = await video_harness.register_and_login(f"member-{uuid4()}@example.com")
    await video_harness.add_member(
        tenant_id=UUID(tenant_id),
        user_id=UUID(member["id"]),
        role="member",
    )

    response = await video_harness.client.get(
        "/api/v1/video-studio/materials",
        headers=headers,
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "permission_denied"


@pytest.mark.asyncio
async def test_revocation_rejects_new_video_calls(video_harness: CapabilityHarness) -> None:
    _install_stub_providers(video_harness)
    current = await video_harness.register_and_login(f"owner-{uuid4()}@example.com")
    tenant_id = current["workspaces"][0]["id"]
    headers = {"X-Tenant-ID": tenant_id}
    await _grant_video(video_harness, headers)

    first = await video_harness.client.get(
        "/api/v1/video-studio/materials",
        headers=headers,
    )
    assert first.status_code == 200

    revoke = await video_harness.client.delete(
        "/api/v1/capabilities/entitlements/video-studio",
        headers=headers,
    )
    assert revoke.status_code == 200

    second = await video_harness.client.get(
        "/api/v1/video-studio/materials",
        headers=headers,
    )
    assert second.status_code == 403
    assert second.json()["detail"]["code"] == "capability_not_entitled"


@pytest.mark.asyncio
async def test_core_and_social_profile_has_no_video_routes(
    capability_harness: CapabilityHarness,
) -> None:
    """默认 Core+social Profile 未安装 video-studio：路由 404、registry 无条目、授予 409。"""

    current = await capability_harness.register_and_login(f"owner-{uuid4()}@example.com")
    tenant_id = current["workspaces"][0]["id"]
    headers = {"X-Tenant-ID": tenant_id}

    response = await capability_harness.client.get(
        "/api/v1/video-studio/materials",
        headers=headers,
    )
    assert response.status_code == 404

    registry = await capability_harness.client.get(
        "/api/v1/capabilities/registry",
        headers=headers,
    )
    assert "video-studio" not in {
        entry["capability_id"] for entry in registry.json()["capabilities"]
    }

    grant = await capability_harness.client.put(
        "/api/v1/capabilities/entitlements/video-studio",
        headers=headers,
        json={},
    )
    assert grant.status_code == 409
    assert grant.json()["detail"]["code"] == "capability_not_installed"


@pytest.mark.asyncio
async def test_core_only_profile_has_no_video_routes(
    core_only_harness: CapabilityHarness,
) -> None:
    current = await core_only_harness.register_and_login(f"owner-{uuid4()}@example.com")
    headers = {"X-Tenant-ID": current["workspaces"][0]["id"]}

    response = await core_only_harness.client.get(
        "/api/v1/video-studio/materials",
        headers=headers,
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_video_material_operations_bridge_audit_to_core_protocol(
    video_harness: CapabilityHarness,
) -> None:
    """素材上传/删除关键操作必须经 C14 统一审计协议落库（脱敏详情）。"""

    verifier = _install_stub_providers(video_harness)
    current = await video_harness.register_and_login(f"owner-{uuid4()}@example.com")
    tenant_id = current["workspaces"][0]["id"]
    headers = {"X-Tenant-ID": tenant_id}
    await _grant_video(video_harness, headers)

    credential_response = await video_harness.client.post(
        "/api/v1/video-studio/materials/upload-credentials",
        headers=headers,
        json={
            "name": "audited.mp4",
            "kind": "video",
            "media_type": "video/mp4",
            "size_bytes": 1000,
            "sha256": "d" * 64,
        },
    )
    assert credential_response.status_code == 201
    material = credential_response.json()["material"]

    from agent_platform.capabilities.video_studio.storage_credentials import (
        StoredMaterialObject,
    )

    verifier.objects[(UUID(tenant_id), material["storage_key"])] = StoredMaterialObject(
        size_bytes=1000,
        sha256="d" * 64,
    )
    assert (
        await video_harness.client.post(
            f"/api/v1/video-studio/materials/{material['id']}/complete-upload",
            headers=headers,
        )
    ).status_code == 200
    assert (
        await video_harness.client.delete(
            f"/api/v1/video-studio/materials/{material['id']}",
            headers=headers,
        )
    ).status_code == 204

    events = await video_harness.client.get(
        "/api/v1/audit/events",
        headers=headers,
    )
    assert events.status_code == 200
    by_action = {}
    for event in events.json():
        by_action.setdefault(event["action"], []).append(event)

    for action in (
        "video.material.upload_requested",
        "video.material.upload_completed",
        "video.material.deleted",
    ):
        matching = by_action.get(action)
        assert matching, f"缺少审计事件 {action}"
        assert matching[0]["tenant_id"] == tenant_id
        assert matching[0]["resource_type"] == "video-studio"
        assert matching[0]["resource_id"] == material["id"]
        # 审计脱敏：不得携带临时凭据或对象摘要。
        metadata = matching[0]["metadata"]
        assert "tmp_secret_key" not in metadata
        assert "session_token" not in metadata
        assert "sha256" not in metadata


@pytest.mark.asyncio
async def test_production_assembly_installs_real_sts_issuer_from_settings() -> None:
    """配置视频素材桶与 CAM 凭据时，生产 App 装配真实 STS 签发器到 app.state。"""

    from capability_harness import build_capability_harness
    from pydantic import SecretStr

    from agent_platform.capabilities.video_studio.tencent_sts import (
        TencentStsMaterialUploadCredentialIssuer,
    )
    from agent_platform.config import AppSettings

    harness, engine, client = await build_capability_harness(
        AppSettings(
            auth_cookie_secure=False,
            installed_capabilities=("video-studio",),
            video_material_cos_bucket="agent-platform-1424480216",
            cos_region="ap-beijing",
            cos_secret_id=SecretStr("assembly-secret-id"),
            cos_secret_key=SecretStr("assembly-secret-key"),
        )
    )
    try:
        issuer = harness.app.state.video_material_upload_credential_issuer
        assert isinstance(issuer, TencentStsMaterialUploadCredentialIssuer)
    finally:
        await client.aclose()
        await engine.dispose()


def test_lifespan_runs_video_media_maintenance_worker() -> None:
    """M-2：生产 App lifespan 启动素材回收清扫常驻任务，停机时取消退出。"""

    from capability_harness import (
        AllowAllRateLimiter,
        FakeKnowledgeProvider,
        InMemorySkillStorage,
    )
    from fastapi.testclient import TestClient
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from agent_platform.api.app import create_app
    from agent_platform.config import AppSettings
    from agent_platform.infrastructure.database.models import load_database_models

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    load_database_models()
    app = create_app(
        settings=AppSettings(
            auth_cookie_secure=False,
            installed_capabilities=("video-studio",),
            video_media_maintenance_interval_seconds=3600,
        ),
        session_factory=async_sessionmaker(engine, expire_on_commit=False),
        auth_rate_limiter=AllowAllRateLimiter(),
        knowledge_provider=FakeKnowledgeProvider(),
        skill_storage=InMemorySkillStorage(),
    )

    with TestClient(app):
        names = app.state.capability_background_worker_names
        assert names == ("video-media-library-maintenance",)
        tasks = app.state.capability_background_tasks
        assert len(tasks) == 1
        assert not tasks[0].done()

    assert app.state.capability_background_tasks[0].cancelled()
