# Agent Platform Backend

平台后端使用 Python 3.12、FastAPI 和 uv。API 与 Agent Worker 共用 `agent_platform` Python 包，但作为独立进程运行。

```bash
uv sync
uv run pytest
uv run uvicorn agent_platform.api.app:app --reload
```

## 本机 Demo 数据

数据库迁移完成后，可为本机开发库幂等写入一组可直接验收的数据：

```bash
uv run python -m agent_platform.bootstrap.demo_seed
```

- Owner 账号：`demo@example.com`
- Admin 账号：`demo.admin@example.com`
- Member 账号：`demo.member@example.com`
- 三个账号的登录密码均为：`agent-platform-demo`
- 工作区：`Agent Platform 演示工作区`
- 重复运行只补齐或修复稳定 ID 对应的数据，不会不断创建重复记录；CLI 会输出新增、更新和未变化数量。
- Seed 仅允许连接 `localhost`、`127.0.0.1` 或 `::1` 上的非系统 PostgreSQL 数据库，并且应用环境必须是 `local` 或 `development`；远程库及生产、预发布、测试环境会被拒绝。
- Demo MCP Server 和 Tool 默认禁用，不会发起外部调用。
- Member 账号拥有一条自己的终态演示任务，用于验收任务行级隔离。
- 默认不 Seed Skill 和知识库：SkillVersion 必须对应真实对象存储文件，知识库必须对应真实 RAGFlow dataset，伪造记录会产生坏引用。

自动化测试继续使用独立测试数据源与 Fixture，不读取或清理上述 Demo 数据。
