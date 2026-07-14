from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path

import pytest

from agent_platform.capabilities.manifest import (
    MANIFEST_SCHEMA_VERSION,
    CapabilityManifest,
    CoreProtocolDependency,
    ManifestValidationError,
)
from agent_platform.capabilities.social_operations.manifest import (
    SOCIAL_OPERATIONS_MANIFEST,
)
from agent_platform.capabilities.video_studio.manifest import VIDEO_STUDIO_MANIFEST

_SOURCE_ROOT = Path(__file__).parents[3] / "src" / "agent_platform"


@pytest.mark.parametrize(
    "manifest",
    [VIDEO_STUDIO_MANIFEST, SOCIAL_OPERATIONS_MANIFEST],
)
def test_builtin_manifests_are_versioned_complete_and_provider_neutral(
    manifest: CapabilityManifest,
) -> None:
    assert manifest.schema_version == MANIFEST_SCHEMA_VERSION
    assert manifest.capability_version == "1.0.0"
    assert manifest.backend_routes
    assert manifest.worker_handlers
    assert manifest.permissions
    assert manifest.events
    assert manifest.frontend_entries
    assert manifest.migrations
    assert manifest.health_checks
    assert all(
        dependency.protocol_id.startswith("core.") for dependency in manifest.core_dependencies
    )

    serialized = repr(manifest).lower()
    for provider_name in ("aliyun", "tencent", "cos", "mps", "playwright"):
        assert provider_name not in serialized


def test_builtin_manifests_use_separate_resource_namespaces() -> None:
    assert VIDEO_STUDIO_MANIFEST.capability_id == "video-studio"
    assert SOCIAL_OPERATIONS_MANIFEST.capability_id == "social-operations"

    for field_name in (
        "backend_routes",
        "worker_handlers",
        "permissions",
        "events",
        "frontend_entries",
        "migrations",
        "health_checks",
        "desktop_components",
    ):
        video_resources = set(getattr(VIDEO_STUDIO_MANIFEST, field_name))
        social_resources = set(getattr(SOCIAL_OPERATIONS_MANIFEST, field_name))
        assert video_resources.isdisjoint(social_resources)


def test_manifest_rejects_cross_capability_dependency_direction() -> None:
    with pytest.raises(ManifestValidationError):
        CoreProtocolDependency(
            protocol_id="capability.social-operations.internal",
            protocol_version="1.0",
        )


@pytest.mark.parametrize(
    ("package_name", "forbidden_package"),
    [
        ("video_studio", "agent_platform.capabilities.social_operations"),
        ("social_operations", "agent_platform.capabilities.video_studio"),
    ],
)
def test_capability_packages_do_not_import_each_other(
    package_name: str,
    forbidden_package: str,
) -> None:
    package_root = _SOURCE_ROOT / "capabilities" / package_name

    for source_file in package_root.rglob("*.py"):
        assert all(
            not imported_module.startswith(forbidden_package)
            for imported_module in _imported_modules(source_file)
        )


def test_core_business_modules_do_not_import_concrete_capabilities() -> None:
    concrete_packages = (
        "agent_platform.capabilities.video_studio",
        "agent_platform.capabilities.social_operations",
    )

    for core_directory in ("platform", "runtimes", "knowledge", "tools"):
        for source_file in (_SOURCE_ROOT / core_directory).rglob("*.py"):
            assert all(
                not imported_module.startswith(concrete_packages)
                for imported_module in _imported_modules(source_file)
            )


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("capability_id", "Video Studio"),
        ("capability_version", "1"),
        ("backend_routes", ("/api/v1/video-studio", "/api/v1/video-studio")),
        ("permissions", ()),
        ("migrations", ("0017",)),
    ],
)
def test_manifest_rejects_noncanonical_or_ambiguous_declarations(
    field_name: str,
    invalid_value: object,
) -> None:
    with pytest.raises(ManifestValidationError):
        replace(VIDEO_STUDIO_MANIFEST, **{field_name: invalid_value})


def _imported_modules(source_file: Path) -> tuple[str, ...]:
    parsed = ast.parse(source_file.read_text(encoding="utf-8"))
    imported_modules: list[str] = []
    for node in ast.walk(parsed):
        if isinstance(node, ast.Import):
            imported_modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported_modules.append(node.module)
    return tuple(imported_modules)
