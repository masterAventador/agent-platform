# AI 数字员工平台整体工程结构

> 状态：已确认的实现基线  
> 确认日期：2026-07-12  
> 适用范围：项目根目录、前后端工程、协议、Skill、基础设施和工程脚本

## 1. 核心决策

项目采用单一 Git 仓库管理，前端和后端都放在同一个项目根目录中，通过 `frontend/` 和 `backend/` 分隔。

```text
同一个 Git 仓库
├── backend/       # Python、FastAPI、Worker、Agent 运行时
└── frontend/      # React、Web、Tauri
```

单仓库不代表单进程或单部署单元：

- 前后端分别维护依赖、测试和构建配置；
- FastAPI API 与 Agent Worker 共用后端代码，但作为独立进程运行；
- Web 与 Tauri 共用前端业务代码，但分别生成 Web 静态资源和桌面安装包；
- CI 根据目录变更分别执行前端、后端和跨系统检查；
- 生产环境可以分别扩容 API、Worker、Web 和基础设施。

## 2. 项目根目录

```text
agent-platform/
├── backend/                       # Python 后端工程
├── frontend/                      # React + Tauri 前端工程
├── skills/                        # 平台随代码发布的内置 Skill 源文件
│   ├── builtin/                   # 正式内置 Skill
│   └── examples/                  # 开发和演示 Skill
├── contracts/                     # 由后端协议导出的跨端契约产物
│   ├── openapi/                   # OpenAPI 快照
│   ├── events/                    # 平台事件 JSON Schema
│   └── fixtures/                  # 前后端契约测试公共样例
├── infra/                         # 本地依赖和部署基础设施配置
│   ├── compose/                   # PostgreSQL、Redis、MinIO 等本地编排
│   ├── docker/                    # API、Worker、Web 镜像配置
│   ├── otel/                      # OpenTelemetry Collector 配置
│   └── monitoring/                # Grafana 等观测配置
├── scripts/                       # 跨工程开发、生成、检查脚本
├── docs/
│   ├── backend-architecture.md
│   ├── frontend-architecture.md
│   ├── project-structure.md
│   └── adr/                       # 重要架构决策记录
├── .github/
│   └── workflows/                 # CI 工作流
├── .local/                        # 本地运行数据，必须加入 .gitignore
├── AGENTS.md
├── CLAUDE.md
├── README.md
├── .editorconfig
├── .gitignore
└── .env.example                   # 只列公共变量名和安全示例，不保存密钥
```

根目录只保存跨前后端内容，不放业务实现代码。

## 3. 后端工程结构

```text
backend/
├── src/
│   └── agent_platform/
│       ├── bootstrap/             # 配置加载、依赖装配和进程启动
│       ├── api/                   # FastAPI HTTP、SSE、WebSocket 边界
│       │   ├── routers/
│       │   ├── middleware/
│       │   ├── dependencies/
│       │   ├── errors/
│       │   └── main.py            # API 进程入口
│       ├── platform/              # 企业平台业务模块
│       │   ├── auth/              # 邮箱注册登录、Session、OIDC
│       │   ├── tenants/           # 企业和租户
│       │   ├── users/             # 用户
│       │   ├── permissions/       # RBAC 和资源授权
│       │   ├── employees/         # 数字员工定义、版本和发布
│       │   ├── runs/              # 任务、状态和事件
│       │   ├── approvals/         # 人工审批
│       │   ├── artifacts/         # 文件和任务产物索引
│       │   ├── skills/            # Skill 注册、版本、绑定和发布
│       │   └── audit/             # 企业审计
│       ├── runtimes/              # EmployeeRuntime 实现
│       │   ├── base.py
│       │   ├── deep_agent.py
│       │   └── langgraph.py
│       ├── knowledge/             # Knowledge Service 和 RAGFlow 适配
│       ├── tools/                 # Tool Gateway、MCP 和审批策略
│       ├── memory/                # Checkpointer、Store 和记忆策略
│       ├── sandbox/               # Sandbox Manager 和供应商适配
│       │   ├── manager.py
│       │   └── providers/
│       ├── infrastructure/        # 技术基础设施实现
│       │   ├── database/
│       │   ├── cache/
│       │   ├── object_storage/
│       │   ├── queue/
│       │   └── secrets/
│       ├── observability/         # OpenTelemetry、日志和指标
│       └── workers/               # Agent Worker 入口和任务消费
│           └── main.py
├── migrations/                    # Alembic 数据库迁移
├── tests/
│   ├── unit/                      # 纯业务和组件单元测试
│   ├── integration/               # 数据库、Redis、对象存储等集成测试
│   ├── contract/                  # API 和事件契约测试
│   └── fixtures/                  # 后端测试数据
├── pyproject.toml                 # Python 依赖和工具配置
├── alembic.ini
├── Dockerfile.api
├── Dockerfile.worker
├── .env.example
└── README.md
```

### 3.1 后端依赖方向

```text
api ───────────────┐
workers ───────────┤
                   ▼
                platform
                   │
                   ▼
                runtimes
        ┌──────────┼──────────┐
        ▼          ▼          ▼
    knowledge    tools      sandbox
        └──────────┼──────────┘
                   ▼
            infrastructure
```

强制边界：

- `api/` 只负责协议转换、认证入口、参数校验和调用业务服务，不实现 Agent 逻辑；
- `workers/` 负责消费任务和驱动运行时，不复制 API 业务代码；
- `platform/` 保存平台业务规则，不依赖 FastAPI 请求对象；
- `runtimes/` 统一实现 `EmployeeRuntime`，不得把 Deep Agents 或 LangGraph 内部事件直接暴露给 API；
- `knowledge/`、`tools/`、`memory/` 和 `sandbox/` 通过清晰接口被运行时调用；
- `infrastructure/` 实现数据库、缓存、存储、队列和密钥等技术接口，不能承载业务决策；
- API 和 Worker 共用同一个 Python 包与数据库模型，不拆成两套仓库或复制两套领域模型。

### 3.2 后端进程

同一份 `backend/` 代码至少生成两个进程：

```text
API 进程
└── agent_platform.api.main

Worker 进程
└── agent_platform.workers.main
```

二者独立构建镜像、独立配置副本数量，但使用相同版本的后端代码。

## 4. 前端工程结构

```text
frontend/
├── src/
│   ├── app/                       # Provider、路由和布局
│   ├── features/                  # 按业务功能组织的页面和逻辑
│   │   ├── auth/
│   │   ├── dashboard/
│   │   ├── employees/
│   │   ├── runs/
│   │   ├── approvals/
│   │   ├── knowledge/
│   │   ├── skills/
│   │   ├── tools/
│   │   ├── artifacts/
│   │   ├── observability/
│   │   ├── audit/
│   │   └── organization/
│   ├── components/                # 跨 Feature 公共 UI 和 AI 组件
│   ├── api/
│   │   ├── generated/             # 从 OpenAPI 生成，禁止手工修改
│   │   ├── client.ts
│   │   ├── streaming.ts
│   │   └── query-client.ts
│   ├── platform/                  # Web/Tauri PlatformAdapter
│   ├── stores/
│   ├── schemas/
│   ├── hooks/
│   ├── styles/
│   ├── assets/
│   ├── test/
│   └── main.tsx
├── e2e/                           # Playwright 跨页面流程
├── public/
├── src-tauri/                     # Tauri 原生适配和打包
├── package.json
├── vite.config.ts
├── playwright.config.ts
├── tsconfig.json
├── Dockerfile.web
├── .env.example
└── README.md
```

完整前端分层和目录约束以 [`frontend-architecture.md`](frontend-architecture.md) 为准。

前后端具体包管理器在初始化工程时确认；一旦选定，生成的依赖锁文件必须提交，后续不得并存多种包管理器的锁文件。

## 5. 前后端契约

前后端不通过源码导入互相依赖，只通过协议协作：

```text
backend Pydantic / FastAPI
        │
        ├── 生成 OpenAPI ──→ contracts/openapi/
        │                         │
        │                         ▼
        │                frontend/src/api/generated/
        │
        └── 导出事件 Schema → contracts/events/
                                  │
                                  ▼
                         前端 SSE 事件模型
```

规则：

- FastAPI/Pydantic 是 REST 契约的唯一源头；
- 平台事件定义在后端统一事件模块中，再导出 JSON Schema；
- `contracts/` 保存生成后的稳定快照和测试样例，不手写第二套业务模型；
- 前端生成代码禁止手工修改，业务层通过 Adapter 转换成前端领域模型；
- CI 必须检查生成产物是否与后端定义一致；
- 破坏性协议修改必须增加版本或提供兼容迁移期。

## 6. Skill 代码与运行数据

根目录 `skills/` 只保存随平台源码维护的内置 Skill：

```text
skills/builtin/report-writer/
├── SKILL.md
├── scripts/
├── references/
└── assets/
```

企业用户上传、在线编辑或安装的 Skill 不直接写入 Git 工作区，而是由平台 Skill Registry 保存到数据库和对象存储。发布流程可以将 `skills/builtin/` 中的 Skill 注册到平台。

以下内容都属于运行数据，禁止提交 Git：

- 用户上传文件；
- Agent 临时工作区；
- 沙盒文件系统；
- MinIO 本地数据；
- PostgreSQL 和 Redis 本地数据；
- 运行日志和 Trace；
- 凭据和真实环境变量。

本地运行数据统一放在根目录 `.local/` 或 Docker Volume，并加入 `.gitignore`。

## 7. 基础设施和本地开发

`infra/` 负责提供统一的本地依赖配置，但不把前后端业务代码放进基础设施目录。

```text
infra/compose/
├── core.yml                      # PostgreSQL、Redis、MinIO
├── knowledge.yml                 # RAGFlow 及其依赖
└── observability.yml             # OTel Collector、Tempo、Prometheus 等
```

拆分 Compose 文件是为了按需启动：

- 普通平台开发只启动 `core.yml`；
- 开发知识库时再叠加 `knowledge.yml`；
- 调试完整观测链路时再叠加 `observability.yml`；
- 本地服务使用完毕必须停止，不设置开机自启。

## 8. 工程脚本

根目录 `scripts/` 只保存跨工程动作，例如：

- 启动或停止本地依赖；
- 从后端导出 OpenAPI 和事件 Schema；
- 生成前端 API 客户端；
- 执行前后端联合检查；
- 初始化或发布内置 Skill；
- 清理本地临时运行数据。

前端专属脚本留在 `frontend/package.json`，后端专属工具配置留在 `backend/pyproject.toml`，避免根目录成为第三套构建系统。

## 9. 测试归属

| 测试类型 | 位置 |
|---|---|
| Python 单元测试 | `backend/tests/unit/` |
| 后端基础设施集成测试 | `backend/tests/integration/` |
| API 与事件契约测试 | `backend/tests/contract/` |
| React 单元和组件测试 | 与 `frontend/src/` 被测文件就近放置 |
| Web 核心用户流程 | `frontend/e2e/` |
| Tauri Rust 单元测试 | `frontend/src-tauri/` 内就近放置 |
| 公共契约样例 | `contracts/fixtures/` |

不在根目录再创建一个混合的 `tests/`，避免测试运行器、依赖和职责混杂。

## 10. CI 边界

CI 根据改动路径执行：

- 修改 `backend/**`：运行 Python 格式、静态检查、单元测试和相关集成测试；
- 修改 `frontend/**`：运行 TypeScript 检查、Lint、Vitest、构建和相关 Playwright；
- 修改 `contracts/**` 或后端协议：重新生成客户端并运行前后端契约检查；
- 修改 `skills/**`：校验 Skill 目录、元数据、引用文件和脚本测试；
- 修改 `infra/**`：校验 Compose、镜像和观测配置；
- 合并主分支前执行一次前后端完整检查。

## 11. 未来扩展边界

未来如果建设独立移动 App，可以在根目录增加：

```text
mobile/
```

移动端继续复用平台 API、事件协议、认证语义和设计 Token，但不要求直接复用 React DOM 页面。未确定移动技术栈前不创建空工程，也不提前引入 Monorepo 工具。

## 12. 禁止事项

- 禁止将前端和后端拆成两个 Git 仓库；
- 禁止把 Python 后端放入 Tauri Sidecar 作为默认架构；
- 禁止前端直接导入后端源码或内部 LangGraph/Deep Agents 类型；
- 禁止 API 与 Worker 分别复制平台业务模型；
- 禁止在根目录堆放临时脚本、上传文件、数据库文件和沙盒数据；
- 禁止手工维护两套 OpenAPI DTO 或两套平台事件；
- 禁止为了单仓库而强制前后端共用同一个依赖管理器或同一个发布节奏。
