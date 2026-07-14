from agent_platform.capabilities.manifest import (
    MANIFEST_SCHEMA_VERSION,
    CapabilityManifest,
    CoreProtocolDependency,
)

VIDEO_STUDIO_MANIFEST = CapabilityManifest(
    schema_version=MANIFEST_SCHEMA_VERSION,
    capability_id="video-studio",
    capability_version="1.0.0",
    backend_routes=("/api/v1/video-studio",),
    worker_handlers=("video_studio.jobs.v1",),
    permissions=("video.read", "video.manage", "video.execute"),
    events=("video.render.requested.v1", "video.render.completed.v1"),
    frontend_entries=("video-studio.routes.v1",),
    migrations=("video-studio.schema.v1",),
    health_checks=("video-studio.health.v1",),
    desktop_components=("video-studio.preview.v1",),
    core_dependencies=(
        CoreProtocolDependency("core.capability-host", "1.0"),
        CoreProtocolDependency("core.runs", "1.0"),
        CoreProtocolDependency("core.artifacts", "1.0"),
        CoreProtocolDependency("core.permissions", "1.0"),
        CoreProtocolDependency("core.events", "1.0"),
    ),
)
