# Agent Platform Backend

平台后端使用 Python 3.12、FastAPI 和 uv。API 与 Agent Worker 共用 `agent_platform` Python 包，但作为独立进程运行。

```bash
uv sync
uv run pytest
uv run uvicorn agent_platform.api.app:app --reload
```
