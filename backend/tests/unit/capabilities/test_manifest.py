from __future__ import annotations

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
from agent_platform.core_contract import (
    CORE_API_ROUTE_ROOTS,
    CORE_EVENT_NAMESPACES,
    CORE_PERMISSION_NAMESPACES,
    CORE_RESOURCE_NAMESPACES,
)
from agent_platform.platform.runs.events import EventType
from agent_platform.platform.tenants.permissions import TenantPermission

_BACKEND_ARCHITECTURE = Path(__file__).parents[4] / "docs" / "backend-architecture.md"


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


@pytest.mark.parametrize(
    ("manifest", "expected_namespace", "expected_resources", "expected_dependencies"),
    [
        (
            VIDEO_STUDIO_MANIFEST,
            "video",
            {
                "backend_routes": ("/api/v1/video-studio",),
                "worker_handlers": ("video.jobs.v1",),
                "permissions": ("video.read", "video.manage", "video.execute"),
                "events": ("video.render.requested.v1", "video.render.completed.v1"),
                "frontend_entries": ("video.routes.v1",),
                "migrations": ("video.schema.v1",),
                "health_checks": ("video.health.v1",),
                "desktop_components": ("video.preview.v1",),
            },
            (
                CoreProtocolDependency("core.capability-host", "1.0"),
                CoreProtocolDependency("core.runs", "1.0"),
                CoreProtocolDependency("core.artifacts", "1.0"),
                CoreProtocolDependency("core.permissions", "1.0"),
                CoreProtocolDependency("core.events", "1.0"),
            ),
        ),
        (
            SOCIAL_OPERATIONS_MANIFEST,
            "social",
            {
                "backend_routes": ("/api/v1/social-operations",),
                "worker_handlers": ("social.jobs.v1",),
                "permissions": ("social.read", "social.manage", "social.execute"),
                "events": (
                    "social.task.requested.v1",
                    "social.task.completed.v1",
                ),
                "frontend_entries": ("social.routes.v1",),
                "migrations": ("social.schema.v1",),
                "health_checks": ("social.health.v1",),
                "desktop_components": ("social.local_executor.v1",),
            },
            (
                CoreProtocolDependency("core.capability-host", "1.0"),
                CoreProtocolDependency("core.runs", "1.0"),
                CoreProtocolDependency("core.permissions", "1.0"),
                CoreProtocolDependency("core.events", "1.0"),
                CoreProtocolDependency("core.approvals", "1.0"),
                CoreProtocolDependency("core.audit", "1.0"),
            ),
        ),
    ],
)
def test_builtin_manifests_match_reviewed_provider_neutral_protocol(
    manifest: CapabilityManifest,
    expected_namespace: str,
    expected_resources: dict[str, tuple[str, ...]],
    expected_dependencies: tuple[CoreProtocolDependency, ...],
) -> None:
    assert manifest.resource_namespace == expected_namespace
    for field_name, expected_values in expected_resources.items():
        assert getattr(manifest, field_name) == expected_values
    assert manifest.core_dependencies == expected_dependencies


def test_architecture_permission_namespace_examples_match_builtin_manifests() -> None:
    architecture = _BACKEND_ARCHITECTURE.read_text(encoding="utf-8")

    for manifest in (VIDEO_STUDIO_MANIFEST, SOCIAL_OPERATIONS_MANIFEST):
        assert f"`{manifest.resource_namespace}.*`" in architecture


def test_provider_neutral_tokens_do_not_reject_cost_events_by_substring() -> None:
    manifest = replace(
        VIDEO_STUDIO_MANIFEST,
        events=(*VIDEO_STUDIO_MANIFEST.events, "video.cost.estimated.v1"),
    )

    assert "video.cost.estimated.v1" in manifest.events


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
        replace(
            VIDEO_STUDIO_MANIFEST,
            **{field_name: invalid_value},  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("schema_version", 1),
        ("schema_version", None),
        ("capability_id", 1),
        ("capability_id", None),
        ("capability_version", 1),
        ("capability_version", None),
    ],
)
def test_manifest_rejects_non_string_regex_fields_with_public_error(
    field_name: str,
    invalid_value: object,
) -> None:
    with pytest.raises(ManifestValidationError):
        replace(
            VIDEO_STUDIO_MANIFEST,
            **{field_name: invalid_value},  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    ("protocol_id", "protocol_version"),
    [
        (1, "1.0"),
        (None, "1.0"),
        ("core.runs", 1),
        ("core.runs", None),
    ],
)
def test_core_dependency_rejects_non_string_regex_fields_with_public_error(
    protocol_id: object,
    protocol_version: object,
) -> None:
    with pytest.raises(ManifestValidationError):
        CoreProtocolDependency(
            protocol_id=protocol_id,  # type: ignore[arg-type]
            protocol_version=protocol_version,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    "invalid_namespace",
    ["core", "video.studio", "Video", "video\x00"],
)
def test_manifest_rejects_invalid_or_reserved_resource_namespace(
    invalid_namespace: str,
) -> None:
    with pytest.raises(ManifestValidationError):
        replace(
            VIDEO_STUDIO_MANIFEST,
            resource_namespace=invalid_namespace,
        )


@pytest.mark.parametrize(
    "core_namespace",
    [
        "core",
        "workspace",
        "employees",
        "knowledge",
        "skills",
        "tools",
        "operations",
        "runs",
        "models",
        "run",
        "message",
        "plan",
        "skill",
        "tool",
        "subagent",
        "approval",
        "artifact",
    ],
)
def test_manifest_rejects_core_owned_resource_namespace(core_namespace: str) -> None:
    capability_id = f"{core_namespace}-tools"

    with pytest.raises(ManifestValidationError):
        replace(
            VIDEO_STUDIO_MANIFEST,
            capability_id=capability_id,
            resource_namespace=core_namespace,
            backend_routes=(f"/api/v1/{capability_id}",),
            worker_handlers=(f"{core_namespace}.jobs.v1",),
            permissions=(f"{core_namespace}.manage",),
            events=(f"{core_namespace}.task.requested.v1",),
            frontend_entries=(f"{core_namespace}.routes.v1",),
            migrations=(f"{core_namespace}.schema.v1",),
            health_checks=(f"{core_namespace}.health.v1",),
            desktop_components=(f"{core_namespace}.executor.v1",),
        )


def test_core_resource_namespaces_follow_stable_permission_and_event_contracts() -> None:
    expected_permission_namespaces = {
        permission.value.partition(".")[0] for permission in TenantPermission
    }
    expected_event_namespaces = {event_type.value.partition(".")[0] for event_type in EventType}

    assert expected_permission_namespaces == CORE_PERMISSION_NAMESPACES
    assert expected_event_namespaces == CORE_EVENT_NAMESPACES
    assert {
        "core",
        *expected_permission_namespaces,
        *expected_event_namespaces,
    } == CORE_RESOURCE_NAMESPACES


@pytest.mark.parametrize(
    "core_route_root",
    ["auth", "model-gateway", "mcp-servers", "run-dead-letters"],
)
def test_manifest_rejects_core_api_route_root(core_route_root: str) -> None:
    resource_namespace = core_route_root.partition("-")[0]

    with pytest.raises(ManifestValidationError):
        replace(
            VIDEO_STUDIO_MANIFEST,
            capability_id=core_route_root,
            resource_namespace=resource_namespace,
            backend_routes=(f"/api/v1/{core_route_root}",),
            worker_handlers=(f"{resource_namespace}.jobs.v1",),
            permissions=(f"{resource_namespace}.manage",),
            events=(f"{resource_namespace}.task.requested.v1",),
            frontend_entries=(f"{resource_namespace}.routes.v1",),
            migrations=(f"{resource_namespace}.schema.v1",),
            health_checks=(f"{resource_namespace}.health.v1",),
            desktop_components=(f"{resource_namespace}.executor.v1",),
        )


def test_reserved_core_api_route_roots_match_the_running_app_contract() -> None:
    from agent_platform.api.app import create_app

    openapi_paths = create_app().openapi().get("paths")
    assert isinstance(openapi_paths, dict)
    app_route_roots = {
        path.removeprefix("/api/v1/").partition("/")[0]
        for path in openapi_paths
        if path.startswith("/api/v1/")
    }

    assert app_route_roots == CORE_API_ROUTE_ROOTS


@pytest.mark.parametrize(
    ("field_name", "foreign_declaration"),
    [
        ("backend_routes", ("/api/v1/video-studio",)),
        ("worker_handlers", ("video.jobs.v1",)),
        ("permissions", ("video.manage",)),
        ("permissions", ("core.permissions.manage",)),
        ("events", ("video.render.requested.v1",)),
        ("frontend_entries", ("video.routes.v1",)),
        ("migrations", ("video.schema.v1",)),
        ("health_checks", ("video.health.v1",)),
        ("desktop_components", ("video.preview.v1",)),
    ],
)
def test_manifest_rejects_resources_owned_by_another_namespace(
    field_name: str,
    foreign_declaration: tuple[str, ...],
) -> None:
    with pytest.raises(ManifestValidationError):
        replace(
            SOCIAL_OPERATIONS_MANIFEST,
            **{field_name: foreign_declaration},  # type: ignore[arg-type]
        )


@pytest.mark.parametrize("impersonated_namespace", ["workspace", "video"])
def test_capability_cannot_impersonate_another_resource_namespace(
    impersonated_namespace: str,
) -> None:
    with pytest.raises(ManifestValidationError):
        replace(
            SOCIAL_OPERATIONS_MANIFEST,
            resource_namespace=impersonated_namespace,
            worker_handlers=(f"{impersonated_namespace}.jobs.v1",),
            permissions=(f"{impersonated_namespace}.manage",),
            events=(f"{impersonated_namespace}.task.requested.v1",),
            frontend_entries=(f"{impersonated_namespace}.routes.v1",),
            migrations=(f"{impersonated_namespace}.schema.v1",),
            health_checks=(f"{impersonated_namespace}.health.v1",),
            desktop_components=(f"{impersonated_namespace}.executor.v1",),
        )


@pytest.mark.parametrize(
    "route",
    [
        "/api/v1/video-studio/",
        "/api/v1/video-studio-extra",
        "/api/v1/video-studio//jobs",
        "/api/v1/video-studio/jobs/",
    ],
)
def test_manifest_rejects_noncanonical_or_foreign_routes(route: str) -> None:
    with pytest.raises(ManifestValidationError):
        replace(VIDEO_STUDIO_MANIFEST, backend_routes=(route,))


@pytest.mark.parametrize(
    ("field_name", "declaration"),
    [
        ("backend_routes", ("/api/v1/video-studio/\x00jobs",)),
        ("worker_handlers", ("video.jobs.\x1b[31m",)),
        ("permissions", ("video.\u202emanage",)),
        ("events", ("video.render\u2066.requested.v1",)),
        ("frontend_entries", ("video.routes\x7f.v1",)),
        ("migrations", ("video.schema.v1\x00",)),
        ("health_checks", ("video.health\x1b.v1",)),
        ("desktop_components", ("video.preview\u200b.v1",)),
    ],
)
def test_manifest_rejects_control_characters_in_resource_declarations(
    field_name: str,
    declaration: tuple[str, ...],
) -> None:
    with pytest.raises(ManifestValidationError):
        replace(
            VIDEO_STUDIO_MANIFEST,
            **{field_name: declaration},  # type: ignore[arg-type]
        )
