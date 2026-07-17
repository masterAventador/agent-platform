from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID


class MaterialStorageCredentialsUnavailable(RuntimeError):
    """临时凭证签发上游失败或响应不可信时的失败关闭错误。"""


class MaterialObjectMissing(RuntimeError):
    """声明的存储对象在对象存储中不存在（客户端未完成直传或已被清理）。"""


class MaterialStorageUnavailable(RuntimeError):
    """对象存储上游暂时故障；不代表客户端上传有问题，可稍后重试。"""


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
    """Trusted object metadata returned by the configured storage provider.

    ``crc64ecma`` is the server-computed ``x-cos-hash-crc64ecma`` value and is
    the trusted content fingerprint. ``sha256`` is the client-written
    ``x-cos-meta-sha256`` metadata: it can be forged by a malicious client and
    must NOT be used as a security gate; it is retained only for display and
    accidental-corruption diagnostics.
    """

    size_bytes: int
    sha256: str
    crc64ecma: str = ""


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
