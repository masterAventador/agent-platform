import os
from functools import partial
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from anyio import to_thread
from minio import Minio
from minio.error import S3Error

from agent_platform.infrastructure.object_storage.artifacts import (
    MinioArtifactStorageProvider,
)
from agent_platform.infrastructure.object_storage.minio import MinioSkillStorage


def _real_minio_client() -> Minio:
    environment = {
        "TEST_MINIO_ENDPOINT": os.getenv("TEST_MINIO_ENDPOINT"),
        "TEST_MINIO_ACCESS_KEY": os.getenv("TEST_MINIO_ACCESS_KEY"),
        "TEST_MINIO_SECRET_KEY": os.getenv("TEST_MINIO_SECRET_KEY"),
    }
    missing = [name for name, value in environment.items() if value is None]
    if missing:
        pytest.skip(f"需要 {', '.join(missing)} 才运行真实 MinIO 集成测试")

    endpoint = environment["TEST_MINIO_ENDPOINT"]
    assert endpoint is not None
    secure = False
    if "://" in endpoint:
        parsed_endpoint = urlsplit(endpoint)
        if parsed_endpoint.scheme not in {"http", "https"} or not parsed_endpoint.netloc:
            pytest.fail("TEST_MINIO_ENDPOINT 必须是 host:port 或有效的 HTTP(S) URL")
        endpoint = parsed_endpoint.netloc
        secure = parsed_endpoint.scheme == "https"

    access_key = environment["TEST_MINIO_ACCESS_KEY"]
    secret_key = environment["TEST_MINIO_SECRET_KEY"]
    assert access_key is not None
    assert secret_key is not None
    return Minio(
        endpoint,
        access_key=access_key,
        secret_key=secret_key,
        secure=secure,
    )


@pytest.mark.asyncio
async def test_real_minio_round_trip_delete_and_automatic_bucket_creation() -> None:
    client = _real_minio_client()
    bucket = f"test-skill-storage-{uuid4().hex}"
    object_name = f"integration/{uuid4().hex}.zip"
    content = b"real-minio-skill-bundle"
    storage = MinioSkillStorage(client=client, bucket=bucket)

    try:
        assert await to_thread.run_sync(client.bucket_exists, bucket) is False

        await storage.put(key=object_name, content=content)

        assert await to_thread.run_sync(client.bucket_exists, bucket) is True
        assert await storage.get(key=object_name) == content

        await storage.delete(key=object_name)

        with pytest.raises(S3Error, match="NoSuchKey"):
            await storage.get(key=object_name)
    finally:
        if await to_thread.run_sync(client.bucket_exists, bucket):
            await to_thread.run_sync(
                partial(client.remove_object, bucket, object_name)
            )
            await to_thread.run_sync(client.remove_bucket, bucket)


@pytest.mark.asyncio
async def test_real_minio_artifact_round_trip_preserves_bytes_and_deletes_object() -> None:
    client = _real_minio_client()
    bucket = f"test-artifact-storage-{uuid4().hex}"
    object_name = f"tenants/{uuid4()}/runs/{uuid4()}/artifacts/result.txt"
    content = "真实 MinIO 任务产物".encode()
    storage = MinioArtifactStorageProvider(client=client, bucket=bucket)

    try:
        await storage.put(
            key=object_name,
            content=content,
            media_type="text/plain; charset=utf-8",
        )

        assert await storage.get(key=object_name) == content

        await storage.delete(key=object_name)

        with pytest.raises(S3Error, match="NoSuchKey"):
            await storage.get(key=object_name)
    finally:
        if await to_thread.run_sync(client.bucket_exists, bucket):
            await to_thread.run_sync(partial(client.remove_object, bucket, object_name))
            await to_thread.run_sync(client.remove_bucket, bucket)
