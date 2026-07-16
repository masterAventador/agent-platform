"""Explicit B04 storage test providers for the isolated Playwright process.

These providers intentionally live below ``tests``: they exercise the production API,
database repository and browser flow without creating a production fake-COS fallback.
Real Tencent STS/COS remains a separate external acceptance gate.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import select

from agent_platform.api.app import create_app
from agent_platform.bootstrap.capabilities import resolve_installed_backend_registrations
from agent_platform.capabilities.video_studio.media_library import MATERIAL_UPLOAD_ACTIONS
from agent_platform.capabilities.video_studio.persistence import (
    VideoMaterialRecord,
)
from agent_platform.capabilities.video_studio.storage_credentials import (
    IssuedMaterialPreview,
    IssuedUploadCredentials,
    StoredMaterialObject,
)


class PlaywrightCredentialIssuer:
    async def issue_upload_credentials(
        self,
        *,
        tenant_id: UUID,
        key_prefix: str,
        expires_at: datetime,
        allowed_actions: tuple[str, ...],
    ) -> IssuedUploadCredentials:
        expected_prefix = f"materials/{tenant_id}/"
        if not key_prefix.startswith(expected_prefix):
            raise ValueError("test STS scope escaped the tenant material prefix")
        if allowed_actions != MATERIAL_UPLOAD_ACTIONS:
            raise ValueError("test STS actions differ from the production contract")
        return IssuedUploadCredentials(
            provider="tencent-cos",
            bucket="agent-platform-materials-1250000000",
            region="ap-beijing",
            key_prefix=key_prefix,
            tmp_secret_id="playwright-tmp-id",
            tmp_secret_key="playwright-tmp-key",
            session_token="playwright-session-token",
            expires_at=expires_at,
        )


class PlaywrightObjectVerifier:
    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory

    async def inspect_uploaded_object(
        self,
        *,
        tenant_id: UUID,
        object_key: str,
    ) -> StoredMaterialObject:
        async with self._session_factory() as session:
            record = (
                await session.execute(
                    select(VideoMaterialRecord).where(
                        VideoMaterialRecord.tenant_id == tenant_id,
                        VideoMaterialRecord.storage_key == object_key,
                    )
                )
            ).scalar_one()
        return StoredMaterialObject(size_bytes=record.size_bytes, sha256=record.sha256)


class PlaywrightPreviewIssuer:
    async def issue_preview_url(
        self,
        *,
        tenant_id: UUID,
        object_key: str,
        expires_at: datetime,
    ) -> IssuedMaterialPreview:
        return IssuedMaterialPreview(
            url=f"https://preview.invalid/{tenant_id}/{object_key}",
            expires_at=expires_at,
        )


(_video_studio_registration,) = resolve_installed_backend_registrations(("video-studio",))
app = create_app(extra_routers=_video_studio_registration.routers)
app.state.video_material_upload_credential_issuer = PlaywrightCredentialIssuer()
app.state.video_material_object_verifier = PlaywrightObjectVerifier(app.state.session_factory)
app.state.video_material_preview_url_issuer = PlaywrightPreviewIssuer()
