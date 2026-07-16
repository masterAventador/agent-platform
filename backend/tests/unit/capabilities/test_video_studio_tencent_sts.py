"""生产腾讯 CAM/STS 素材上传凭证签发 Provider 的策略与失败语义。

真实网络签发由 TEST_COS_* 门禁测试覆盖；本文件用注入的 fetcher
验证策略构造（限定目录前缀、短有效期、显式动作集）与失败关闭语义，
并保证与测试 Provider（Mock）的契约一致：越界前缀、空动作集一律拒绝。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest

from agent_platform.capabilities.video_studio.media_library import MATERIAL_UPLOAD_ACTIONS
from agent_platform.capabilities.video_studio.storage_credentials import (
    MaterialStorageCredentialsUnavailable,
)
from agent_platform.capabilities.video_studio.tencent_sts import (
    MAX_STS_DURATION_SECONDS,
    TencentStsMaterialUploadCredentialIssuer,
)


def _payload() -> dict[str, Any]:
    return {
        "credentials": {
            "tmpSecretId": "real-tmp-id",
            "tmpSecretKey": "real-tmp-key",
            "sessionToken": "real-session-token",
        },
        "expiredTime": int(datetime.now(UTC).timestamp()) + 900,
    }


class RecordingFetcher:
    def __init__(self, payload: dict[str, Any] | None = None) -> None:
        self.configs: list[dict[str, Any]] = []
        self.payload = payload or _payload()

    def __call__(self, config: dict[str, Any]) -> dict[str, Any]:
        self.configs.append(config)
        return self.payload


def _issuer(fetcher: Any) -> TencentStsMaterialUploadCredentialIssuer:
    return TencentStsMaterialUploadCredentialIssuer(
        secret_id="test-secret-id",
        secret_key="test-secret-key",
        bucket="agent-platform-1424480216",
        region="ap-beijing",
        fetch_credentials=fetcher,
    )


@pytest.mark.asyncio
async def test_issues_prefix_scoped_short_lived_write_only_credentials() -> None:
    fetcher = RecordingFetcher()
    issuer = _issuer(fetcher)
    tenant_id = uuid4()
    key_prefix = f"materials/{tenant_id}/{uuid4()}/"
    expires_at = datetime.now(UTC) + timedelta(minutes=15)

    credentials = await issuer.issue_upload_credentials(
        tenant_id=tenant_id,
        key_prefix=key_prefix,
        expires_at=expires_at,
        allowed_actions=MATERIAL_UPLOAD_ACTIONS,
    )

    assert credentials.provider == "tencent-cos"
    assert credentials.bucket == "agent-platform-1424480216"
    assert credentials.region == "ap-beijing"
    assert credentials.key_prefix == key_prefix
    assert credentials.tmp_secret_id == "real-tmp-id"
    assert credentials.tmp_secret_key == "real-tmp-key"
    assert credentials.session_token == "real-session-token"
    assert credentials.expires_at.tzinfo is not None

    (config,) = fetcher.configs
    assert config["bucket"] == "agent-platform-1424480216"
    assert config["region"] == "ap-beijing"
    # 策略限定：只允许该素材目录前缀，动作集必须显式传入、不得扩权。
    assert config["allow_prefix"] == [f"{key_prefix}*"]
    assert config["allow_actions"] == list(MATERIAL_UPLOAD_ACTIONS)
    assert 0 < config["duration_seconds"] <= MAX_STS_DURATION_SECONDS


@pytest.mark.asyncio
async def test_rejects_prefix_escaping_tenant_material_scope() -> None:
    issuer = _issuer(RecordingFetcher())
    tenant_id = uuid4()

    for bad_prefix in (
        f"materials/{uuid4()}/{uuid4()}/",  # 其他租户
        "materials/",  # 越界到全部素材
        f"artifacts/{tenant_id}/",  # 其他资源根
        f"materials/{tenant_id}/../",  # 路径穿越
    ):
        with pytest.raises(ValueError):
            await issuer.issue_upload_credentials(
                tenant_id=tenant_id,
                key_prefix=bad_prefix,
                expires_at=datetime.now(UTC) + timedelta(minutes=5),
                allowed_actions=MATERIAL_UPLOAD_ACTIONS,
            )


@pytest.mark.asyncio
async def test_rejects_empty_actions_and_non_positive_or_overlong_ttl() -> None:
    issuer = _issuer(RecordingFetcher())
    tenant_id = uuid4()
    key_prefix = f"materials/{tenant_id}/{uuid4()}/"

    with pytest.raises(ValueError):
        await issuer.issue_upload_credentials(
            tenant_id=tenant_id,
            key_prefix=key_prefix,
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
            allowed_actions=(),
        )
    with pytest.raises(ValueError):
        await issuer.issue_upload_credentials(
            tenant_id=tenant_id,
            key_prefix=key_prefix,
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
            allowed_actions=MATERIAL_UPLOAD_ACTIONS,
        )
    with pytest.raises(ValueError):
        await issuer.issue_upload_credentials(
            tenant_id=tenant_id,
            key_prefix=key_prefix,
            expires_at=datetime.now(UTC) + timedelta(hours=2),
            allowed_actions=MATERIAL_UPLOAD_ACTIONS,
        )


@pytest.mark.asyncio
async def test_upstream_failure_and_malformed_response_fail_closed() -> None:
    tenant_id = uuid4()
    key_prefix = f"materials/{tenant_id}/{uuid4()}/"
    expires_at = datetime.now(UTC) + timedelta(minutes=5)

    def broken_fetch(config: dict[str, Any]) -> dict[str, Any]:
        raise OSError("sts endpoint unreachable")

    with pytest.raises(MaterialStorageCredentialsUnavailable):
        await _issuer(broken_fetch).issue_upload_credentials(
            tenant_id=tenant_id,
            key_prefix=key_prefix,
            expires_at=expires_at,
            allowed_actions=MATERIAL_UPLOAD_ACTIONS,
        )

    malformed = RecordingFetcher(payload={"credentials": {}})
    with pytest.raises(MaterialStorageCredentialsUnavailable):
        await _issuer(malformed).issue_upload_credentials(
            tenant_id=tenant_id,
            key_prefix=key_prefix,
            expires_at=expires_at,
            allowed_actions=MATERIAL_UPLOAD_ACTIONS,
        )
