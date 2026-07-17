"""能力包组合根：按显式安装清单装配后端路由与宿主注册表。

这是平台内唯一允许 import 具体能力包实现的 Core 装配层；
API、Worker 与业务模块只消费本模块产出的注册结果。
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final, cast

from fastapi import APIRouter

from agent_platform.capabilities.manifest import CapabilityManifest
from agent_platform.capabilities.registration import (
    BackendCapabilityRegistration as CapabilityPackageRegistration,
)
from agent_platform.capabilities.registry import CapabilityHost, CapabilityHostError
from agent_platform.capabilities.request_context import (
    ContextBufferAuditSink,
    require_capability_request_context,
)
from agent_platform.capabilities.social_operations.device_account_api import (
    create_device_account_router,
)
from agent_platform.capabilities.social_operations.device_account_persistence import (
    SqliteDeviceAccountStateStore,
)
from agent_platform.capabilities.social_operations.device_account_service import (
    ActorContext,
    DeviceAccountService,
)
from agent_platform.capabilities.social_operations.manifest import SOCIAL_OPERATIONS_MANIFEST
from agent_platform.capabilities.video_studio.maintenance import (
    run_media_library_maintenance,
)
from agent_platform.capabilities.video_studio.manifest import VIDEO_STUDIO_MANIFEST
from agent_platform.capabilities.video_studio.registration import (
    VIDEO_STUDIO_BACKEND_REGISTRATION,
)
from agent_platform.capabilities.video_studio.storage_credentials import (
    MaterialObjectCleaner,
)
from agent_platform.capabilities.video_studio.tencent_cos import (
    TencentCosMaterialObjectCleaner,
    TencentCosMaterialObjectVerifier,
    TencentCosMaterialPreviewUrlIssuer,
    create_cos_client,
)
from agent_platform.capabilities.video_studio.tencent_sts import (
    TencentStsMaterialUploadCredentialIssuer,
)
from agent_platform.config import AppSettings
from agent_platform.infrastructure.database.models import load_database_models


class CapabilityInstallationError(CapabilityHostError):
    """The deployment installation list cannot be satisfied fail-closed."""


CORE_PROTOCOL_VERSIONS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "core.capability-host": "1.0",
        "core.runs": "1.0",
        "core.permissions": "1.0",
        "core.events": "1.0",
        "core.approvals": "1.0",
        "core.audit": "1.0",
        "core.artifacts": "1.0",
    }
)

KNOWN_CAPABILITY_MANIFESTS: Final[Mapping[str, CapabilityManifest]] = MappingProxyType(
    {
        SOCIAL_OPERATIONS_MANIFEST.capability_id: SOCIAL_OPERATIONS_MANIFEST,
        VIDEO_STUDIO_MANIFEST.capability_id: VIDEO_STUDIO_MANIFEST,
    }
)

# 声明了平台 PostgreSQL 模型的能力包注册。能力包的表属于随代码交付的
# schema，无论部署 Profile 是否安装该能力，Alembic 迁移都必须无条件包含
# 这些模型；否则 ``alembic revision --autogenerate`` 会把已存在的能力包表
# 误判为需要 DROP 的漂移。
_CAPABILITY_DATABASE_MODEL_REGISTRATIONS: Final[tuple[CapabilityPackageRegistration, ...]] = (
    VIDEO_STUDIO_BACKEND_REGISTRATION,
)


def load_all_database_models() -> None:
    """注册 Core 与全部能力包模型到共享 Metadata（迁移与运行时同源）。"""

    load_database_models()
    for registration in _CAPABILITY_DATABASE_MODEL_REGISTRATIONS:
        if not registration.database_models:
            raise RuntimeError(
                f"capability declares no database models: "
                f"{registration.manifest.capability_id}"
            )


def _no_extra_state(settings: AppSettings) -> Mapping[str, object]:
    del settings
    return {}


# (worker 名称, 协程工厂)：由 API lifespan 启动为常驻后台任务、停机时取消。
BackgroundWorkerFactory = Callable[[], Coroutine[Any, Any, None]]
CapabilityBackgroundWorkers = tuple[tuple[str, BackgroundWorkerFactory], ...]


def _no_background_workers(
    settings: AppSettings,
    session_factory: object,
    state: Mapping[str, object],
) -> CapabilityBackgroundWorkers:
    del settings, session_factory, state
    return ()


@dataclass(frozen=True, slots=True)
class BackendCapabilityRegistration:
    """One installed capability plus its backend router factory."""

    manifest: CapabilityManifest
    settings: AppSettings
    create_router: Callable[[AppSettings], APIRouter]
    # 能力包生产装配所需的 app.state 注入（如真实云凭据 Provider）；
    # 未配置的能力保持空字典，对应端点按各自约定失败关闭。
    create_state: Callable[[AppSettings], Mapping[str, object]] = _no_extra_state
    # 能力包生产后台任务（如素材回收清扫）；默认没有。
    create_background_workers: Callable[
        [AppSettings, object, Mapping[str, object]], CapabilityBackgroundWorkers
    ] = _no_background_workers


@dataclass(frozen=True, slots=True)
class InstalledBackendCapabilities:
    host: CapabilityHost
    catalog: Mapping[str, CapabilityManifest]
    registrations: tuple[BackendCapabilityRegistration, ...]


def _social_operations_actor() -> ActorContext:
    context = require_capability_request_context()
    return ActorContext(
        tenant_id=context.tenant_id,
        user_id=context.user_id,
        permissions=context.permissions,
    )


def _create_social_operations_router(settings: AppSettings) -> APIRouter:
    state_store = None
    if settings.social_operations_state_path is not None:
        state_store = SqliteDeviceAccountStateStore(Path(settings.social_operations_state_path))
    service = DeviceAccountService(
        clock=lambda: datetime.now(UTC),
        audit_sink=ContextBufferAuditSink(),
        offline_after=timedelta(seconds=settings.social_operations_offline_after_seconds),
        claim_lease=timedelta(seconds=settings.social_operations_claim_lease_seconds),
        state_store=state_store,
    )
    return create_device_account_router(service, actor_provider=_social_operations_actor)


def _create_video_studio_router(settings: AppSettings) -> APIRouter:
    del settings
    (media_library_router,) = VIDEO_STUDIO_BACKEND_REGISTRATION.routers
    return media_library_router


def _create_video_studio_state(settings: AppSettings) -> Mapping[str, object]:
    """按部署配置装配真实腾讯 CAM/STS 签发器；配置不全时保持失败关闭。"""

    if (
        not settings.video_material_cos_bucket
        or not settings.cos_region
        or not settings.cos_secret_id.get_secret_value()
        or not settings.cos_secret_key.get_secret_value()
    ):
        return {}
    bucket = settings.video_material_cos_bucket
    cos_client = create_cos_client(
        region=settings.cos_region,
        secret_id=settings.cos_secret_id.get_secret_value(),
        secret_key=settings.cos_secret_key.get_secret_value(),
        scheme=settings.cos_scheme,
    )
    return {
        "video_material_upload_credential_issuer": TencentStsMaterialUploadCredentialIssuer(
            secret_id=settings.cos_secret_id.get_secret_value(),
            secret_key=settings.cos_secret_key.get_secret_value(),
            bucket=bucket,
            region=settings.cos_region,
        ),
        "video_material_object_verifier": TencentCosMaterialObjectVerifier(
            bucket=bucket, client=cos_client
        ),
        "video_material_preview_url_issuer": TencentCosMaterialPreviewUrlIssuer(
            bucket=bucket, client=cos_client
        ),
        "video_material_object_cleaner": TencentCosMaterialObjectCleaner(
            bucket=bucket, client=cos_client
        ),
    }


def _create_video_studio_background_workers(
    settings: AppSettings,
    session_factory: object,
    state: Mapping[str, object],
) -> CapabilityBackgroundWorkers:
    from agent_platform.capabilities.video_studio.maintenance import SessionFactory

    object_cleaner = cast(
        "MaterialObjectCleaner | None", state.get("video_material_object_cleaner")
    )

    def factory() -> Coroutine[Any, Any, None]:
        return run_media_library_maintenance(
            session_factory=cast(SessionFactory, session_factory),
            object_cleaner=object_cleaner,
            interval_seconds=settings.video_media_maintenance_interval_seconds,
            batch_limit=settings.video_media_maintenance_batch_limit,
        )

    return (("video-media-library-maintenance", factory),)


_BACKEND_STATE_FACTORIES: Final[Mapping[str, Callable[[AppSettings], Mapping[str, object]]]] = (
    MappingProxyType({"video-studio": _create_video_studio_state})
)

_BACKEND_WORKER_FACTORIES: Final[
    Mapping[str, Callable[[AppSettings, object, Mapping[str, object]], CapabilityBackgroundWorkers]]
] = MappingProxyType({"video-studio": _create_video_studio_background_workers})


_BACKEND_ROUTER_FACTORIES: Final[Mapping[str, Callable[[AppSettings], APIRouter]]] = (
    MappingProxyType(
        {
            "social-operations": _create_social_operations_router,
            "video-studio": _create_video_studio_router,
        }
    )
)


def resolve_installed_backend_registrations(settings: AppSettings) -> InstalledBackendCapabilities:
    host = CapabilityHost(core_protocols=CORE_PROTOCOL_VERSIONS)
    registrations: list[BackendCapabilityRegistration] = []
    for capability_id in settings.installed_capabilities:
        manifest = KNOWN_CAPABILITY_MANIFESTS.get(capability_id)
        if manifest is None:
            raise CapabilityInstallationError(
                f"unknown capability in installation list: {capability_id}"
            )
        router_factory = _BACKEND_ROUTER_FACTORIES.get(capability_id)
        if router_factory is None:
            raise CapabilityInstallationError(
                f"capability has no backend host integration yet: {capability_id}"
            )
        host.install(manifest)
        registrations.append(
            BackendCapabilityRegistration(
                manifest=manifest,
                settings=settings,
                create_router=router_factory,
                create_state=_BACKEND_STATE_FACTORIES.get(capability_id, _no_extra_state),
                create_background_workers=_BACKEND_WORKER_FACTORIES.get(
                    capability_id, _no_background_workers
                ),
            )
        )
    return InstalledBackendCapabilities(
        host=host,
        catalog=KNOWN_CAPABILITY_MANIFESTS,
        registrations=tuple(registrations),
    )
