from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from agent_platform.core_contract import CORE_API_ROUTE_ROOTS, CORE_RESOURCE_NAMESPACES

MANIFEST_SCHEMA_VERSION = "1.0"

_CAPABILITY_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_RESOURCE_NAMESPACE_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
_SEMANTIC_VERSION_PATTERN = re.compile(r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$")
_PROTOCOL_ID_PATTERN = re.compile(r"^core\.[a-z][a-z0-9-]*(?:\.[a-z][a-z0-9-]*)*$")
_PROTOCOL_VERSION_PATTERN = re.compile(r"^[1-9][0-9]*\.(?:0|[1-9][0-9]*)$")
_RESOURCE_DECLARATION_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
_ROUTE_SEGMENT_PATTERN = r"[a-z0-9]+(?:-[a-z0-9]+)*"


class ManifestValidationError(ValueError):
    """A capability manifest is not canonical or violates dependency direction."""


@dataclass(frozen=True, slots=True)
class CoreProtocolDependency:
    """Versioned reference to a public Core protocol, never another capability."""

    protocol_id: str
    protocol_version: str

    def __post_init__(self) -> None:
        if not _matches_pattern(_PROTOCOL_ID_PATTERN, self.protocol_id):
            raise ManifestValidationError("invalid Core protocol dependency")
        if not _matches_pattern(_PROTOCOL_VERSION_PATTERN, self.protocol_version):
            raise ManifestValidationError("invalid Core protocol version")


@dataclass(frozen=True, slots=True)
class CapabilityManifest:
    """Immutable installation declaration consumed by a capability host."""

    schema_version: str
    capability_id: str
    capability_version: str
    resource_namespace: str
    backend_routes: tuple[str, ...]
    worker_handlers: tuple[str, ...]
    permissions: tuple[str, ...]
    events: tuple[str, ...]
    frontend_entries: tuple[str, ...]
    migrations: tuple[str, ...]
    health_checks: tuple[str, ...]
    desktop_components: tuple[str, ...]
    core_dependencies: tuple[CoreProtocolDependency, ...]

    def __post_init__(self) -> None:
        if not _matches_pattern(_PROTOCOL_VERSION_PATTERN, self.schema_version):
            raise ManifestValidationError("invalid manifest schema version")
        if not _matches_pattern(_CAPABILITY_ID_PATTERN, self.capability_id):
            raise ManifestValidationError("invalid capability id")
        if self.capability_id in CORE_API_ROUTE_ROOTS:
            raise ManifestValidationError("reserved core API route root")
        if not _matches_pattern(_SEMANTIC_VERSION_PATTERN, self.capability_version):
            raise ManifestValidationError("invalid capability version")
        if (
            not _matches_pattern(_RESOURCE_NAMESPACE_PATTERN, self.resource_namespace)
            or self.resource_namespace in CORE_RESOURCE_NAMESPACES
            or self.resource_namespace != self.capability_id.partition("-")[0]
        ):
            raise ManifestValidationError("invalid resource namespace")

        required_declarations = (
            self.backend_routes,
            self.worker_handlers,
            self.permissions,
            self.events,
            self.frontend_entries,
            self.migrations,
            self.health_checks,
        )
        for declarations in required_declarations:
            _validate_declarations(declarations, required=True)
        _validate_declarations(self.desktop_components, required=False)

        route_root = f"/api/v1/{self.capability_id}"
        route_pattern = re.compile(rf"^{re.escape(route_root)}(?:/{_ROUTE_SEGMENT_PATTERN})*$")
        if any(route_pattern.fullmatch(route) is None for route in self.backend_routes):
            raise ManifestValidationError("invalid backend route declaration")

        resource_declarations = (
            self.worker_handlers,
            self.permissions,
            self.events,
            self.frontend_entries,
            self.migrations,
            self.health_checks,
            self.desktop_components,
        )
        if any(
            _RESOURCE_DECLARATION_PATTERN.fullmatch(declaration) is None
            or declaration.partition(".")[0] != self.resource_namespace
            for declarations in resource_declarations
            for declaration in declarations
        ):
            raise ManifestValidationError("invalid resource declaration")

        logical_migration_pattern = re.compile(
            rf"^{re.escape(self.resource_namespace)}\.schema\.v[1-9][0-9]*$"
        )
        if any(
            logical_migration_pattern.fullmatch(migration) is None for migration in self.migrations
        ):
            raise ManifestValidationError("invalid logical migration declaration")
        if not isinstance(self.core_dependencies, tuple) or not self.core_dependencies:
            raise ManifestValidationError("Core protocol dependencies are required")
        if any(
            not isinstance(dependency, CoreProtocolDependency)
            for dependency in self.core_dependencies
        ):
            raise ManifestValidationError("invalid Core protocol dependency")
        dependency_ids = tuple(dependency.protocol_id for dependency in self.core_dependencies)
        if len(dependency_ids) != len(set(dependency_ids)):
            raise ManifestValidationError("duplicate Core protocol dependency")

    def resource_claims(self) -> tuple[tuple[str, str], ...]:
        """Return namespaced exclusive claims used by a host conflict gate."""

        declaration_groups = (
            ("resource_namespace", (self.resource_namespace,)),
            ("backend_route", self.backend_routes),
            ("worker_handler", self.worker_handlers),
            ("permission", self.permissions),
            ("event", self.events),
            ("frontend_entry", self.frontend_entries),
            ("migration", self.migrations),
            ("health_check", self.health_checks),
            ("desktop_component", self.desktop_components),
        )
        return tuple(
            (category, declaration)
            for category, declarations in declaration_groups
            for declaration in declarations
        )


def _validate_declarations(values: tuple[str, ...], *, required: bool) -> None:
    if not isinstance(values, tuple) or (required and not values):
        raise ManifestValidationError("invalid manifest declarations")
    if any(
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or any(character.isspace() for character in value)
        or any(unicodedata.category(character).startswith("C") for character in value)
        for value in values
    ):
        raise ManifestValidationError("invalid manifest declaration")
    if len(values) != len(set(values)):
        raise ManifestValidationError("duplicate manifest declaration")


def _matches_pattern(pattern: re.Pattern[str], value: object) -> bool:
    return isinstance(value, str) and pattern.fullmatch(value) is not None
