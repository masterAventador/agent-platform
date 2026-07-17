"""真实腾讯 CAM/STS 素材上传凭证门禁（B04）。

需要 TEST_COS_REGION / TEST_COS_SECRET_ID / TEST_COS_SECRET_KEY / TEST_COS_BUCKET
环境变量（infra/compose/.env.platform 提供开发凭据）；缺失时跳过。

断言真实签发的临时凭证：
- 只能写入限定的素材目录前缀；
- 越界前缀写入被 COS 拒绝；
- 动作集为只写（用同一凭证读取对象被拒绝）。
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from qcloud_cos import CosConfig, CosS3Client, CosServiceError

from agent_platform.capabilities.video_studio.media_library import MATERIAL_UPLOAD_ACTIONS
from agent_platform.capabilities.video_studio.tencent_sts import (
    TencentStsMaterialUploadCredentialIssuer,
)


@pytest.mark.asyncio
async def test_real_sts_credentials_are_prefix_scoped_and_write_only() -> None:
    required = {
        "TEST_COS_REGION": os.getenv("TEST_COS_REGION"),
        "TEST_COS_SECRET_ID": os.getenv("TEST_COS_SECRET_ID"),
        "TEST_COS_SECRET_KEY": os.getenv("TEST_COS_SECRET_KEY"),
        "TEST_COS_BUCKET": os.getenv("TEST_COS_BUCKET"),
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        pytest.skip(f"需要 {', '.join(missing)} 才运行真实腾讯云 STS 门禁")

    region = required["TEST_COS_REGION"]
    bucket = required["TEST_COS_BUCKET"]
    assert region is not None and bucket is not None

    issuer = TencentStsMaterialUploadCredentialIssuer(
        secret_id=required["TEST_COS_SECRET_ID"] or "",
        secret_key=required["TEST_COS_SECRET_KEY"] or "",
        bucket=bucket,
        region=region,
    )

    tenant_id = uuid4()
    material_id = uuid4()
    key_prefix = f"materials/{tenant_id}/{material_id}/"
    credentials = await issuer.issue_upload_credentials(
        tenant_id=tenant_id,
        key_prefix=key_prefix,
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
        allowed_actions=MATERIAL_UPLOAD_ACTIONS,
    )
    assert credentials.provider == "tencent-cos"
    assert credentials.bucket == bucket
    assert credentials.key_prefix == key_prefix
    assert credentials.session_token

    scoped_client = CosS3Client(
        CosConfig(
            Region=region,
            SecretId=credentials.tmp_secret_id,
            SecretKey=credentials.tmp_secret_key,
            Token=credentials.session_token,
            Scheme="https",
        )
    )
    in_scope_key = f"{key_prefix}material.mp4"
    out_of_scope_key = f"materials/{uuid4()}/{uuid4()}/escape.mp4"
    content = "B04 真实 STS 门禁上传内容".encode()

    try:
        # 限定前缀内可写。
        scoped_client.put_object(Bucket=bucket, Key=in_scope_key, Body=content)

        # 越界前缀被拒绝。
        with pytest.raises(CosServiceError) as escape_error:
            scoped_client.put_object(Bucket=bucket, Key=out_of_scope_key, Body=b"escape")
        assert escape_error.value.get_status_code() == 403

        # 动作集只写：同一临时凭证读取对象（下载）被拒绝。
        with pytest.raises(CosServiceError) as read_error:
            scoped_client.get_object(Bucket=bucket, Key=in_scope_key)
        assert read_error.value.get_status_code() == 403
    finally:
        cleanup_client = CosS3Client(
            CosConfig(
                Region=region,
                SecretId=required["TEST_COS_SECRET_ID"],
                SecretKey=required["TEST_COS_SECRET_KEY"],
                Scheme="https",
            )
        )
        cleanup_client.delete_object(Bucket=bucket, Key=in_scope_key)
        cleanup_client.delete_object(Bucket=bucket, Key=out_of_scope_key)
