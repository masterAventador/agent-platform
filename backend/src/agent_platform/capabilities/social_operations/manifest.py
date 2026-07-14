from agent_platform.capabilities.manifest import (
    MANIFEST_SCHEMA_VERSION,
    CapabilityManifest,
    CoreProtocolDependency,
)

SOCIAL_OPERATIONS_MANIFEST = CapabilityManifest(
    schema_version=MANIFEST_SCHEMA_VERSION,
    capability_id="social-operations",
    capability_version="1.0.0",
    backend_routes=("/api/v1/social-operations",),
    worker_handlers=("social_operations.jobs.v1",),
    permissions=(
        "social_operations.read",
        "social_operations.manage",
        "social_operations.execute",
    ),
    events=(
        "social_operations.task.requested.v1",
        "social_operations.task.completed.v1",
    ),
    frontend_entries=("social-operations.routes.v1",),
    migrations=("social-operations.schema.v1",),
    health_checks=("social-operations.health.v1",),
    desktop_components=("social-operations.local-executor.v1",),
    core_dependencies=(
        CoreProtocolDependency("core.capability-host", "1.0"),
        CoreProtocolDependency("core.runs", "1.0"),
        CoreProtocolDependency("core.permissions", "1.0"),
        CoreProtocolDependency("core.events", "1.0"),
        CoreProtocolDependency("core.approvals", "1.0"),
        CoreProtocolDependency("core.audit", "1.0"),
    ),
)
