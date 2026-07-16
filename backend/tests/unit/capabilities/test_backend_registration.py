from __future__ import annotations

import pytest
from fastapi import APIRouter

from agent_platform.bootstrap.capabilities import resolve_installed_backend_registrations
from agent_platform.capabilities.registration import (
    BackendCapabilityRegistration,
    BackendRegistrationValidationError,
)
from agent_platform.capabilities.registry import DuplicateCapabilityError
from agent_platform.capabilities.video_studio.manifest import VIDEO_STUDIO_MANIFEST
from agent_platform.capabilities.video_studio.registration import (
    VIDEO_STUDIO_BACKEND_REGISTRATION,
)
from agent_platform.config import AppSettings


def test_video_studio_backend_registration_declares_routes_and_models() -> None:
    registration = VIDEO_STUDIO_BACKEND_REGISTRATION

    assert registration.manifest is VIDEO_STUDIO_MANIFEST
    assert registration.routers
    route_root = "/api/v1/video-studio"
    for router in registration.routers:
        paths = [getattr(route, "path", "") for route in router.routes]
        assert paths
        assert all(path == route_root or path.startswith(f"{route_root}/") for path in paths)

    table_names = {model.__tablename__ for model in registration.database_models}
    assert table_names == {
        "video_material_folders",
        "video_materials",
        "video_material_references",
        "video_download_tasks",
    }


def test_backend_registration_rejects_router_outside_manifest_route_root() -> None:
    escaped_router = APIRouter(prefix="/api/v1/runs")

    @escaped_router.get("/escape")
    async def _escape() -> dict[str, str]:  # pragma: no cover - never mounted
        return {}

    with pytest.raises(BackendRegistrationValidationError):
        BackendCapabilityRegistration(
            manifest=VIDEO_STUDIO_MANIFEST,
            routers=(escaped_router,),
            database_models=VIDEO_STUDIO_BACKEND_REGISTRATION.database_models,
        )


def test_backend_registration_requires_routers_and_models() -> None:
    with pytest.raises(BackendRegistrationValidationError):
        BackendCapabilityRegistration(
            manifest=VIDEO_STUDIO_MANIFEST,
            routers=(),
            database_models=VIDEO_STUDIO_BACKEND_REGISTRATION.database_models,
        )
    with pytest.raises(BackendRegistrationValidationError):
        BackendCapabilityRegistration(
            manifest=VIDEO_STUDIO_MANIFEST,
            routers=VIDEO_STUDIO_BACKEND_REGISTRATION.routers,
            database_models=(),
        )


def test_bootstrap_rejects_duplicate_installed_capability() -> None:
    with pytest.raises(DuplicateCapabilityError):
        resolve_installed_backend_registrations(
            AppSettings(installed_capabilities=("social-operations", "social-operations"))
        )
