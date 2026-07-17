"""生产腾讯 COS 对象核验/预签名预览/清理 Provider（M-3 + A: crc64 硬化）。

核验可信值 = 服务端计算的 `x-cos-hash-crc64ecma` + 服务端可信 Content-Length；
`x-cos-meta-sha256` 是客户端自定义元数据、可伪造，仅作展示不作安全门禁。真实网络
行为由 TEST_COS_* 门禁覆盖，本文件用注入的 COS 客户端验证参数、失败语义与
crc64/元数据缺失的失败关闭。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest

from agent_platform.capabilities.video_studio.storage_credentials import (
    MaterialObjectMissing,
    MaterialStorageUnavailable,
)
from agent_platform.capabilities.video_studio.tencent_cos import (
    TencentCosMaterialObjectCleaner,
    TencentCosMaterialObjectVerifier,
    TencentCosMaterialPreviewUrlIssuer,
)


class FakeCosServiceError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"cos error {status_code}")
        self._status_code = status_code

    def get_status_code(self) -> int:
        return self._status_code


class FakeCosClient:
    def __init__(self) -> None:
        self.objects: dict[str, dict[str, Any]] = {}
        self.head_calls: list[tuple[str, str]] = []
        self.deleted: list[tuple[str, str]] = []
        self.presign_calls: list[dict[str, Any]] = []
        self.fail_with: Exception | None = None

    def head_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        self.head_calls.append((Bucket, Key))
        if self.fail_with is not None:
            raise self.fail_with
        try:
            return self.objects[Key]
        except KeyError:
            raise FakeCosServiceError(404) from None

    def delete_object(self, *, Bucket: str, Key: str) -> None:
        if self.fail_with is not None:
            raise self.fail_with
        self.deleted.append((Bucket, Key))
        self.objects.pop(Key, None)

    def get_presigned_url(
        self, *, Method: str, Bucket: str, Key: str, Expired: int
    ) -> str:
        self.presign_calls.append(
            {"method": Method, "bucket": Bucket, "key": Key, "expired": Expired}
        )
        return f"https://{Bucket}.cos.example/{Key}?sign=test&expires={Expired}"


def _providers(
    client: FakeCosClient,
) -> tuple[
    TencentCosMaterialObjectVerifier,
    TencentCosMaterialPreviewUrlIssuer,
    TencentCosMaterialObjectCleaner,
]:
    return (
        TencentCosMaterialObjectVerifier(bucket="agent-platform-1424480216", client=client),
        TencentCosMaterialPreviewUrlIssuer(bucket="agent-platform-1424480216", client=client),
        TencentCosMaterialObjectCleaner(bucket="agent-platform-1424480216", client=client),
    )


@pytest.mark.asyncio
async def test_verifier_reads_trusted_size_and_server_crc64() -> None:
    client = FakeCosClient()
    key = f"materials/{uuid4()}/{uuid4()}/clip.mp4"
    client.objects[key] = {
        "Content-Length": "1000",
        "ETag": '"not-a-sha"',
        "x-cos-meta-sha256": "a" * 64,
        "x-cos-hash-crc64ecma": "11051210869376104954",
    }
    verifier, _, _ = _providers(client)

    stored = await verifier.inspect_uploaded_object(tenant_id=uuid4(), object_key=key)

    assert stored.size_bytes == 1000
    # 服务端可信内容指纹。
    assert stored.crc64ecma == "11051210869376104954"
    # sha256 仍读出用于展示，但不是安全门禁。
    assert stored.sha256 == "a" * 64
    assert client.head_calls == [("agent-platform-1424480216", key)]


@pytest.mark.asyncio
async def test_verifier_fails_closed_on_missing_object_or_missing_metadata() -> None:
    client = FakeCosClient()
    verifier, _, _ = _providers(client)

    with pytest.raises(MaterialObjectMissing):
        await verifier.inspect_uploaded_object(
            tenant_id=uuid4(),
            object_key=f"materials/{uuid4()}/{uuid4()}/absent.mp4",
        )

    # 缺少 crc64 的对象不可核验：返回空 crc64，交由服务层按元数据不一致失败关闭。
    key = f"materials/{uuid4()}/{uuid4()}/no-meta.mp4"
    client.objects[key] = {"Content-Length": "1000"}
    stored = await verifier.inspect_uploaded_object(tenant_id=uuid4(), object_key=key)
    assert stored.crc64ecma == ""
    assert stored.sha256 == ""

    # 非 404 的上游故障必须区分为存储不可用，不得误标 upload_failed。
    client.fail_with = FakeCosServiceError(503)
    with pytest.raises(MaterialStorageUnavailable):
        await verifier.inspect_uploaded_object(tenant_id=uuid4(), object_key=key)


@pytest.mark.asyncio
async def test_preview_issuer_signs_short_lived_get_url() -> None:
    client = FakeCosClient()
    _, preview_issuer, _ = _providers(client)
    key = f"materials/{uuid4()}/{uuid4()}/clip.mp4"
    expires_at = datetime.now(UTC) + timedelta(minutes=5)

    preview = await preview_issuer.issue_preview_url(
        tenant_id=uuid4(),
        object_key=key,
        expires_at=expires_at,
    )

    assert preview.url.startswith("https://")
    assert preview.expires_at == expires_at
    (call,) = client.presign_calls
    assert call["method"] == "GET"
    assert call["key"] == key
    assert 0 < call["expired"] <= 300

    with pytest.raises(ValueError):
        await preview_issuer.issue_preview_url(
            tenant_id=uuid4(),
            object_key=key,
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )


@pytest.mark.asyncio
async def test_cleaner_deletes_object_and_translates_upstream_failure() -> None:
    client = FakeCosClient()
    _, _, cleaner = _providers(client)
    key = f"materials/{uuid4()}/{uuid4()}/clip.mp4"
    client.objects[key] = {"Content-Length": "1"}

    await cleaner.delete_object(tenant_id=uuid4(), object_key=key)
    assert client.deleted == [("agent-platform-1424480216", key)]

    client.fail_with = FakeCosServiceError(503)
    with pytest.raises(MaterialStorageUnavailable):
        await cleaner.delete_object(tenant_id=uuid4(), object_key=key)
