"""能力包组合根：按显式安装清单装配后端路由与宿主注册表。

这是平台内唯一允许 import 具体能力包实现的 Core 装配层；
API、Worker 与业务模块只消费本模块产出的注册结果。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import Final

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
from agent_platform.capabilities.video_studio.manifest import VIDEO_STUDIO_MANIFEST
from agent_platform.capabilities.video_studio.registration import (
    VIDEO_STUDIO_BACKEND_REGISTRATION,
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


@dataclass(frozen=True, slots=True)
class BackendCapabilityRegistration:
    """One installed capability plus its backend router factory."""

    manifest: CapabilityManifest
    settings: AppSettings
    create_router: Callable[[AppSettings], APIRouter]


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


_BACKEND_ROUTER_FACTORIES: Final[Mapping[str, Callable[[AppSettings], APIRouter]]] = (
    MappingProxyType({"social-operations": _create_social_operations_router})
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
            )
        )
    return InstalledBackendCapabilities(
        host=host,
        catalog=KNOWN_CAPABILITY_MANIFESTS,
        registrations=tuple(registrations),
    )
