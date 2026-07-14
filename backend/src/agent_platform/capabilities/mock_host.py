from __future__ import annotations

from collections.abc import Mapping

from agent_platform.capabilities.manifest import (
    MANIFEST_SCHEMA_VERSION,
    CapabilityManifest,
    CoreProtocolDependency,
    ManifestValidationError,
)


class CapabilityHostError(RuntimeError):
    """Base error for isolated capability composition failures."""


class DuplicateCapabilityError(CapabilityHostError):
    """A capability id is already installed."""


class CapabilityConflictError(CapabilityHostError):
    """Two installed manifests claim the same host resource."""


class UnsatisfiedCoreProtocolError(CapabilityHostError):
    """The Mock Host cannot satisfy a versioned public Core dependency."""


class CapabilityNotInstalledError(CapabilityHostError):
    """A host operation targeted an unknown capability."""


class MockCapabilityHost:
    """Pure in-memory host used before the C17 production registry exists."""

    def __init__(self, *, core_protocols: Mapping[str, str]) -> None:
        checked_protocols: dict[str, str] = {}
        for protocol_id, protocol_version in core_protocols.items():
            try:
                dependency = CoreProtocolDependency(
                    protocol_id=protocol_id,
                    protocol_version=protocol_version,
                )
            except ManifestValidationError:
                raise ValueError("invalid Mock Host Core protocol") from None
            checked_protocols[dependency.protocol_id] = dependency.protocol_version
        self._core_protocols = checked_protocols
        self._manifests: dict[str, CapabilityManifest] = {}
        self._enabled: set[str] = set()
        self._resource_owners: dict[tuple[str, str], str] = {}

    @property
    def installed_capability_ids(self) -> frozenset[str]:
        return frozenset(self._manifests)

    @property
    def enabled_capability_ids(self) -> frozenset[str]:
        return frozenset(self._enabled)

    def install(self, manifest: CapabilityManifest) -> None:
        if not isinstance(manifest, CapabilityManifest):
            raise TypeError("manifest must be a CapabilityManifest")
        if manifest.schema_version != MANIFEST_SCHEMA_VERSION:
            raise UnsatisfiedCoreProtocolError("unsupported manifest schema")
        if manifest.capability_id in self._manifests:
            raise DuplicateCapabilityError("capability is already installed")
        for dependency in manifest.core_dependencies:
            if self._core_protocols.get(dependency.protocol_id) != dependency.protocol_version:
                raise UnsatisfiedCoreProtocolError("Core protocol dependency is unavailable")

        claims = manifest.resource_claims()
        if any(claim in self._resource_owners for claim in claims):
            raise CapabilityConflictError("capability resource conflict")

        self._manifests[manifest.capability_id] = manifest
        self._enabled.add(manifest.capability_id)
        self._resource_owners.update({claim: manifest.capability_id for claim in claims})

    def disable(self, capability_id: str) -> None:
        if capability_id not in self._manifests:
            raise CapabilityNotInstalledError("capability is not installed")
        self._enabled.discard(capability_id)
