import os
from uuid import uuid4

import pytest
from qcloud_cos import CosServiceError

from agent_platform.config import AppSettings
from agent_platform.infrastructure.object_storage.artifacts import (
    create_artifact_storage_provider,
)


@pytest.mark.asyncio
async def test_real_tencent_cos_round_trip_and_delete() -> None:
    required = {
        "TEST_COS_REGION": os.getenv("TEST_COS_REGION"),
        "TEST_COS_SECRET_ID": os.getenv("TEST_COS_SECRET_ID"),
        "TEST_COS_SECRET_KEY": os.getenv("TEST_COS_SECRET_KEY"),
        "TEST_COS_BUCKET": os.getenv("TEST_COS_BUCKET"),
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        pytest.skip(f"需要 {', '.join(missing)} 才运行真实腾讯云 COS 门禁")

    storage = create_artifact_storage_provider(
        settings=AppSettings(
            artifact_storage_provider="tencent-cos",
            artifact_storage_bucket=required["TEST_COS_BUCKET"],
            cos_region=required["TEST_COS_REGION"],
            cos_secret_id=required["TEST_COS_SECRET_ID"],
            cos_secret_key=required["TEST_COS_SECRET_KEY"],
            cos_token=os.getenv("TEST_COS_TOKEN", ""),
        )
    )
    key = f"codex-c04-acceptance/{uuid4()}/result.txt"
    content = "真实腾讯云 COS 任务产物".encode()

    try:
        await storage.put(key=key, content=content, media_type="text/plain")
        assert await storage.get(key=key) == content
        await storage.delete(key=key)
        with pytest.raises(CosServiceError):
            await storage.get(key=key)
    finally:
        await storage.delete(key=key)
