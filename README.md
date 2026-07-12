# Agent Platform

企业级 AI 数字员工平台。前端和后端位于同一个 Git 仓库，通过稳定的平台 API 与事件协议协作。

## 工程目录

- `backend/`：Python、FastAPI、Agent Worker 和运行时；
- `frontend/`：React、Vite、Web 和 Tauri 客户端；
- `contracts/`：OpenAPI、平台事件和契约样例；
- `skills/`：随平台源码维护的内置 Skill；
- `infra/`：本地依赖和部署基础设施配置；
- `docs/`：整体、后端和前端架构文档。

## 本地开发

后端使用 uv 管理 Python 3.12 和依赖：

```bash
cd backend
uv sync
uv run uvicorn agent_platform.api.app:app --reload
```

前端使用 pnpm：

```bash
cd frontend
pnpm install
pnpm dev
```

开发服务按需启动，使用完成后及时停止。

## 验证

```bash
cd backend
uv run pytest
uv run ruff check .
uv run mypy

cd ../frontend
pnpm test
pnpm typecheck
pnpm lint
pnpm build
```

完整工程规则见 `CLAUDE.md`，架构说明见 `docs/`。
