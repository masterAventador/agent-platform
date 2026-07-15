from functools import partial
from io import BytesIO
from typing import Protocol, cast

from anyio import to_thread
from minio.error import S3Error

from agent_platform.infrastructure.object_storage.minio import (
    BUCKET_CREATION_RACE_ERROR_CODES,
    MinioClient,
)


class MinioArtifactStorageProvider:
    def __init__(self, *, client: MinioClient, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    async def put(self, *, key: str, content: bytes, media_type: str) -> None:
        await to_thread.run_sync(self._put, key, content, media_type)

    async def get(self, *, key: str) -> bytes:
        return await to_thread.run_sync(self._get, key)

    async def delete(self, *, key: str) -> None:
        await to_thread.run_sync(partial(self._client.remove_object, self._bucket, key))

    def _put(self, key: str, content: bytes, media_type: str) -> None:
        if not self._client.bucket_exists(self._bucket):
            try:
                self._client.make_bucket(self._bucket)
            except S3Error as error:
                if error.code not in BUCKET_CREATION_RACE_ERROR_CODES:
                    raise
        self._client.put_object(
            self._bucket,
            key,
            BytesIO(content),
            len(content),
            media_type,
        )

    def _get(self, key: str) -> bytes:
        response = self._client.get_object(self._bucket, key)
        try:
            return response.read()
        finally:
            try:
                response.close()
            finally:
                response.release_conn()


class CosBody(Protocol):
    def read(self) -> bytes: ...

    def close(self) -> None: ...


class TencentCosClient(Protocol):
    def put_object(
        self, *, Bucket: str, Key: str, Body: bytes, ContentType: str
    ) -> object: ...

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, object]: ...

    def delete_object(self, *, Bucket: str, Key: str) -> object: ...


class TencentCosArtifactProvider:
    """兼容腾讯云 COS 与 LighthouseCOS 的标准对象 API。"""

    def __init__(self, *, client: TencentCosClient, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    async def put(self, *, key: str, content: bytes, media_type: str) -> None:
        await to_thread.run_sync(
            partial(
                self._client.put_object,
                Bucket=self._bucket,
                Key=key,
                Body=content,
                ContentType=media_type,
            )
        )

    async def get(self, *, key: str) -> bytes:
        response = await to_thread.run_sync(
            partial(self._client.get_object, Bucket=self._bucket, Key=key)
        )
        return await to_thread.run_sync(self._read_body, cast(CosBody, response["Body"]))

    async def delete(self, *, key: str) -> None:
        await to_thread.run_sync(
            partial(self._client.delete_object, Bucket=self._bucket, Key=key)
        )

    @staticmethod
    def _read_body(body: CosBody) -> bytes:
        try:
            return body.read()
        finally:
            body.close()
