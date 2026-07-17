"""生产腾讯 COS 素材对象核验 / 预签名预览 / 清理 Provider（M-3）。

核验选型：COS 的 ETag 对多段上传不是内容 MD5，也没有服务端 sha256；
平台以「前端直传时写入的自定义元数据头 ``x-cos-meta-sha256`` + 服务端可信的
``Content-Length``」与草稿声明比对。元数据由声明同一 sha256 的客户端写入，
可防意外损坏与换文件；对抗恶意客户端伪造摘要需升级为草稿声明 crc64ecma 并
与 COS 服务端计算的 ``x-cos-hash-crc64ecma`` 比对（记录为后续硬化项）。

SDK（cos-python-sdk-v5，已锁定）只在默认客户端工厂内使用；404 转换为
``MaterialObjectMissing``，其余上游故障转换为 ``MaterialStorageUnavailable``。
"""

from __future__ import annotations

import asyncio
import math
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

from agent_platform.capabilities.video_studio.storage_credentials import (
    IssuedMaterialPreview,
    MaterialObjectMissing,
    MaterialStorageUnavailable,
    StoredMaterialObject,
)

SHA256_METADATA_HEADER = "x-cos-meta-sha256"


class CosObjectClient(Protocol):
    def head_object(self, *, Bucket: str, Key: str) -> dict[str, Any]: ...

    def delete_object(self, *, Bucket: str, Key: str) -> Any: ...

    def get_presigned_url(
        self, *, Method: str, Bucket: str, Key: str, Expired: int
    ) -> str: ...


def create_cos_client(
    *,
    region: str,
    secret_id: str,
    secret_key: str,
    scheme: str = "https",
) -> CosObjectClient:
    from qcloud_cos import CosConfig, CosS3Client  # type: ignore[import-untyped]

    return CosS3Client(  # type: ignore[no-any-return]
        CosConfig(Region=region, SecretId=secret_id, SecretKey=secret_key, Scheme=scheme)
    )


def _status_code_of(error: Exception) -> int | None:
    get_status_code = getattr(error, "get_status_code", None)
    if callable(get_status_code):
        try:
            return int(get_status_code())
        except Exception:  # pragma: no cover - 上游异常自身损坏
            return None
    return None


def _translate_error(error: Exception, *, object_key: str) -> Exception:
    if _status_code_of(error) == 404:
        return MaterialObjectMissing(f"存储对象不存在: {object_key}")
    return MaterialStorageUnavailable("素材对象存储暂时不可用")


class TencentCosMaterialObjectVerifier:
    def __init__(self, *, bucket: str, client: CosObjectClient) -> None:
        self._bucket = bucket
        self._client = client

    async def inspect_uploaded_object(
        self,
        *,
        tenant_id: UUID,
        object_key: str,
    ) -> StoredMaterialObject:
        del tenant_id
        try:
            headers = await asyncio.to_thread(
                self._client.head_object, Bucket=self._bucket, Key=object_key
            )
            size_bytes = int(headers["Content-Length"])
        except (MaterialObjectMissing, MaterialStorageUnavailable):
            raise
        except Exception as error:
            raise _translate_error(error, object_key=object_key) from error
        # 元数据缺失 → 空摘要，由服务层按「与声明不一致」失败关闭。
        sha256 = str(headers.get(SHA256_METADATA_HEADER, ""))
        return StoredMaterialObject(size_bytes=size_bytes, sha256=sha256)


class TencentCosMaterialPreviewUrlIssuer:
    def __init__(self, *, bucket: str, client: CosObjectClient) -> None:
        self._bucket = bucket
        self._client = client

    async def issue_preview_url(
        self,
        *,
        tenant_id: UUID,
        object_key: str,
        expires_at: datetime,
    ) -> IssuedMaterialPreview:
        del tenant_id
        expired_seconds = math.ceil((expires_at - datetime.now(UTC)).total_seconds())
        if expired_seconds <= 0:
            raise ValueError("preview URLs must expire in the future")
        try:
            url = await asyncio.to_thread(
                self._client.get_presigned_url,
                Method="GET",
                Bucket=self._bucket,
                Key=object_key,
                Expired=expired_seconds,
            )
        except Exception as error:
            raise _translate_error(error, object_key=object_key) from error
        return IssuedMaterialPreview(url=url, expires_at=expires_at)


class TencentCosMaterialObjectCleaner:
    def __init__(self, *, bucket: str, client: CosObjectClient) -> None:
        self._bucket = bucket
        self._client = client

    async def delete_object(self, *, tenant_id: UUID, object_key: str) -> None:
        del tenant_id
        try:
            await asyncio.to_thread(
                self._client.delete_object, Bucket=self._bucket, Key=object_key
            )
        except Exception as error:
            translated = _translate_error(error, object_key=object_key)
            # 对象已不存在视为清理成功（幂等）。
            if isinstance(translated, MaterialObjectMissing):
                return
            raise translated from error
