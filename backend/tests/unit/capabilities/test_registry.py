from __future__ import annotations

import pytest

from agent_platform.capabilities.manifest import CoreProtocolDependency
from agent_platform.capabilities.registry import (
    CapabilityConflictError,
    CapabilityHost,
    DuplicateCapabilityError,
    UnsatisfiedCoreProtocolError,
)
from agent_platform.capabilities.social_operations.manifest import SOCIAL_OPERATIONS_MANIFEST
from agent_platform.capabilities.video_studio.manifest import VIDEO_STUDIO_MANIFEST

_CORE_PROTOCOLS = {
    "core.capability-host": "1.0",
    "core.runs": "1.0",
    "core.permissions": "1.0",
    "core.events": "1.0",
    "core.approvals": "1.0",
    "core.audit": "1.0",
    "core.artifacts": "1.0",
}


def test_host_installs_manifests_and_resolves_them() -> None:
    host = CapabilityHost(core_protocols=_CORE_PROTOCOLS)
    host.install(SOCIAL_OPERATIONS_MANIFEST)
    host.install(VIDEO_STUDIO_MANIFEST)

    assert host.installed_capability_ids == frozenset({"social-operations", "video-studio"})
    assert host.manifest_for("social-operations") is SOCIAL_OPERATIONS_MANIFEST
    assert host.manifest_for("video-studio") is VIDEO_STUDIO_MANIFEST
    assert host.manifest_for("unknown") is None
    assert [manifest.capability_id for manifest in host.installed_manifests] == [
        "social-operations",
        "video-studio",
    ]


def test_host_rejects_duplicate_installation() -> None:
    host = CapabilityHost(core_protocols=_CORE_PROTOCOLS)
    host.install(SOCIAL_OPERATIONS_MANIFEST)
    with pytest.raises(DuplicateCapabilityError):
        host.install(SOCIAL_OPERATIONS_MANIFEST)


def test_host_rejects_unsatisfied_core_protocol() -> None:
    host = CapabilityHost(core_protocols={"core.capability-host": "1.0"})
    with pytest.raises(UnsatisfiedCoreProtocolError):
        host.install(SOCIAL_OPERATIONS_MANIFEST)


def test_host_rejects_resource_conflicts() -> None:
    from dataclasses import replace

    host = CapabilityHost(core_protocols=_CORE_PROTOCOLS)
    host.install(SOCIAL_OPERATIONS_MANIFEST)
    conflicting = replace(
        VIDEO_STUDIO_MANIFEST,
        capability_id="social-mirror",
        resource_namespace="social",
        backend_routes=("/api/v1/social-mirror",),
        worker_handlers=("social.jobs.v1",),
        permissions=("social.read",),
        events=("social.mirror.requested.v1",),
        frontend_entries=("social.mirror.routes.v1",),
        migrations=("social.schema.v1",),
        health_checks=("social.mirror.health.v1",),
        desktop_components=(),
        core_dependencies=(CoreProtocolDependency("core.capability-host", "1.0"),),
    )
    with pytest.raises(CapabilityConflictError):
        host.install(conflicting)
