from __future__ import annotations

import base64
from hmac import compare_digest
from typing import Annotated, Any
from uuid import UUID

from docker.errors import APIError
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse

from agent_platform.sandbox.controller.config import ControllerSettings
from agent_platform.sandbox.controller.models import (
    CreateSandboxRequest,
    DownloadRequest,
    ExecRequest,
    ExecResponse,
    FileResult,
    FilesResponse,
    SandboxResponse,
    UploadRequest,
)
from agent_platform.sandbox.controller.service import (
    DockerSandboxController,
    SandboxLeaseMismatch,
    SandboxNotFound,
    decode_upload,
)

LeaseHeader = Annotated[UUID, Header(alias="X-Sandbox-Lease-ID")]


class RequestBodyTooLarge(Exception):
    pass


class RequestBodyLimitMiddleware:
    def __init__(self, app: Any, *, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        length = headers.get(b"content-length")
        if length is not None:
            try:
                content_length = int(length)
            except ValueError:
                await self._reject(send, status_code=400, detail="invalid content length")
                return
            if content_length < 0 or content_length > self.max_bytes:
                await self._reject(send)
                return
        received = 0

        async def limited_receive() -> Any:
            nonlocal received
            message = await receive()
            received += len(message.get("body", b""))
            if received > self.max_bytes:
                raise RequestBodyTooLarge
            return message

        try:
            await self.app(scope, limited_receive, send)
        except RequestBodyTooLarge:
            await self._reject(send)

    @staticmethod
    async def _reject(
        send: Any, *, status_code: int = 413, detail: str = "request body too large"
    ) -> None:
        body = f'{{"detail":"{detail}"}}'.encode()
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


def create_controller_app(*, settings: ControllerSettings, docker_client: Any) -> FastAPI:
    controller = DockerSandboxController(settings=settings, docker_client=docker_client)
    app = FastAPI(title="Agent Platform Local Sandbox Controller", docs_url=None, redoc_url=None)
    app.add_middleware(RequestBodyLimitMiddleware, max_bytes=settings.max_request_bytes)

    @app.exception_handler(ValueError)
    async def invalid_request(_request: Request, _error: ValueError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": "invalid request"})

    @app.exception_handler(SandboxNotFound)
    async def not_found(_request: Request, _error: SandboxNotFound) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": "sandbox not found"})

    @app.exception_handler(SandboxLeaseMismatch)
    async def lease_mismatch(_request: Request, _error: SandboxLeaseMismatch) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": "sandbox not found"})

    @app.exception_handler(APIError)
    async def docker_unavailable(_request: Request, _error: APIError) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": "controller unavailable"})

    @app.exception_handler(RuntimeError)
    async def internal_failure(_request: Request, _error: RuntimeError) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": "controller unavailable"})

    def authorize(authorization: Annotated[str | None, Header()] = None) -> None:
        expected = f"Bearer {settings.bearer_secret}"
        if authorization is None or not compare_digest(authorization, expected):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthorized")

    protected = [Depends(authorize)]

    @app.get("/health/live", include_in_schema=False)
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready", include_in_schema=False)
    def ready() -> dict[str, str]:
        if not docker_client.ping():
            raise HTTPException(status_code=503, detail="controller unavailable")
        return {"status": "ok"}

    @app.post("/v1/sandboxes", response_model=SandboxResponse, dependencies=protected)
    def create(request: CreateSandboxRequest) -> SandboxResponse:
        result = controller.create(lease_id=request.lease_id)
        return SandboxResponse(sandbox_id=result.sandbox_id)

    @app.get("/v1/sandboxes/{sandbox_id}", response_model=SandboxResponse, dependencies=protected)
    def reconnect(sandbox_id: str, lease_id: LeaseHeader) -> SandboxResponse:
        result = controller.reconnect(sandbox_id, lease_id=lease_id)
        return SandboxResponse(sandbox_id=result.sandbox_id)

    @app.post(
        "/v1/sandboxes/{sandbox_id}/exec", response_model=ExecResponse, dependencies=protected
    )
    def execute(
        sandbox_id: str,
        request: ExecRequest,
        lease_id: LeaseHeader,
    ) -> ExecResponse:
        result = controller.execute(
            sandbox_id, lease_id=lease_id, command=request.command, timeout=request.timeout
        )
        return ExecResponse(**result.__dict__)

    @app.put(
        "/v1/sandboxes/{sandbox_id}/files", response_model=FilesResponse, dependencies=protected
    )
    def upload(
        sandbox_id: str,
        request: UploadRequest,
        lease_id: LeaseHeader,
    ) -> FilesResponse:
        encoded_bytes = sum(len(item.content_base64) for item in request.files)
        if encoded_bytes > ((settings.max_batch_bytes + 2) // 3) * 4:
            raise ValueError("upload batch 超过 controller 限制")
        files = [
            (item.path, decode_upload(item.content_base64, max_bytes=settings.max_file_bytes))
            for item in request.files
        ]
        paths = controller.upload(sandbox_id, lease_id=lease_id, files=files)
        return FilesResponse(files=[FileResult(path=path) for path in paths])

    @app.post(
        "/v1/sandboxes/{sandbox_id}/download",
        response_model=FilesResponse,
        dependencies=protected,
    )
    def download(
        sandbox_id: str,
        request: DownloadRequest,
        lease_id: LeaseHeader,
    ) -> FilesResponse:
        results = controller.download(sandbox_id, lease_id=lease_id, paths=request.paths)
        return FilesResponse(
            files=[
                FileResult(
                    path=path,
                    content_base64=(
                        base64.b64encode(content).decode() if content is not None else None
                    ),
                    error=error,
                )
                for path, content, error in results
            ]
        )

    @app.delete(
        "/v1/sandboxes/{sandbox_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=protected
    )
    def delete(sandbox_id: str, lease_id: LeaseHeader) -> Response:
        controller.delete(sandbox_id, lease_id=lease_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return app
