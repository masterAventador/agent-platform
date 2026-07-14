from agent_platform.capabilities.manifest import (
    MANIFEST_SCHEMA_VERSION,
    CapabilityManifest,
    CoreProtocolDependency,
)

SOCIAL_OPERATIONS_MANIFEST = CapabilityManifest(
    schema_version=MANIFEST_SCHEMA_VERSION,
    capability_id="social-operations",
    capability_version="1.0.0",
    resource_namespace="social",
    backend_routes=("/api/v1/social-operations",),
    worker_handlers=("social.jobs.v1",),
    permissions=(
        "social.read",
        "social.manage",
        "social.execute",
    ),
    events=(
        "social.task.requested.v1",
        "social.task.completed.v1",
    ),
    frontend_entries=("social.routes.v1",),
    migrations=("social.schema.v1",),
    health_checks=("social.health.v1",),
    desktop_components=("social.local_executor.v1",),
    core_dependencies=(
        CoreProtocolDependency("core.capability-host", "1.0"),
        CoreProtocolDependency("core.runs", "1.0"),
        CoreProtocolDependency("core.permissions", "1.0"),
        CoreProtocolDependency("core.events", "1.0"),
        CoreProtocolDependency("core.approvals", "1.0"),
        CoreProtocolDependency("core.audit", "1.0"),
    ),
)
