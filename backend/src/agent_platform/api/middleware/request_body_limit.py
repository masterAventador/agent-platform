import json
from collections.abc import Awaitable, Callable
from typing import Any

from agent_platform.platform.artifacts.entities import MAX_FILE_SIZE_BYTES

MAX_FILE_UPLOAD_REQUEST_BYTES = MAX_FILE_SIZE_BYTES + 64 * 1024

AsgiReceive = Callable[[], Awaitable[dict[str, Any]]]
AsgiSend = Callable[[dict[str, Any]], Awaitable[None]]


class RequestBodyTooLargeError(Exception):
    """Raised when a streamed upload exceeds the configured request limit."""


class FileUploadRequestBodyLimitMiddleware:
    """Bound the upload body before Starlette's multipart parser sees any bytes."""

    def __init__(self, app: Any, *, max_bytes: int = MAX_FILE_UPLOAD_REQUEST_BYTES) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: dict[str, Any], receive: AsgiReceive, send: AsgiSend) -> None:
        if not self._is_file_upload(scope):
            await self.app(scope, receive, send)
            return
        content_lengths = [
            value for key, value in scope.get("headers", []) if key.lower() == b"content-length"
        ]
        if not content_lengths:
            await self._too_large(send)
            return
        if len(content_lengths) != 1:
            await self._invalid_content_length(send)
            return
        try:
            content_length = int(content_lengths[0])
        except ValueError:
            await self._invalid_content_length(send)
            return
        if content_length < 0:
            await self._invalid_content_length(send)
            return
        if content_length > self.max_bytes:
            await self._too_large(send)
            return
        minimum_body_bytes = self._minimum_multipart_body_bytes(scope)
        if minimum_body_bytes is not None and content_length < minimum_body_bytes:
            await self._too_large(send)
            return

        received = 0

        async def bounded_receive() -> dict[str, Any]:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > content_length or received > self.max_bytes:
                    raise RequestBodyTooLargeError
            return message

        response_started = False

        async def tracking_send(message: dict[str, Any]) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, bounded_receive, tracking_send)
        except RequestBodyTooLargeError:
            if response_started:
                raise
            await self._too_large(send)

    @staticmethod
    def _is_file_upload(scope: dict[str, Any]) -> bool:
        return (
            scope.get("type") == "http"
            and scope.get("method") == "POST"
            and scope.get("path") == "/api/v1/files"
        )

    @staticmethod
    def _minimum_multipart_body_bytes(scope: dict[str, Any]) -> int | None:
        content_type = next(
            (value for key, value in scope.get("headers", []) if key.lower() == b"content-type"),
            None,
        )
        if content_type is None or not content_type.lower().startswith(b"multipart/form-data"):
            return None
        for part in content_type.split(b";")[1:]:
            name, separator, value = part.strip().partition(b"=")
            if separator and name.lower() == b"boundary":
                boundary = value.strip().strip(b'"')
                if boundary:
                    return len(b"--") + len(boundary) + len(b"--\r\n")
        return None

    async def _too_large(self, send: AsgiSend) -> None:
        await self._reject(
            send,
            status_code=413,
            code="request_body_too_large",
            message="上传请求体超过限制",
        )

    async def _invalid_content_length(self, send: AsgiSend) -> None:
        await self._reject(
            send,
            status_code=400,
            code="invalid_content_length",
            message="Content-Length 无效",
        )

    @staticmethod
    async def _reject(
        send: AsgiSend,
        *,
        status_code: int,
        code: str,
        message: str,
    ) -> None:
        body = json.dumps(
            {"detail": {"code": code, "message": message}},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
        await send(
            {
                "type": "http.response.start",
                "status": status_code,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
