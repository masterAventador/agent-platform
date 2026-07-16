"""部署安装层（deployment_installed）的能力包显式装配。

本模块是唯一允许 import 具体能力包的组合根：部署 Profile 声明安装哪些能力，
bootstrap 按声明把能力包的路由与数据库模型装配进 Core App；未安装的能力
不挂载路由、不注册模型，Core 单独启动不依赖任何能力包。

三层授权中的租户 Entitlement 与用户 `video.*` 权限域校验依赖 Core C17，
当前仅覆盖“部署已安装”这一层。
"""

from __future__ import annotations

from collections.abc import Iterable

from agent_platform.capabilities.registration import BackendCapabilityRegistration
from agent_platform.capabilities.video_studio.registration import (
    VIDEO_STUDIO_BACKEND_REGISTRATION,
)
from agent_platform.infrastructure.database.models import load_database_models


class UnknownInstalledCapabilityError(ValueError):
    """A deployment profile requested a capability this build cannot assemble."""


_INSTALLABLE_BACKEND_REGISTRATIONS: dict[str, BackendCapabilityRegistration] = {
    registration.manifest.capability_id: registration
    for registration in (VIDEO_STUDIO_BACKEND_REGISTRATION,)
}


def load_all_database_models() -> None:
    """注册 Core 与全部能力包模型到共享 Metadata（迁移与运行时同源）。

    能力包的表属于随代码交付的 schema，无论部署 Profile 是否安装该能力，
    Alembic 迁移都必须无条件包含这些模型；否则 ``alembic revision
    --autogenerate`` 会把已存在的能力包表误判为需要 DROP 的漂移。
    """

    load_database_models()
    for registration in _INSTALLABLE_BACKEND_REGISTRATIONS.values():
        if not registration.database_models:
            raise RuntimeError(
                f"capability declares no database models: "
                f"{registration.manifest.capability_id}"
            )


def resolve_installed_backend_registrations(
    capability_ids: Iterable[str],
) -> tuple[BackendCapabilityRegistration, ...]:
    """按部署声明解析能力包装配，未知或重复声明一律失败关闭。"""

    resolved: list[BackendCapabilityRegistration] = []
    seen: set[str] = set()
    for capability_id in capability_ids:
        if capability_id in seen:
            raise UnknownInstalledCapabilityError(
                f"duplicate installed capability: {capability_id}"
            )
        registration = _INSTALLABLE_BACKEND_REGISTRATIONS.get(capability_id)
        if registration is None:
            raise UnknownInstalledCapabilityError(f"unknown installed capability: {capability_id}")
        seen.add(capability_id)
        resolved.append(registration)
    return tuple(resolved)
