"""Video Studio 能力包的后端装配声明。"""

from __future__ import annotations

from agent_platform.capabilities.registration import BackendCapabilityRegistration
from agent_platform.capabilities.video_studio.api import router as media_library_router
from agent_platform.capabilities.video_studio.manifest import VIDEO_STUDIO_MANIFEST
from agent_platform.capabilities.video_studio.persistence import (
    VideoDownloadTaskRecord,
    VideoMaterialFolderRecord,
    VideoMaterialRecord,
    VideoMaterialReferenceRecord,
)

VIDEO_STUDIO_BACKEND_REGISTRATION = BackendCapabilityRegistration(
    manifest=VIDEO_STUDIO_MANIFEST,
    routers=(media_library_router,),
    database_models=(
        VideoMaterialFolderRecord,
        VideoMaterialRecord,
        VideoMaterialReferenceRecord,
        VideoDownloadTaskRecord,
    ),
)
