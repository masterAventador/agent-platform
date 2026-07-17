from __future__ import annotations

from agent_platform.platform.runs.events import EventType
from agent_platform.platform.tenants.permissions import TenantPermission

CORE_API_ROUTE_ROOTS = frozenset(
    {
        "approvals",
        "artifacts",
        "audit",
        "auth",
        "capabilities",
        "conversations",
        "employees",
        "files",
        "health",
        "knowledge-bases",
        "mcp-servers",
        "model-gateway",
        "observability",
        "run-dead-letters",
        "runs",
        "skills",
        "tool-invocations",
        "tools",
        "workbench",
    }
)

CORE_PERMISSION_NAMESPACES = frozenset(
    permission.value.partition(".")[0] for permission in TenantPermission
)
CORE_EVENT_NAMESPACES = frozenset(event_type.value.partition(".")[0] for event_type in EventType)
CORE_RESOURCE_NAMESPACES = frozenset({"core", *CORE_PERMISSION_NAMESPACES, *CORE_EVENT_NAMESPACES})
