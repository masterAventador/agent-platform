from collections.abc import Callable
from functools import partial
from io import BytesIO
from typing import Protocol, cast

from anyio import to_thread
from minio import Minio
from minio.error import S3Error
from urllib3 import PoolManager, Timeout

from agent_platform.config import AppSettings
from agent_platform.infrastructure.object_storage.minio import (
    BUCKET_CREATION_RACE_ERROR_CODES,
    MinioClient,
)
from agent_platform.platform.artifacts.ports import ArtifactStorageProvider


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
    def put_object(self, *, Bucket: str, Key: str, Body: bytes, ContentType: str) -> object: ...

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
        await to_thread.run_sync(partial(self._client.delete_object, Bucket=self._bucket, Key=key))

    @staticmethod
    def _read_body(body: CosBody) -> bytes:
        try:
            return body.read()
        finally:
            body.close()


CosClientFactory = Callable[..., TencentCosClient]


def create_bounded_minio_client(settings: AppSettings) -> Minio:
    return Minio(
        settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=settings.minio_secure,
        http_client=PoolManager(
            # A lazy first put can issue bucket_exists, make_bucket and put_object.
            # Bound each HTTP request to one third of the service operation deadline.
            timeout=Timeout(
                total=settings.artifact_storage_request_timeout_seconds / 3,
            ),
            retries=False,
        ),
    )


def _create_tencent_cos_client(
    *,
    region: str,
    secret_id: str,
    secret_key: str,
    token: str | None,
    scheme: str,
    request_timeout: float,
) -> TencentCosClient:
    from qcloud_cos import CosConfig, CosS3Client  # type: ignore[import-untyped]

    config = CosConfig(
        Region=region,
        SecretId=secret_id,
        SecretKey=secret_key,
        Token=token,
        Scheme=scheme,
        Timeout=request_timeout,
    )
    return cast(TencentCosClient, CosS3Client(config, retry=0))


def create_artifact_storage_provider(
    *,
    settings: AppSettings,
    minio_client: MinioClient | None = None,
    cos_client_factory: CosClientFactory | None = None,
) -> ArtifactStorageProvider:
    if settings.artifact_storage_provider == "minio":
        minio_storage_client = minio_client or cast(
            MinioClient,
            create_bounded_minio_client(settings),
        )
        return MinioArtifactStorageProvider(
            client=minio_storage_client,
            bucket=settings.artifact_storage_bucket,
        )

    factory = cos_client_factory or _create_tencent_cos_client
    cos_client = factory(
        region=cast(str, settings.cos_region),
        secret_id=settings.cos_secret_id.get_secret_value(),
        secret_key=settings.cos_secret_key.get_secret_value(),
        token=settings.cos_token.get_secret_value() or None,
        scheme=settings.cos_scheme,
        request_timeout=settings.artifact_storage_request_timeout_seconds,
    )
    return TencentCosArtifactProvider(
        client=cos_client,
        bucket=settings.artifact_storage_bucket,
    )
