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


def test_video_studio_backend_host_is_not_installable_before_b04() -> None:
    with pytest.raises(CapabilityInstallationError):
        resolve_installed_backend_registrations(_settings(("video-studio",)))


def test_catalog_always_contains_known_manifests() -> None:
    installed = resolve_installed_backend_registrations(AppSettings())

    assert set(installed.catalog) == {"social-operations", "video-studio"}
    assert installed.catalog["social-operations"].capability_id == "social-operations"
