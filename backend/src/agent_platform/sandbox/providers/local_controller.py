from __future__ import annotations

import base64
import re
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from deepagents.backends.protocol import ExecuteResponse, FileDownloadResponse, FileUploadResponse
from deepagents.backends.sandbox import BaseSandbox

from agent_platform.sandbox.ports import ProviderSandbox, SandboxAcquireRequest

_SANDBOX_ID = re.compile(r"^[0-9a-f]{64}$")


class LocalControllerBackend(BaseSandbox):
    def __init__(
        self,
        *,
        sandbox_id: str,
        lease_id: UUID,
        sandbox_epoch: int,
        base_url: str,
        bearer_secret: str,
        transport: httpx.BaseTransport | httpx.AsyncBaseTransport | None = None,
        request_timeout: httpx.Timeout | None = None,
    ) -> None:
        self._sandbox_id = sandbox_id
        self._lease_id = lease_id
        self._sandbox_epoch = sandbox_epoch
        headers = {
            "Authorization": f"Bearer {bearer_secret}",
            "X-Sandbox-Lease-ID": str(lease_id),
            "X-Sandbox-Epoch": str(sandbox_epoch),
        }
        sync_transport = transport if isinstance(transport, httpx.BaseTransport) else None
        async_transport = transport if isinstance(transport, httpx.AsyncBaseTransport) else None
        timeout = request_timeout or httpx.Timeout(130.0)
        self._client = httpx.Client(
            base_url=base_url,
            headers=headers,
            transport=sync_transport,
            timeout=timeout,
        )
        self._async_client = httpx.AsyncClient(
            base_url=base_url,
            headers=headers,
            transport=async_transport,
            timeout=timeout,
        )

    @property
    def id(self) -> str:
        return self._sandbox_id

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        response = self._client.post(
            f"/v1/sandboxes/{self.id}/exec", json={"command": command, "timeout": timeout}
        )
        response.raise_for_status()
        payload = response.json()
        return ExecuteResponse(**payload)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        response = self._client.put(
            f"/v1/sandboxes/{self.id}/files", json={"files": self._encoded_files(files)}
        )
        response.raise_for_status()
        return [
            FileUploadResponse(path=item["path"], error=item.get("error"))
            for item in response.json()["files"]
        ]

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        response = self._client.post(f"/v1/sandboxes/{self.id}/download", json={"paths": paths})
        response.raise_for_status()
        return self._download_results(response.json()["files"])

    async def aexecute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        response = await self._async_client.post(
            f"/v1/sandboxes/{self.id}/exec", json={"command": command, "timeout": timeout}
        )
        response.raise_for_status()
        return ExecuteResponse(**response.json())

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        response = await self._async_client.put(
            f"/v1/sandboxes/{self.id}/files", json={"files": self._encoded_files(files)}
        )
        response.raise_for_status()
        return [
            FileUploadResponse(path=item["path"], error=item.get("error"))
            for item in response.json()["files"]
        ]

    async def adownload_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        response = await self._async_client.post(
            f"/v1/sandboxes/{self.id}/download", json={"paths": paths}
        )
        response.raise_for_status()
        return self._download_results(response.json()["files"])

    def close(self) -> None:
        self._client.close()

    async def aclose(self) -> None:
        failures = 0
        try:
            self._client.close()
        except Exception:
            failures += 1
        try:
            await self._async_client.aclose()
        except Exception:
            failures += 1
        if failures:
            raise RuntimeError("local controller client cleanup failed")

    @staticmethod
    def _encoded_files(files: list[tuple[str, bytes]]) -> list[dict[str, str]]:
        return [
            {"path": path, "content_base64": base64.b64encode(content).decode()}
            for path, content in files
        ]

    @staticmethod
    def _download_results(items: list[dict[str, Any]]) -> list[FileDownloadResponse]:
        return [
            FileDownloadResponse(
                path=item["path"],
                content=(
                    base64.b64decode(item["content_base64"])
                    if item.get("content_base64") is not None
                    else None
                ),
                error=item.get("error"),
            )
            for item in items
        ]


class LocalControllerWorkspace:
    def __init__(self, backend: LocalControllerBackend) -> None:
        self.backend = backend

    async def write_file(self, *, path: str, content: bytes) -> None:
        results = await self.backend.aupload_files([(path, content)])
        if results[0].error is not None:
            raise RuntimeError(f"sandbox write failed: {results[0].error}")


class LocalControllerSandboxProvider:
    name = "local-controller"

    def __init__(
        self,
        *,
        base_url: str,
        bearer_secret: str,
        transport: httpx.BaseTransport | httpx.AsyncBaseTransport | None = None,
        request_timeout_seconds: float = 130.0,
    ) -> None:
        parts = urlsplit(base_url)
        try:
            port = parts.port
        except ValueError:
            raise ValueError("local controller URL port 无效") from None
        if (
            parts.scheme != "http"
            or not parts.hostname
            or port is None
            or parts.username
            or parts.query
            or parts.fragment
            or parts.path not in {"", "/"}
        ):
            raise ValueError("local controller 必须使用无凭据的内部 http URL")
        if not bearer_secret:
            raise ValueError("local controller secret 未配置")
        if request_timeout_seconds < 125 or request_timeout_seconds > 3_600:
            raise ValueError("local controller request timeout 超出安全范围")
        self._base_url = base_url.rstrip("/")
        self._secret = bearer_secret
        self._request_timeout = httpx.Timeout(request_timeout_seconds)
        self._transport = transport
        self._backends: dict[str, LocalControllerBackend] = {}

    async def acquire(self, request: SandboxAcquireRequest) -> ProviderSandbox:
        async with self._async_client() as client:
            response = await client.post(
                "/v1/sandboxes",
                json={
                    "lease_id": str(request.lease_id),
                    "sandbox_epoch": request.sandbox_epoch,
                },
            )
            response.raise_for_status()
            return self._sandbox(
                response.json()["sandbox_id"],
                lease_id=request.lease_id,
                sandbox_epoch=request.sandbox_epoch,
            )

    async def reconnect(
        self,
        *,
        sandbox_id: str,
        lease_id: UUID,
        sandbox_epoch: int,
    ) -> ProviderSandbox:
        async with self._async_client(
            lease_id=lease_id,
            sandbox_epoch=sandbox_epoch,
        ) as client:
            response = await client.get(f"/v1/sandboxes/{sandbox_id}")
            response.raise_for_status()
            return self._sandbox(
                response.json()["sandbox_id"],
                lease_id=lease_id,
                sandbox_epoch=sandbox_epoch,
            )

    async def delete(self, *, sandbox_id: str, lease_id: UUID, sandbox_epoch: int) -> None:
        async with self._async_client(
            lease_id=lease_id,
            sandbox_epoch=sandbox_epoch,
        ) as client:
            response = await client.delete(f"/v1/sandboxes/{sandbox_id}")
            response.raise_for_status()
        backend = self._backends.pop(sandbox_id, None)
        if backend is not None:
            await backend.aclose()

    async def discover(self, *, lease_id: UUID, sandbox_epoch: int) -> list[str]:
        async with self._async_client() as client:
            response = await client.get(
                "/v1/sandboxes",
                params={
                    "lease_id": str(lease_id),
                    "sandbox_epoch": sandbox_epoch,
                },
            )
            response.raise_for_status()
        sandbox_ids = response.json()["sandbox_ids"]
        if not isinstance(sandbox_ids, list) or len(sandbox_ids) > 1:
            raise ValueError("controller returned ambiguous sandbox discovery")
        for sandbox_id in sandbox_ids:
            if not isinstance(sandbox_id, str) or _SANDBOX_ID.fullmatch(sandbox_id) is None:
                raise ValueError("controller returned an invalid sandbox id")
        return sandbox_ids

    async def delete_by_lease(self, *, lease_id: UUID, sandbox_epoch: int) -> str | None:
        async with self._async_client() as client:
            response = await client.delete(
                "/v1/sandboxes",
                params={
                    "lease_id": str(lease_id),
                    "sandbox_epoch": sandbox_epoch,
                },
            )
            response.raise_for_status()
        sandbox_id = response.json()["sandbox_id"]
        if sandbox_id is not None and (
            not isinstance(sandbox_id, str) or _SANDBOX_ID.fullmatch(sandbox_id) is None
        ):
            raise ValueError("controller returned an invalid sandbox id")
        if sandbox_id is not None:
            backend = self._backends.pop(sandbox_id, None)
            if backend is not None:
                await backend.aclose()
        return sandbox_id

    async def disconnect(self, *, sandbox_id: str) -> None:
        backend = self._backends.pop(sandbox_id, None)
        if backend is not None:
            await backend.aclose()

    async def aclose(self) -> None:
        backends = list(self._backends.values())
        self._backends.clear()
        failures = 0
        for backend in backends:
            try:
                await backend.aclose()
            except Exception:
                failures += 1
        if failures:
            raise RuntimeError("local controller backend cleanup failed")

    def _sandbox(
        self,
        sandbox_id: str,
        *,
        lease_id: UUID,
        sandbox_epoch: int,
    ) -> ProviderSandbox:
        if _SANDBOX_ID.fullmatch(sandbox_id) is None:
            raise ValueError("controller returned an invalid sandbox id")
        previous = self._backends.pop(sandbox_id, None)
        if previous is not None:
            previous.close()
        backend = LocalControllerBackend(
            sandbox_id=sandbox_id,
            lease_id=lease_id,
            sandbox_epoch=sandbox_epoch,
            base_url=self._base_url,
            bearer_secret=self._secret,
            transport=self._transport,
            request_timeout=self._request_timeout,
        )
        self._backends[sandbox_id] = backend
        return ProviderSandbox(
            sandbox_id=sandbox_id,
            workspace=LocalControllerWorkspace(backend),
            backend=backend,
            sandbox_epoch=sandbox_epoch,
        )

    def _async_client(
        self,
        *,
        lease_id: UUID | None = None,
        sandbox_epoch: int | None = None,
    ) -> httpx.AsyncClient:
        transport = (
            self._transport if isinstance(self._transport, httpx.AsyncBaseTransport) else None
        )
        headers = {"Authorization": f"Bearer {self._secret}"}
        if lease_id is not None:
            headers["X-Sandbox-Lease-ID"] = str(lease_id)
        if sandbox_epoch is not None:
            headers["X-Sandbox-Epoch"] = str(sandbox_epoch)
        return httpx.AsyncClient(
            base_url=self._base_url,
            headers=headers,
            transport=transport,
            timeout=self._request_timeout,
        )

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(base_url={self._base_url!r})"
