from functools import partial
from io import BytesIO
from typing import Protocol

from anyio import to_thread
from minio.error import S3Error

BUCKET_CREATION_RACE_ERROR_CODES = {
    "BucketAlreadyExists",
    "BucketAlreadyOwnedByYou",
}


class ObjectResponse(Protocol):
    def read(self) -> bytes: ...

    def close(self) -> None: ...

    def release_conn(self) -> None: ...


class MinioClient(Protocol):
    def bucket_exists(self, bucket_name: str) -> bool: ...

    def make_bucket(self, bucket_name: str) -> None: ...

    def put_object(
        self,
        bucket_name: str,
        object_name: str,
        data: BytesIO,
        length: int,
        content_type: str,
    ) -> object: ...

    def get_object(self, bucket_name: str, object_name: str) -> ObjectResponse: ...

    def remove_object(self, bucket_name: str, object_name: str) -> None: ...


class MinioSkillStorage:
    def __init__(self, *, client: MinioClient, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    async def put(self, *, key: str, content: bytes) -> None:
        await to_thread.run_sync(self._put, key, content)

    async def get(self, *, key: str) -> bytes:
        return await to_thread.run_sync(self._get, key)

    async def delete(self, *, key: str) -> None:
        await to_thread.run_sync(
            partial(self._client.remove_object, self._bucket, key)
        )

    def _put(self, key: str, content: bytes) -> None:
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
            "application/zip",
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
