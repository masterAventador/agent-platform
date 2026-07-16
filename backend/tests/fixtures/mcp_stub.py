"""C09 本地 MCP stub Server（streamable HTTP）。

用于契约/E2E 验收：官方 mcp SDK FastMCP 提供真实协议实现，另暴露
`/__control/*` 端点让测试切换工具目录（同步差异语义）、鉴权要求
（凭据链路）和故障模式（恶意/超慢/畸形 Server）。

启动方式：
    uv run uvicorn tests.fixtures.mcp_stub:app --host 127.0.0.1 --port <port>
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel

mcp = FastMCP("agent-platform-mcp-stub", stateless_http=True, json_response=True)

_state: dict[str, object] = {
    "profile": "v1",
    "mode": "normal",
    "required_token": None,
    "slow_seconds": 20.0,
}


def _search_customers_v1(query: str) -> dict[str, object]:
    """在企业客户系统中搜索客户。"""
    return {"customers": [{"name": "Acme", "match": query}], "profile": "v1"}


def _search_customers_v2(query: str) -> dict[str, object]:
    """在企业客户系统中搜索客户（升级版）。"""
    return {"customers": [{"name": "Acme", "match": query}], "profile": "v2"}


def _send_notification(message: str) -> dict[str, object]:
    """向企业通知渠道发送消息。"""
    return {"delivered": True, "message": message}


def _fetch_order(order_id: str) -> dict[str, object]:
    """查询订单详情。"""
    return {"order_id": order_id, "status": "shipped"}


_PROFILES: dict[str, list[tuple[object, str, str]]] = {
    "v1": [
        (_search_customers_v1, "search_customers", "在企业客户系统中搜索客户"),
        (_send_notification, "send_notification", "向企业通知渠道发送消息"),
    ],
    "v2": [
        (_search_customers_v2, "search_customers", "在企业客户系统中搜索客户（升级版）"),
        (_fetch_order, "fetch_order", "查询订单详情"),
    ],
}


def _apply_profile(profile: str) -> None:
    for name in list(mcp._tool_manager._tools):  # noqa: SLF001 - 测试夹具重置目录
        mcp.remove_tool(name)
    for fn, name, description in _PROFILES[profile]:
        mcp.add_tool(fn, name=name, description=description)
    _state["profile"] = profile


_apply_profile("v1")
_streamable_app = mcp.streamable_http_app()


class _FaultInjectionMiddleware:
    """只对 /mcp 路径注入慢响应、畸形响应和鉴权检查。"""

    def __init__(self, inner) -> None:
        self._inner = inner

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith("/mcp"):
            await self._inner(scope, receive, send)
            return
        mode = _state["mode"]
        if mode == "slow":
            await asyncio.sleep(float(_state["slow_seconds"]))  # type: ignore[arg-type]
        if mode == "malformed":
            await self._send_plain(
                send, status=200, body=b"<<< not a valid MCP payload >>>"
            )
            return
        required_token = _state["required_token"]
        if required_token is not None:
            headers = {
                key.decode("latin-1").lower(): value.decode("latin-1")
                for key, value in scope.get("headers", [])
            }
            if headers.get("authorization") != f"Bearer {required_token}":
                await self._send_plain(send, status=401, body=b'{"error":"unauthorized"}')
                return
        await self._inner(scope, receive, send)

    @staticmethod
    async def _send_plain(send, *, status: int, body: bytes) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": body})


@asynccontextmanager
async def _lifespan(app: FastAPI):
    del app
    async with mcp.session_manager.run():
        yield


app = FastAPI(lifespan=_lifespan)


class ProfileUpdate(BaseModel):
    profile: Literal["v1", "v2"]


class ModeUpdate(BaseModel):
    mode: Literal["normal", "slow", "malformed"]
    slow_seconds: float | None = None


class AuthUpdate(BaseModel):
    token: str | None


@app.get("/health")
async def health() -> dict[str, object]:
    return {
        "status": "ok",
        "profile": _state["profile"],
        "mode": _state["mode"],
        "auth_required": _state["required_token"] is not None,
    }


@app.post("/__control/profile")
async def set_profile(payload: ProfileUpdate) -> dict[str, object]:
    _apply_profile(payload.profile)
    return {"profile": payload.profile}


@app.post("/__control/mode")
async def set_mode(payload: ModeUpdate) -> dict[str, object]:
    _state["mode"] = payload.mode
    if payload.slow_seconds is not None:
        _state["slow_seconds"] = payload.slow_seconds
    return {"mode": payload.mode}


@app.post("/__control/auth")
async def set_auth(payload: AuthUpdate) -> dict[str, object]:
    _state["required_token"] = payload.token
    return {"auth_required": payload.token is not None}


app.mount("/", _FaultInjectionMiddleware(_streamable_app))
