from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

from agent_platform.sandbox.entities import SandboxScope
from agent_platform.sandbox.ports import SandboxAcquireRequest
from agent_platform.sandbox.providers.local_controller import LocalControllerSandboxProvider

SANDBOX_ID = "b" * 64


@pytest.mark.asyncio
async def test_provider_acquire_is_lease_idempotent_and_workspace_uses_same_backend() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/v1/sandboxes":
            return httpx.Response(200, json={"sandbox_id": SANDBOX_ID})
        if request.url.path.endswith("/files"):
            return httpx.Response(200, json={"files": [{"path": "/skills/demo/SKILL.md"}]})
        raise AssertionError(request.url.path)

    transport = httpx.MockTransport(handler)
    provider = LocalControllerSandboxProvider(
        base_url="http://sandbox-controller:8090",
        bearer_secret="controller-secret",
        transport=transport,
    )
    request = SandboxAcquireRequest(
        lease_id=uuid4(),
        scope=SandboxScope(uuid4(), uuid4(), uuid4(), "thread"),
        expires_at=datetime(2026, 7, 14, tzinfo=UTC),
        sandbox_epoch=7,
    )

    sandbox = await provider.acquire(request)
    await sandbox.workspace.write_file(path="/skills/demo/SKILL.md", content=b"# Demo")

    assert sandbox.workspace.backend is sandbox.backend
    assert json.loads(requests[0].content) == {
        "lease_id": str(request.lease_id),
        "sandbox_epoch": 7,
    }
    assert requests[0].headers["Authorization"] == "Bearer controller-secret"
    assert requests[1].headers["Authorization"] == "Bearer controller-secret"
    assert requests[1].headers["X-Sandbox-Lease-ID"] == str(request.lease_id)
    assert requests[1].headers["X-Sandbox-Epoch"] == "7"
    assert requests[0].extensions["timeout"]["read"] == 130.0
    assert requests[1].extensions["timeout"]["read"] == 130.0
    assert "controller-secret" not in repr(provider)


def test_provider_requires_internal_http_url_and_secret() -> None:
    with pytest.raises(ValueError, match="secret"):
        LocalControllerSandboxProvider(base_url="http://controller:8090", bearer_secret="")
    with pytest.raises(ValueError, match="http"):
        LocalControllerSandboxProvider(base_url="https://controller", bearer_secret="secret")
    with pytest.raises(ValueError, match="http"):
        LocalControllerSandboxProvider(
            base_url="http://controller:8090/unexpected", bearer_secret="secret"
        )


@pytest.mark.asyncio
async def test_provider_rejects_a_malicious_controller_sandbox_id() -> None:
    provider = LocalControllerSandboxProvider(
        base_url="http://sandbox-controller:8090",
        bearer_secret="controller-secret",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json={"sandbox_id": "../../docker.sock"})
        ),
    )
    request = SandboxAcquireRequest(
        lease_id=uuid4(),
        scope=SandboxScope(uuid4(), uuid4(), uuid4(), "thread"),
        expires_at=datetime(2026, 7, 14, tzinfo=UTC),
        sandbox_epoch=1,
    )

    with pytest.raises(ValueError, match="invalid sandbox id"):
        await provider.acquire(request)


@pytest.mark.asyncio
async def test_provider_discovers_and_deletes_by_exact_lease_with_bearer() -> None:
    lease_id = uuid4()
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"sandbox_ids": [SANDBOX_ID]})
        if request.method == "DELETE":
            return httpx.Response(200, json={"sandbox_id": SANDBOX_ID})
        raise AssertionError(request.method)

    provider = LocalControllerSandboxProvider(
        base_url="http://sandbox-controller:8090",
        bearer_secret="controller-secret",
        transport=httpx.MockTransport(handler),
    )

    assert await provider.discover(lease_id=lease_id, sandbox_epoch=4) == [SANDBOX_ID]
    assert await provider.delete_by_lease(lease_id=lease_id, sandbox_epoch=4) == SANDBOX_ID
    assert [(request.method, request.url.path) for request in requests] == [
        ("GET", "/v1/sandboxes"),
        ("DELETE", "/v1/sandboxes"),
    ]
    assert all(
        request.headers["Authorization"] == "Bearer controller-secret" for request in requests
    )
    assert requests[0].url.params["lease_id"] == str(lease_id)
    assert requests[0].url.params["sandbox_epoch"] == "4"
    assert requests[1].url.params["sandbox_epoch"] == "4"


@pytest.mark.asyncio
async def test_provider_passes_epoch_to_reconnect_delete_and_every_data_plane_request() -> None:
    lease_id = uuid4()
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"sandbox_id": SANDBOX_ID})
        if request.method == "DELETE":
            return httpx.Response(204)
        if request.url.path.endswith("/exec"):
            return httpx.Response(
                200,
                json={"output": "ok", "exit_code": 0, "truncated": False},
            )
        if request.url.path.endswith("/files"):
            return httpx.Response(200, json={"files": [{"path": "/workspace/demo"}]})
        return httpx.Response(
            200,
            json={
                "files": [
                    {
                        "path": "/workspace/demo",
                        "content_base64": "b2s=",
                        "error": None,
                    }
                ]
            },
        )

    provider = LocalControllerSandboxProvider(
        base_url="http://sandbox-controller:8090",
        bearer_secret="controller-secret",
        transport=httpx.MockTransport(handler),
    )

    sandbox = await provider.reconnect(
        sandbox_id=SANDBOX_ID,
        lease_id=lease_id,
        sandbox_epoch=9,
    )
    await sandbox.backend.aexecute("true")
    await sandbox.backend.aupload_files([("/workspace/demo", b"ok")])
    await sandbox.backend.adownload_files(["/workspace/demo"])
    await provider.delete(sandbox_id=SANDBOX_ID, lease_id=lease_id, sandbox_epoch=9)

    assert len(requests) == 5
    assert all(request.headers["X-Sandbox-Epoch"] == "9" for request in requests)


@pytest.mark.asyncio
async def test_empty_download_remains_an_empty_file_not_a_missing_file() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/sandboxes":
            return httpx.Response(200, json={"sandbox_id": SANDBOX_ID})
        if request.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(
            200,
            json={"files": [{"path": "/workspace/empty", "content_base64": "", "error": None}]},
        )

    provider = LocalControllerSandboxProvider(
        base_url="http://sandbox-controller:8090",
        bearer_secret="controller-secret",
        transport=httpx.MockTransport(handler),
    )
    request = SandboxAcquireRequest(
        lease_id=uuid4(),
        scope=SandboxScope(uuid4(), uuid4(), uuid4(), "thread"),
        expires_at=datetime(2026, 7, 14, tzinfo=UTC),
        sandbox_epoch=1,
    )
    sandbox = await provider.acquire(request)

    result = await sandbox.backend.adownload_files(["/workspace/empty"])

    assert result[0].content == b""
    await provider.delete(
        sandbox_id=sandbox.sandbox_id,
        lease_id=request.lease_id,
        sandbox_epoch=request.sandbox_epoch,
    )


@pytest.mark.asyncio
async def test_provider_close_continues_after_one_backend_fails() -> None:
    sandbox_ids = iter(["b" * 64, "c" * 64])
    provider = LocalControllerSandboxProvider(
        base_url="http://sandbox-controller:8090",
        bearer_secret="controller-secret",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json={"sandbox_id": next(sandbox_ids)})
        ),
    )
    first = await provider.acquire(
        SandboxAcquireRequest(
            lease_id=uuid4(),
            scope=SandboxScope(uuid4(), uuid4(), uuid4(), "first"),
            expires_at=datetime(2026, 7, 14, tzinfo=UTC),
            sandbox_epoch=1,
        )
    )
    second = await provider.acquire(
        SandboxAcquireRequest(
            lease_id=uuid4(),
            scope=SandboxScope(uuid4(), uuid4(), uuid4(), "second"),
            expires_at=datetime(2026, 7, 14, tzinfo=UTC),
            sandbox_epoch=1,
        )
    )
    first_close = AsyncMock(side_effect=RuntimeError("secret"))
    second_close = AsyncMock()
    first.backend.aclose = first_close
    second.backend.aclose = second_close

    with pytest.raises(RuntimeError, match="backend cleanup failed"):
        await provider.aclose()

    first_close.assert_awaited_once()
    second_close.assert_awaited_once()
