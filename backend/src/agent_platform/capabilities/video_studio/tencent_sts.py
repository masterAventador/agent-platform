"""生产腾讯 CAM/STS 素材上传临时凭证签发 Provider。

通过官方 ``qcloud-python-sts`` SDK 调用 GetFederationToken，策略严格限定：

- 资源只允许 ``materials/{tenant_id}/{material_id}/`` 目录前缀；
- 动作集由调用方显式传入（生产路径固定为素材直传写动作集，不含读）；
- 有效期为短时（不超过 15 分钟），与素材上传凭证 TTL 上限一致。

SDK 只在默认 fetcher 内部使用（零侵入，版本经 pyproject.toml 锁定）；
上游失败与畸形响应统一转换为 ``MaterialStorageCredentialsUnavailable``。
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from agent_platform.capabilities.video_studio.storage_credentials import (
    IssuedUploadCredentials,
    MaterialStorageCredentialsUnavailable,
)

MAX_STS_DURATION_SECONDS = 900

StsCredentialFetcher = Callable[[dict[str, Any]], dict[str, Any]]


def _default_fetch_credentials(config: dict[str, Any]) -> dict[str, Any]:
    from sts.sts import Sts  # type: ignore[import-untyped]

    return Sts(config).get_credential()  # type: ignore[no-any-return]


class TencentStsMaterialUploadCredentialIssuer:
    def __init__(
        self,
        *,
        secret_id: str,
        secret_key: str,
        bucket: str,
        region: str,
        fetch_credentials: StsCredentialFetcher | None = None,
    ) -> None:
        if not secret_id or not secret_key:
            raise ValueError("Tencent STS credentials are required")
        if not bucket or not region:
            raise ValueError("Tencent STS bucket and region are required")
        self._secret_id = secret_id
        self._secret_key = secret_key
        self._bucket = bucket
        self._region = region
        self._fetch_credentials = fetch_credentials or _default_fetch_credentials

    async def issue_upload_credentials(
        self,
        *,
        tenant_id: UUID,
        key_prefix: str,
        expires_at: datetime,
        allowed_actions: tuple[str, ...],
    ) -> IssuedUploadCredentials:
        expected_prefix = f"materials/{tenant_id}/"
        if not key_prefix.startswith(expected_prefix) or ".." in key_prefix:
            raise ValueError("STS key prefix escaped the tenant material scope")
        if not allowed_actions:
            raise ValueError("STS credentials require an explicit action set")
        now = datetime.now(UTC)
        duration_seconds = math.ceil((expires_at - now).total_seconds())
        if duration_seconds <= 0:
            raise ValueError("STS credentials must expire in the future")
        if duration_seconds > MAX_STS_DURATION_SECONDS:
            raise ValueError("material upload credentials must be short lived")

        config: dict[str, Any] = {
            "secret_id": self._secret_id,
            "secret_key": self._secret_key,
            "bucket": self._bucket,
            "region": self._region,
            "duration_seconds": duration_seconds,
            "allow_prefix": [f"{key_prefix}*"],
            "allow_actions": list(allowed_actions),
        }
        try:
            payload = await asyncio.to_thread(self._fetch_credentials, config)
            credentials = payload["credentials"]
            issued = IssuedUploadCredentials(
                provider="tencent-cos",
                bucket=self._bucket,
                region=self._region,
                key_prefix=key_prefix,
                tmp_secret_id=credentials["tmpSecretId"],
                tmp_secret_key=credentials["tmpSecretKey"],
                session_token=credentials["sessionToken"],
                expires_at=datetime.fromtimestamp(int(payload["expiredTime"]), tz=UTC),
            )
        except Exception as error:
            raise MaterialStorageCredentialsUnavailable(
                "腾讯云 STS 临时凭证签发失败"
            ) from error
        if not issued.tmp_secret_id or not issued.tmp_secret_key or not issued.session_token:
            raise MaterialStorageCredentialsUnavailable("腾讯云 STS 返回的临时凭证不完整")
        return issued
