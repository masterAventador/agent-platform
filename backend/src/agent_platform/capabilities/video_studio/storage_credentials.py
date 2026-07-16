from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class IssuedUploadCredentials:
    provider: str
    bucket: str
    region: str
    key_prefix: str
    tmp_secret_id: str
    tmp_secret_key: str
    session_token: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class StoredMaterialObject:
    """Trusted object metadata returned by the configured storage provider."""

    size_bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class IssuedMaterialPreview:
    url: str
    expires_at: datetime


class MaterialObjectVerifier(Protocol):
    async def inspect_uploaded_object(
        self,
        *,
        tenant_id: UUID,
        object_key: str,
    ) -> StoredMaterialObject: ...


class MaterialObjectCleaner(Protocol):
    async def delete_object(
        self,
        *,
        tenant_id: UUID,
        object_key: str,
    ) -> None: ...


class MaterialPreviewUrlIssuer(Protocol):
    async def issue_preview_url(
        self,
        *,
        tenant_id: UUID,
        object_key: str,
        expires_at: datetime,
    ) -> IssuedMaterialPreview: ...


class MaterialUploadCredentialIssuer(Protocol):
    async def issue_upload_credentials(
        self,
        *,
        tenant_id: UUID,
        key_prefix: str,
        expires_at: datetime,
        allowed_actions: tuple[str, ...],
    ) -> IssuedUploadCredentials: ...
