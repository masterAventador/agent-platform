from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException

READY_FILE = Path("/tmp/agent-platform-runtime-recovery-e2e-worker-ready")
SUPERVISOR_READY_FILE = Path("/tmp/agent-platform-runtime-recovery-e2e-supervisor-ready")
MODEL_PHASE_FILE = Path("/tmp/agent-platform-runtime-recovery-e2e-model-phase")
_child: subprocess.Popen[bytes] | None = None
_lock = asyncio.Lock()


def _running() -> bool:
    return _child is not None and _child.poll() is None


async def _spawn() -> int:
    global _child
    async with _lock:
        if _running():
            raise HTTPException(status_code=409, detail="worker already running")
        READY_FILE.unlink(missing_ok=True)
        _child = subprocess.Popen(
            [sys.executable, "-m", "tests.fixtures.runtime_recovery_worker_child"],
            env=os.environ.copy(),
        )
        return _child.pid


async def _kill(*, force: bool) -> None:
    global _child
    async with _lock:
        if not _running():
            return
        assert _child is not None
        os.kill(_child.pid, signal.SIGKILL if force else signal.SIGTERM)
        await asyncio.to_thread(_child.wait, 10)
        _child = None
        READY_FILE.unlink(missing_ok=True)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await _spawn()
    SUPERVISOR_READY_FILE.touch(mode=0o600)
    try:
        yield
    finally:
        SUPERVISOR_READY_FILE.unlink(missing_ok=True)
        await _kill(force=False)


app = FastAPI(lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, bool]:
    return {"running": _running()}


@app.get("/status")
async def status() -> dict[str, int | bool | None]:
    return {
        "running": _running(),
        "ready": READY_FILE.exists(),
        "pid": _child.pid if _running() and _child is not None else None,
    }


@app.post("/kill")
async def kill() -> dict[str, bool]:
    await _kill(force=True)
    return {"killed": True}


@app.post("/restart")
async def restart() -> dict[str, int]:
    return {"pid": await _spawn()}


@app.post("/reset-model")
async def reset_model() -> dict[str, bool]:
    if _running():
        raise HTTPException(status_code=409, detail="worker must be stopped")
    MODEL_PHASE_FILE.unlink(missing_ok=True)
    return {"reset": True}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=18092, log_level="warning")
