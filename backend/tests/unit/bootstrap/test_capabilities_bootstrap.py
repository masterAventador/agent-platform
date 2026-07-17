from __future__ import annotations

import pytest

from agent_platform.bootstrap.capabilities import (
    KNOWN_CAPABILITY_MANIFESTS,
    CapabilityInstallationError,
    resolve_installed_backend_registrations,
)
from agent_platform.config import AppSettings


def _settings(installed: tuple[str, ...]) -> AppSettings:
    return AppSettings(installed_capabilities=installed)


def test_default_profile_installs_social_operations() -> None:
    installed = resolve_installed_backend_registrations(AppSettings())

    assert installed.host.installed_capability_ids == frozenset({"social-operations"})
    assert len(installed.registrations) == 1
    registration = installed.registrations[0]
    assert registration.manifest.capability_id == "social-operations"
    router = registration.create_router(registration.settings)
    route_paths = {route.path for route in router.routes}  # type: ignore[attr-defined]
    assert any(path.startswith("/api/v1/social-operations") for path in route_paths)


def test_core_only_profile_installs_nothing() -> None:
    installed = resolve_installed_backend_registrations(_settings(()))

    assert installed.host.installed_capability_ids == frozenset()
    assert installed.registrations == ()
    assert set(installed.catalog) == set(KNOWN_CAPABILITY_MANIFESTS)


def test_unknown_capability_id_fails_closed() -> None:
    with pytest.raises(CapabilityInstallationError):
        resolve_installed_backend_registrations(_settings(("unknown-capability",)))


def test_video_studio_backend_host_is_installable_with_media_library_routes() -> None:
    installed = resolve_installed_backend_registrations(_settings(("video-studio",)))

    assert installed.host.installed_capability_ids == frozenset({"video-studio"})
    assert len(installed.registrations) == 1
    registration = installed.registrations[0]
    assert registration.manifest.capability_id == "video-studio"
    router = registration.create_router(registration.settings)
    route_paths = {route.path for route in router.routes}  # type: ignore[attr-defined]
    assert any(path.startswith("/api/v1/video-studio") for path in route_paths)


def test_core_and_both_capability_packages_install_together() -> None:
    installed = resolve_installed_backend_registrations(
        _settings(("social-operations", "video-studio"))
    )

    assert installed.host.installed_capability_ids == frozenset(
        {"social-operations", "video-studio"}
    )
    assert {registration.manifest.capability_id for registration in installed.registrations} == {
        "social-operations",
        "video-studio",
    }


def test_catalog_always_contains_known_manifests() -> None:
    installed = resolve_installed_backend_registrations(AppSettings())

    assert set(installed.catalog) == {"social-operations", "video-studio"}
    assert installed.catalog["social-operations"].capability_id == "social-operations"


def test_video_studio_state_wires_real_tencent_sts_issuer_when_configured() -> None:
    """配置了视频素材 COS 桶与 CAM 凭据时，生产装配注入真实 STS 签发器。"""

    from pydantic import SecretStr

    from agent_platform.capabilities.video_studio.tencent_sts import (
        TencentStsMaterialUploadCredentialIssuer,
    )

    settings = AppSettings(
        installed_capabilities=("video-studio",),
        video_material_cos_bucket="agent-platform-1424480216",
        cos_region="ap-beijing",
        cos_secret_id=SecretStr("test-secret-id"),
        cos_secret_key=SecretStr("test-secret-key"),
    )
    from agent_platform.capabilities.video_studio.tencent_cos import (
        TencentCosMaterialObjectCleaner,
        TencentCosMaterialObjectVerifier,
        TencentCosMaterialPreviewUrlIssuer,
    )

    installed = resolve_installed_backend_registrations(settings)
    (registration,) = installed.registrations

    state = registration.create_state(registration.settings)
    issuer = state["video_material_upload_credential_issuer"]
    assert isinstance(issuer, TencentStsMaterialUploadCredentialIssuer)
    assert isinstance(
        state["video_material_object_verifier"], TencentCosMaterialObjectVerifier
    )
    assert isinstance(
        state["video_material_preview_url_issuer"], TencentCosMaterialPreviewUrlIssuer
    )
    assert isinstance(
        state["video_material_object_cleaner"], TencentCosMaterialObjectCleaner
    )


def test_video_studio_state_stays_fail_closed_without_sts_configuration() -> None:
    """未配置真实 STS 时不注入任何签发器，端点保持 503 失败关闭。"""

    installed = resolve_installed_backend_registrations(_settings(("video-studio",)))
    (registration,) = installed.registrations

    assert registration.create_state(registration.settings) == {}


def test_social_operations_declares_no_extra_app_state() -> None:
    installed = resolve_installed_backend_registrations(AppSettings())
    (registration,) = installed.registrations

    assert registration.create_state(registration.settings) == {}


def test_video_studio_declares_media_maintenance_background_worker() -> None:
    """M-2：video-studio 注册生产回收清扫为 lifespan 后台任务；social 无后台任务。"""

    import asyncio

    installed = resolve_installed_backend_registrations(_settings(("video-studio",)))
    (registration,) = installed.registrations

    workers = registration.create_background_workers(
        registration.settings,
        _fake_session_factory,
        registration.create_state(registration.settings),
    )
    assert len(workers) == 1
    name, worker_factory = workers[0]
    assert name == "video-media-library-maintenance"
    coroutine = worker_factory()
    assert asyncio.iscoroutine(coroutine)
    coroutine.close()

    social = resolve_installed_backend_registrations(AppSettings()).registrations[0]
    assert (
        social.create_background_workers(social.settings, _fake_session_factory, {})
        == ()
    )


def _fake_session_factory():  # pragma: no cover - 仅用于装配断言
    raise AssertionError("装配测试不应真正打开会话")
