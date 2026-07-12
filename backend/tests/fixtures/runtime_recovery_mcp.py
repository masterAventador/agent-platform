from __future__ import annotations

import asyncio
import os
from pathlib import Path
from threading import Lock

from mcp.server.fastmcp import Context, FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

COUNT_FILE = Path("/tmp/agent-platform-runtime-recovery-e2e-tool-count")
BLOCK_NEXT_FILE = Path("/tmp/agent-platform-runtime-recovery-e2e-tool-block-next")
STARTED_FILE = Path("/tmp/agent-platform-runtime-recovery-e2e-tool-started")
RELEASE_FILE = Path("/tmp/agent-platform-runtime-recovery-e2e-tool-release")
INVOCATION_FILE = Path("/tmp/agent-platform-runtime-recovery-e2e-tool-invocation")
INVOCATION_META_KEY = "io.agent-platform/invocation-id"
_count_lock = Lock()
_port = int(os.getenv("RUNTIME_RECOVERY_MCP_PORT", "18093"))
mcp = FastMCP(
    "Runtime recovery E2E MCP",
    host="127.0.0.1",
    port=_port,
    streamable_http_path="/mcp",
    stateless_http=True,
    json_response=True,
)


@mcp.tool()
async def recovery_external(value: str, ctx: Context) -> str:
    """Execute one externally observable recovery operation."""
    del value
    metadata = ctx.request_context.meta
    if isinstance(metadata, dict):
        invocation_id = metadata.get(INVOCATION_META_KEY)
    else:
        extra = getattr(metadata, "model_extra", None)
        invocation_id = extra.get(INVOCATION_META_KEY) if extra is not None else None
    INVOCATION_FILE.write_text(str(invocation_id or ""))
    with _count_lock:
        current = int(COUNT_FILE.read_text() or "0") if COUNT_FILE.exists() else 0
        COUNT_FILE.write_text(str(current + 1))
    if BLOCK_NEXT_FILE.exists():
        BLOCK_NEXT_FILE.unlink(missing_ok=True)
        STARTED_FILE.touch(mode=0o600)
        while not RELEASE_FILE.exists():
            await asyncio.sleep(0.05)
    return "Recovery external operation completed."


@mcp.custom_route("/health", methods=["GET"])
async def health(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


@mcp.custom_route("/count", methods=["GET"])
async def count(_request: Request) -> JSONResponse:
    value = int(COUNT_FILE.read_text() or "0") if COUNT_FILE.exists() else 0
    return JSONResponse({"count": value})


@mcp.custom_route("/reset", methods=["POST"])
async def reset(_request: Request) -> JSONResponse:
    for path in (COUNT_FILE, BLOCK_NEXT_FILE, STARTED_FILE, RELEASE_FILE, INVOCATION_FILE):
        path.unlink(missing_ok=True)
    return JSONResponse({"reset": True})


@mcp.custom_route("/block-next", methods=["POST"])
async def block_next(_request: Request) -> JSONResponse:
    STARTED_FILE.unlink(missing_ok=True)
    RELEASE_FILE.unlink(missing_ok=True)
    BLOCK_NEXT_FILE.touch(mode=0o600)
    return JSONResponse({"blocking": True})


@mcp.custom_route("/started", methods=["GET"])
async def started(_request: Request) -> JSONResponse:
    return JSONResponse({"started": STARTED_FILE.exists()})


@mcp.custom_route("/release", methods=["POST"])
async def release(_request: Request) -> JSONResponse:
    RELEASE_FILE.touch(mode=0o600)
    return JSONResponse({"released": True})


@mcp.custom_route("/last-invocation", methods=["GET"])
async def last_invocation(_request: Request) -> JSONResponse:
    value = INVOCATION_FILE.read_text() if INVOCATION_FILE.exists() else ""
    return JSONResponse({"invocation_id": value})


if __name__ == "__main__":
    mcp.run("streamable-http")
