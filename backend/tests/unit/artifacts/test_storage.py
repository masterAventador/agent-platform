from io import BytesIO

import pytest

from agent_platform.config import AppSettings
from agent_platform.infrastructure.object_storage.artifacts import (
    MinioArtifactStorageProvider,
    TencentCosArtifactProvider,
    create_artifact_storage_provider,
)


class MinioResponse:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.closed = False
        self.released = False

    def read(self) -> bytes:
        return self.content

    def close(self) -> None:
        self.closed = True

    def release_conn(self) -> None:
        self.released = True


class FakeMinio:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], tuple[bytes, str]] = {}
        self.buckets: set[str] = set()
        self.last_response: MinioResponse | None = None

    def bucket_exists(self, bucket_name: str) -> bool:
        return bucket_name in self.buckets

    def make_bucket(self, bucket_name: str) -> None:
        self.buckets.add(bucket_name)

    def put_object(
        self,
        bucket_name: str,
        object_name: str,
        data: BytesIO,
        length: int,
        content_type: str,
    ) -> object:
        self.objects[(bucket_name, object_name)] = (data.read(length), content_type)
        return object()

    def get_object(self, bucket_name: str, object_name: str) -> MinioResponse:
        self.last_response = MinioResponse(self.objects[(bucket_name, object_name)][0])
        return self.last_response

    def remove_object(self, bucket_name: str, object_name: str) -> None:
        self.objects.pop((bucket_name, object_name), None)


@pytest.mark.asyncio
async def test_minio_provider_round_trip_and_response_cleanup() -> None:
    client = FakeMinio()
    provider = MinioArtifactStorageProvider(client=client, bucket="artifacts")

    await provider.put(key="tenant/file", content=b"hello", media_type="text/plain")
    assert await provider.get(key="tenant/file") == b"hello"
    assert client.last_response is not None
    assert client.last_response.closed and client.last_response.released
    await provider.delete(key="tenant/file")
    assert client.objects == {}


class FakeCos:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.body: BytesIO | None = None

    def put_object(self, *, Bucket: str, Key: str, Body: bytes, ContentType: str) -> object:
        del ContentType
        self.objects[(Bucket, Key)] = Body
        return object()

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, object]:
        self.body = BytesIO(self.objects[(Bucket, Key)])
        return {"Body": self.body}

    def delete_object(self, *, Bucket: str, Key: str) -> object:
        self.objects.pop((Bucket, Key), None)
        return object()


@pytest.mark.asyncio
async def test_tencent_cos_provider_is_lighthouse_cos_compatible() -> None:
    client = FakeCos()
    provider = TencentCosArtifactProvider(client=client, bucket="demo-1250000000")

    await provider.put(key="tenant/artifact", content=b"result", media_type="text/plain")
    assert await provider.get(key="tenant/artifact") == b"result"
    assert client.body is not None and client.body.closed
    await provider.delete(key="tenant/artifact")
    assert client.objects == {}


def test_storage_factory_builds_tencent_cos_from_production_settings() -> None:
    client = FakeCos()
    captured: dict[str, object] = {}

    def create_client(**kwargs: object) -> FakeCos:
        captured.update(kwargs)
        return client

    provider = create_artifact_storage_provider(
        settings=AppSettings(
            artifact_storage_provider="tencent-cos",
            artifact_storage_bucket="demo-1250000000",
            cos_region="ap-beijing",
            cos_secret_id="secret-id",
            cos_secret_key="secret-key",
        ),
        cos_client_factory=create_client,
    )

    assert isinstance(provider, TencentCosArtifactProvider)
    assert captured == {
        "region": "ap-beijing",
        "secret_id": "secret-id",
        "secret_key": "secret-key",
        "token": None,
        "scheme": "https",
    }
