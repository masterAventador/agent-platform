from __future__ import annotations

from dataclasses import replace

import pytest

from agent_platform.capabilities.manifest import (
    CapabilityManifest,
    CoreProtocolDependency,
    ManifestValidationError,
)
from agent_platform.capabilities.mock_host import (
    CapabilityConflictError,
    CapabilityNotInstalledError,
    DuplicateCapabilityError,
    MockCapabilityHost,
    UnsatisfiedCoreProtocolError,
)
from agent_platform.capabilities.social_operations.manifest import (
    SOCIAL_OPERATIONS_MANIFEST,
)
from agent_platform.capabilities.video_studio.manifest import VIDEO_STUDIO_MANIFEST

CORE_PROTOCOLS = {
    "core.approvals": "1.0",
    "core.artifacts": "1.0",
    "core.audit": "1.0",
    "core.capability-host": "1.0",
    "core.events": "1.0",
    "core.permissions": "1.0",
    "core.runs": "1.0",
}


def _host() -> MockCapabilityHost:
    return MockCapabilityHost(core_protocols=CORE_PROTOCOLS)


@pytest.mark.parametrize(
    ("manifests", "expected_ids"),
    [
        ((), frozenset()),
        ((VIDEO_STUDIO_MANIFEST,), frozenset({"video-studio"})),
        ((SOCIAL_OPERATIONS_MANIFEST,), frozenset({"social-operations"})),
        (
            (VIDEO_STUDIO_MANIFEST, SOCIAL_OPERATIONS_MANIFEST),
            frozenset({"video-studio", "social-operations"}),
        ),
    ],
)
def test_core_and_optional_capability_combinations_are_isolated(
    manifests: tuple[CapabilityManifest, ...],
    expected_ids: frozenset[str],
) -> None:
    host = _host()

    for manifest in manifests:
        host.install(manifest)

    assert host.installed_capability_ids == expected_ids
    assert host.enabled_capability_ids == expected_ids


def test_disable_is_idempotent_and_does_not_uninstall_other_package() -> None:
    host = _host()
    host.install(VIDEO_STUDIO_MANIFEST)
    host.install(SOCIAL_OPERATIONS_MANIFEST)

    host.disable("video-studio")
    host.disable("video-studio")

    assert host.installed_capability_ids == frozenset({"video-studio", "social-operations"})
    assert host.enabled_capability_ids == frozenset({"social-operations"})


def test_disable_rejects_unknown_capability() -> None:
    with pytest.raises(CapabilityNotInstalledError):
        _host().disable("video-studio")


def test_duplicate_registration_is_rejected_without_mutating_host() -> None:
    host = _host()
    host.install(VIDEO_STUDIO_MANIFEST)

    with pytest.raises(DuplicateCapabilityError):
        host.install(VIDEO_STUDIO_MANIFEST)

    assert host.installed_capability_ids == frozenset({"video-studio"})


def test_unsupported_manifest_schema_is_rejected() -> None:
    host = _host()
    manifest = replace(VIDEO_STUDIO_MANIFEST, schema_version="2.0")

    with pytest.raises(UnsatisfiedCoreProtocolError) as error:
        host.install(manifest)

    assert "manifest schema" in str(error.value)
    assert "expected=1.0" in str(error.value)
    assert "actual=2.0" in str(error.value)
    assert host.installed_capability_ids == frozenset()


def test_resource_conflict_is_rejected_atomically() -> None:
    host = _host()
    host.install(VIDEO_STUDIO_MANIFEST)
    conflicting = replace(
        VIDEO_STUDIO_MANIFEST,
        capability_id="video-studio-secondary",
        backend_routes=("/api/v1/video-studio-secondary",),
    )

    with pytest.raises(CapabilityConflictError) as error:
        host.install(conflicting)

    assert "resource_namespace=video" in str(error.value)
    assert "owner=video-studio" in str(error.value)
    assert "requester=video-studio-secondary" in str(error.value)
    assert host.installed_capability_ids == frozenset({"video-studio"})
    assert host.enabled_capability_ids == frozenset({"video-studio"})


def test_resource_namespace_is_an_exclusive_host_claim() -> None:
    host = _host()
    host.install(VIDEO_STUDIO_MANIFEST)
    separate_resources_in_same_namespace = replace(
        VIDEO_STUDIO_MANIFEST,
        capability_id="video-editor",
        backend_routes=("/api/v1/video-editor",),
        worker_handlers=("video.editor.jobs.v1",),
        permissions=("video.editor.manage",),
        events=("video.editor.completed.v1",),
        frontend_entries=("video.editor.routes.v1",),
        migrations=("video.schema.v2",),
        health_checks=("video.editor.health.v1",),
        desktop_components=("video.editor.preview.v1",),
    )

    with pytest.raises(CapabilityConflictError) as error:
        host.install(separate_resources_in_same_namespace)

    assert "resource_namespace=video" in str(error.value)
    assert "owner=video-studio" in str(error.value)
    assert "requester=video-editor" in str(error.value)
    assert host.installed_capability_ids == frozenset({"video-studio"})


def test_noncanonical_route_cannot_bypass_resource_ownership() -> None:
    host = _host()
    host.install(VIDEO_STUDIO_MANIFEST)

    with pytest.raises(ManifestValidationError):
        replace(
            SOCIAL_OPERATIONS_MANIFEST,
            backend_routes=("/api/v1/video-studio/",),
        )

    assert host.installed_capability_ids == frozenset({"video-studio"})


@pytest.mark.parametrize("control", ["\x00", "\x1b[31m", "\u202e"])
def test_control_characters_are_rejected_before_conflict_diagnostics(
    control: str,
) -> None:
    host = _host()
    host.install(VIDEO_STUDIO_MANIFEST)

    with pytest.raises(ManifestValidationError) as error:
        replace(
            SOCIAL_OPERATIONS_MANIFEST,
            permissions=(f"social.manage{control}",),
        )

    assert control not in str(error.value)
    assert host.installed_capability_ids == frozenset({"video-studio"})


@pytest.mark.parametrize(
    "dependency",
    [
        CoreProtocolDependency(protocol_id="core.unknown", protocol_version="1.0"),
        CoreProtocolDependency(protocol_id="core.runs", protocol_version="2.0"),
    ],
)
def test_unsatisfied_core_dependency_is_rejected_without_partial_install(
    dependency: CoreProtocolDependency,
) -> None:
    host = _host()
    manifest = replace(VIDEO_STUDIO_MANIFEST, core_dependencies=(dependency,))

    with pytest.raises(UnsatisfiedCoreProtocolError) as error:
        host.install(manifest)

    assert dependency.protocol_id in str(error.value)
    assert f"expected={dependency.protocol_version}" in str(error.value)
    actual_version = CORE_PROTOCOLS.get(dependency.protocol_id, "<missing>")
    assert f"actual={actual_version}" in str(error.value)
    assert host.installed_capability_ids == frozenset()
    assert host.enabled_capability_ids == frozenset()


@pytest.mark.parametrize(
    "core_protocols",
    [
        {1: "1.0"},
        {None: "1.0"},
        {"core.runs": 1},
        {"core.runs": None},
    ],
)
def test_mock_host_normalizes_invalid_protocol_types_to_public_value_error(
    core_protocols: dict[object, object],
) -> None:
    with pytest.raises(ValueError, match="^invalid Mock Host Core protocol$"):
        MockCapabilityHost(core_protocols=core_protocols)  # type: ignore[arg-type]
