"""真实腾讯 COS 素材对象核验 / 预签名预览 / 清理门禁（B04 M-3）。

需要 TEST_COS_* 环境变量；缺失时跳过。闭环：带 `x-cos-meta-sha256`
元数据真实上传 → 核验 size/sha → 预签名 GET 拉回内容 → 删除 → 云端确认不存在。
"""

from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest

from agent_platform.capabilities.video_studio.storage_credentials import (
    MaterialObjectMissing,
)
from agent_platform.capabilities.video_studio.tencent_cos import (
    SHA256_METADATA_HEADER,
    TencentCosMaterialObjectCleaner,
    TencentCosMaterialObjectVerifier,
    TencentCosMaterialPreviewUrlIssuer,
    create_cos_client,
)


@pytest.mark.asyncio
async def test_real_cos_material_object_round_trip_preview_and_cleanup() -> None:
    required = {
        "TEST_COS_REGION": os.getenv("TEST_COS_REGION"),
        "TEST_COS_SECRET_ID": os.getenv("TEST_COS_SECRET_ID"),
        "TEST_COS_SECRET_KEY": os.getenv("TEST_COS_SECRET_KEY"),
        "TEST_COS_BUCKET": os.getenv("TEST_COS_BUCKET"),
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        pytest.skip(f"需要 {', '.join(missing)} 才运行真实腾讯云 COS 对象门禁")

    bucket = required["TEST_COS_BUCKET"]
    assert bucket is not None
    client = create_cos_client(
        region=required["TEST_COS_REGION"] or "",
        secret_id=required["TEST_COS_SECRET_ID"] or "",
        secret_key=required["TEST_COS_SECRET_KEY"] or "",
    )
    verifier = TencentCosMaterialObjectVerifier(bucket=bucket, client=client)
    preview_issuer = TencentCosMaterialPreviewUrlIssuer(bucket=bucket, client=client)
    cleaner = TencentCosMaterialObjectCleaner(bucket=bucket, client=client)

    tenant_id = uuid4()
    object_key = f"materials/{tenant_id}/{uuid4()}/b04-m3-gate.mp4"
    content = "B04 M-3 真实对象核验/预览/清理门禁".encode()
    sha256 = hashlib.sha256(content).hexdigest()

    try:
        # 与前端直传一致：写入 x-cos-meta-sha256 自定义元数据。
        client.put_object(  # type: ignore[attr-defined]
            Bucket=bucket,
            Key=object_key,
            Body=content,
            Metadata={SHA256_METADATA_HEADER: sha256},
        )

        stored = await verifier.inspect_uploaded_object(
            tenant_id=tenant_id, object_key=object_key
        )
        assert stored.size_bytes == len(content)
        assert stored.sha256 == sha256

        preview = await preview_issuer.issue_preview_url(
            tenant_id=tenant_id,
            object_key=object_key,
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
        async with httpx.AsyncClient() as http:
            fetched = await http.get(preview.url)
        assert fetched.status_code == 200
        assert fetched.content == content

        await cleaner.delete_object(tenant_id=tenant_id, object_key=object_key)
        with pytest.raises(MaterialObjectMissing):
            await verifier.inspect_uploaded_object(
                tenant_id=tenant_id, object_key=object_key
            )
        # 清理幂等：对已删除对象再次清理不报错。
        await cleaner.delete_object(tenant_id=tenant_id, object_key=object_key)
    finally:
        client.delete_object(Bucket=bucket, Key=object_key)  # type: ignore[attr-defined]
