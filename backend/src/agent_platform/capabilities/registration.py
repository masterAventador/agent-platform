"""能力包后端装配声明协议。

能力包用 :class:`BackendCapabilityRegistration` 声明自己的 FastAPI 路由和
SQLAlchemy 模型；Core 不导入任何具体能力包，装配由部署层 bootstrap
（``agent_platform.bootstrap.capabilities``）显式完成。
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import APIRouter

from agent_platform.capabilities.manifest import CapabilityManifest
from agent_platform.infrastructure.database.base import Base


class BackendRegistrationValidationError(ValueError):
    """A backend capability registration violates its manifest declaration."""


@dataclass(frozen=True, slots=True)
class BackendCapabilityRegistration:
    """Immutable backend assembly declaration owned by a capability package."""

    manifest: CapabilityManifest
    routers: tuple[APIRouter, ...]
    database_models: tuple[type[Base], ...]

    def __post_init__(self) -> None:
        if not isinstance(self.manifest, CapabilityManifest):
            raise BackendRegistrationValidationError("manifest must be a CapabilityManifest")
        if not isinstance(self.routers, tuple) or not self.routers:
            raise BackendRegistrationValidationError("backend routers are required")
        if any(not isinstance(router, APIRouter) for router in self.routers):
            raise BackendRegistrationValidationError("invalid backend router")

        route_root = f"/api/v1/{self.manifest.capability_id}"
        for router in self.routers:
            paths = [getattr(route, "path", None) for route in router.routes]
            if not paths:
                raise BackendRegistrationValidationError("backend router declares no routes")
            if any(
                not isinstance(path, str)
                or (path != route_root and not path.startswith(f"{route_root}/"))
                for path in paths
            ):
                raise BackendRegistrationValidationError(
                    "backend route escapes the manifest route root"
                )

        if not isinstance(self.database_models, tuple) or not self.database_models:
            raise BackendRegistrationValidationError("database models are required")
        if any(
            not isinstance(model, type) or not issubclass(model, Base)
            for model in self.database_models
        ):
            raise BackendRegistrationValidationError("invalid database model")
        if len(self.database_models) != len(set(self.database_models)):
            raise BackendRegistrationValidationError("duplicate database model")
