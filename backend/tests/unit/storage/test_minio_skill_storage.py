from io import BytesIO

import pytest
from minio.error import S3Error

from agent_platform.infrastructure.object_storage.minio import MinioSkillStorage


class ObjectResponse:
    def __init__(self, content: bytes) -> None:
        self._content = content
        self.closed = False
        self.released = False

    def read(self) -> bytes:
        return self._content

    def close(self) -> None:
        self.closed = True

    def release_conn(self) -> None:
        self.released = True


class FakeMinioClient:
    def __init__(self) -> None:
        self.bucket_created = False
        self.objects: dict[str, bytes] = {}
        self.response: ObjectResponse | None = None

    def bucket_exists(self, bucket_name: str) -> bool:
        assert bucket_name == "agent-skills"
        return self.bucket_created

    def make_bucket(self, bucket_name: str) -> None:
        assert bucket_name == "agent-skills"
        self.bucket_created = True

    def put_object(
        self,
        bucket_name: str,
        object_name: str,
        data: BytesIO,
        length: int,
        content_type: str,
    ) -> None:
        assert bucket_name == "agent-skills"
        assert content_type == "application/zip"
        self.objects[object_name] = data.read(length)

    def get_object(self, bucket_name: str, object_name: str) -> ObjectResponse:
        assert bucket_name == "agent-skills"
        self.response = ObjectResponse(self.objects[object_name])
        return self.response

    def remove_object(self, bucket_name: str, object_name: str) -> None:
        assert bucket_name == "agent-skills"
        self.objects.pop(object_name, None)


class BucketCreationRaceClient(FakeMinioClient):
    def __init__(self, error_code: str) -> None:
        super().__init__()
        self._error_code = error_code

    def bucket_exists(self, bucket_name: str) -> bool:
        assert bucket_name == "agent-skills"
        return False

    def make_bucket(self, bucket_name: str) -> None:
        assert bucket_name == "agent-skills"
        raise S3Error(
            None,
            self._error_code,
            "bucket creation failed",
            bucket_name,
            "request-id",
            "host-id",
            bucket_name,
        )


class CloseFailureResponse(ObjectResponse):
    def close(self) -> None:
        self.closed = True
        raise RuntimeError("close failed")


class CloseFailureClient(FakeMinioClient):
    def get_object(self, bucket_name: str, object_name: str) -> ObjectResponse:
        assert bucket_name == "agent-skills"
        self.response = CloseFailureResponse(self.objects[object_name])
        return self.response


@pytest.mark.asyncio
async def test_minio_skill_storage_round_trip_and_cleanup() -> None:
    client = FakeMinioClient()
    storage = MinioSkillStorage(client=client, bucket="agent-skills")

    await storage.put(key="tenant/skill/1.zip", content=b"bundle")
    assert client.bucket_created is True
    assert await storage.get(key="tenant/skill/1.zip") == b"bundle"
    assert client.response is not None
    assert client.response.closed is True
    assert client.response.released is True

    await storage.delete(key="tenant/skill/1.zip")
    assert client.objects == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error_code",
    ["BucketAlreadyOwnedByYou", "BucketAlreadyExists"],
)
async def test_put_ignores_bucket_creation_race(error_code: str) -> None:
    client = BucketCreationRaceClient(error_code)
    storage = MinioSkillStorage(client=client, bucket="agent-skills")

    await storage.put(key="tenant/skill/1.zip", content=b"bundle")

    assert client.objects == {"tenant/skill/1.zip": b"bundle"}


@pytest.mark.asyncio
async def test_put_propagates_unexpected_bucket_creation_error() -> None:
    client = BucketCreationRaceClient("AccessDenied")
    storage = MinioSkillStorage(client=client, bucket="agent-skills")

    with pytest.raises(S3Error, match="AccessDenied"):
        await storage.put(key="tenant/skill/1.zip", content=b"bundle")


@pytest.mark.asyncio
async def test_get_releases_connection_when_close_fails() -> None:
    client = CloseFailureClient()
    client.objects["tenant/skill/1.zip"] = b"bundle"
    storage = MinioSkillStorage(client=client, bucket="agent-skills")

    with pytest.raises(RuntimeError, match="close failed"):
        await storage.get(key="tenant/skill/1.zip")

    assert client.response is not None
    assert client.response.closed is True
    assert client.response.released is True
