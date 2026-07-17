# AI 数字员工平台整体工程结构

> 状态：已确认的实现基线  
> 确认日期：2026-07-12  
> 补充确认：2026-07-14（AI 中台 Core、可插拔能力包与客户解决方案）
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
- Tauri 是普通用户的主要交付入口，Web 构建保留给复用测试、内部调试和未来可选管理端；
- AI 中台能力作为永久 Core，视频剪辑、自动运营等行业功能作为可插拔能力包；
- 客户差异通过能力授权、交付清单和声明式解决方案包实现，不维护客户专属代码分支；
- CI 根据目录变更分别执行前端、后端和跨系统检查；
- 生产环境分别扩容 API、Worker 和基础设施；只有启用可选企业管理端时才部署 Web 静态站点。

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
│   ├── capabilities/              # 能力清单和授权契约 Schema
│   └── fixtures/                  # 前后端契约测试公共样例
├── solution-packs/                # 非敏感、声明式客户解决方案配置
│   ├── templates/                 # 通用方案模板
│   └── examples/                  # 脱敏示例，不保存真实客户数据
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
│       │   ├── entitlements/      # 企业能力授权、套餐和交付清单
│       │   ├── employees/         # 数字员工定义、版本和发布
│       │   ├── runs/              # 任务、状态和事件
│       │   ├── approvals/         # 人工审批
│       │   ├── artifacts/         # 文件、任务附件、产物领域与存储端口
│       │   ├── skills/            # Skill 注册、版本、绑定和发布
│       │   ├── model_gateway/     # 租户模型网关 desired policy 与端口
│       │   └── audit/             # 企业审计
│       ├── capabilities/          # 可插拔行业能力，只依赖 Core 公开接口
│       │   ├── registry.py        # 模块清单、装配和可用性检查
│       │   ├── video_studio/      # 素材、Timeline、云剪辑和成片任务
│       │   └── social_operations/ # 平台账号、发布、客服和 RPA 任务
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
│       │   ├── llm/               # LiteLLM 公开 HTTP 协议的宿主侧 Adapter
│       │   ├── object_storage/    # MinIO/COS 文件与产物端口实现
│       │   ├── queue/
│       │   └── secrets/
│       ├── observability/         # OpenTelemetry、日志和指标
│       └── workers/               # Agent Worker 入口和任务消费
│           ├── main.py
│           └── model_gateway_controller.py  # 模型网关 provisioning 对账进程
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
capabilities ──────┤
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
- `platform/entitlements/` 是企业能力授权的唯一真相源，不依赖前端菜单或安装包判断；
- `capabilities/` 通过 Core 公开接口接入，Core 的 `platform/`、`runtimes/`、`knowledge/`、`tools/` 和 `memory/` 禁止反向导入能力包；
- `capabilities/video_studio/` 与 `capabilities/social_operations/` 禁止互相导入内部实现；
- `runtimes/` 统一实现 `EmployeeRuntime`，不得把 Deep Agents 或 LangGraph 内部事件直接暴露给 API；
- `knowledge/`、`tools/`、`memory/` 和 `sandbox/` 通过清晰接口被运行时调用；
- `infrastructure/` 实现数据库、缓存、存储、队列和密钥等技术接口，不能承载业务决策；
- API 和 Worker 共用同一个 Python 包与数据库模型，不拆成两套仓库或复制两套领域模型；
- 数字员工只保存 provider-neutral 模型 alias；供应商模型和密钥只进入独立 LiteLLM 配置，Worker 通过 `infrastructure/llm/` 访问网关。
- 租户模型网关策略、Key 生命周期、对账决策与凭据派生位于 `platform/model_gateway/`，SQLAlchemy policy/key/outbox 位于 `infrastructure/database/`，LiteLLM 管理适配位于 `infrastructure/llm/provisioner.py`；独立 Controller 进程 `workers/model_gateway_controller.py` 通过公开接口消费 outbox 并对账 LiteLLM，API 进程内不做任何对账。

### 3.2 后端进程

同一份 `backend/` 代码至少生成以下进程：

```text
API 进程
└── agent_platform.api.main

Worker 进程
└── agent_platform.workers.main

模型网关 Provisioning Controller 进程
└── agent_platform.workers.model_gateway_controller
```

各进程共享同一镜像与同一版本后端代码，但独立配置副本数量与生命周期。
Controller 独立于 API 请求路径运行，是唯一持有 LiteLLM master key 的平台进程。

## 4. 前端工程结构

```text
frontend/
├── src/
│   ├── app/                       # Provider、路由和布局
│   │   └── capability-registry/   # 根据服务端能力清单装配路由和菜单
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
│   │   ├── video-studio/          # 可选视频能力包前端
│   │   ├── social-operations/     # 可选自动运营能力包前端
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
├── e2e-tauri/                     # WebdriverIO 真实桌面流程
├── public/
├── src-tauri/                     # Tauri 原生适配和打包
│   ├── src/                       # Rust Command 与系统原生适配
│   ├── tests/                     # Rust 集成测试
│   ├── capabilities/              # 正式最小权限声明，不承载业务页面
│   ├── tauri.conf.json            # 正式桌面构建配置
│   └── tauri.test.conf.json       # 仅供 desktop-test 的测试权限叠加配置
├── package.json
├── vite.config.ts
├── playwright.config.ts
├── wdio.conf.ts
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

## 6. 能力包与客户解决方案

平台按三层组织产品能力：

```text
AI Platform Core（始终保留）
├── video-studio（按交付清单安装、按企业授权）
├── social-operations（按交付清单安装、按企业授权）
└── solution-pack（客户工作流、提示词、品牌和业务参数）
```

Core 包含企业、用户、权限、数字员工、运行时、模型、知识、记忆、Skill、Tool、任务、审批、产物、审计和客户端骨架。能力包只能依赖 Core 的公开服务、契约和事件，不能要求 Core 导入其内部模块。

每个能力包必须提供稳定清单，至少声明：

- `capability_id` 和版本；
- 后端路由、Worker 处理器和事件；
- 前端路由、菜单和按需加载入口；
- 所需企业 Entitlement 与 RBAC 权限；
- 可选 Tauri Sidecar、系统权限和云资源；
- 数据迁移、健康检查、测试和卸载/禁用行为。

`solution-packs/` 只保存非敏感的声明式资产，例如 AI 角色模板、工作流、提示词、知识绑定描述、审批规则、品牌和业务默认值。真实客户凭据、Cookie、聊天、联系人、素材和生产配置必须保存在平台数据库、对象存储或密钥服务，不得提交 Git。

能力可用性统一计算为：

```text
deployment_installed && tenant_entitled && user_permitted
```

同一主干可以生成不同交付清单，但禁止复制前后端工程、长期维护客户分支或在通用模块中硬编码客户名称。

## 7. Skill 代码与运行数据

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

## 8. 基础设施和本地开发

`infra/` 负责提供统一的本地依赖配置，但不把前后端业务代码放进基础设施目录。

```text
infra/compose/
├── core.yml                      # PostgreSQL、Redis、MinIO
└── observability.yml             # OTel Collector、Tempo、Prometheus 等
infra/ragflow/
├── VERSION                       # 锁定的官方稳定 tag
├── manage.sh                     # 原样管理官方独立 Compose
└── README.md                     # 独立端口和升级说明
```

拆分 Compose 文件是为了按需启动：

- 普通平台开发只启动 `core.yml`；
- 开发知识库时由 `infra/ragflow/manage.sh` 在 `.local/` 准备并运行对应官方 tag；
- 调试完整观测链路时再叠加 `observability.yml`；
- 本地服务使用完毕必须停止，不设置开机自启。

## 9. 工程脚本

根目录 `scripts/` 只保存跨工程动作，例如：

- 启动或停止本地依赖；
- 从后端导出 OpenAPI 和事件 Schema；
- 生成前端 API 客户端；
- 执行前后端联合检查；
- 初始化或发布内置 Skill；
- 校验能力包清单和生成客户交付配置；
- 清理本地临时运行数据。

前端专属脚本留在 `frontend/package.json`，后端专属工具配置留在 `backend/pyproject.toml`，避免根目录成为第三套构建系统。

## 10. 测试归属

| 测试类型 | 位置 |
|---|---|
| Python 单元测试 | `backend/tests/unit/` |
| 后端基础设施集成测试 | `backend/tests/integration/` |
| API 与事件契约测试 | `backend/tests/contract/` |
| React 单元和组件测试 | 与 `frontend/src/` 被测文件就近放置 |
| Web 核心用户流程 | `frontend/e2e/` |
| Tauri Rust 单元测试 | `frontend/src-tauri/` 内就近放置 |
| 能力包后端测试 | `backend/tests/` 对应 unit、integration、contract 目录 |
| 客户组合 E2E | `frontend/e2e/`，按能力组合或交付 Profile 标记 |
| 公共契约样例 | `contracts/fixtures/` |

不在根目录再创建一个混合的 `tests/`，避免测试运行器、依赖和职责混杂。

## 11. CI 边界

CI 根据改动路径执行：

- 修改 `backend/**`：运行 Python 格式、静态检查、单元测试和相关集成测试；
- 修改 `frontend/**`：运行 TypeScript 检查、Lint、Vitest、构建和相关 Playwright；
- 修改 `contracts/**` 或后端协议：重新生成客户端并运行前后端契约检查；
- 修改 `skills/**`：校验 Skill 目录、元数据、引用文件和脚本测试；
- 修改 `infra/**`：校验 Compose、镜像和观测配置；
- 修改能力包或 `solution-packs/**`：校验模块清单、授权、禁用行为和目标客户组合；
- 合并主分支前执行一次前后端完整检查。

CI 的最低组合矩阵必须包含 Core-only、Core+视频、Core+自动运营和当前正式客户交付组合。禁用可选能力时，Core 的登录、数字员工、任务、知识、Skill、Tool、审批和审计回归必须继续通过。

## 12. 未来扩展边界

未来如果建设独立移动 App，可以在根目录增加：

```text
mobile/
```

移动端继续复用平台 API、事件协议、认证语义和设计 Token，但不要求直接复用 React DOM 页面。未确定移动技术栈前不创建空工程，也不提前引入 Monorepo 工具。

## 13. 禁止事项

- 禁止将前端和后端拆成两个 Git 仓库；
- 禁止把 Python 后端放入 Tauri Sidecar 作为默认架构；
- 禁止前端直接导入后端源码或内部 LangGraph/Deep Agents 类型；
- 禁止 API 与 Worker 分别复制平台业务模型；
- 禁止在根目录堆放临时脚本、上传文件、数据库文件和沙盒数据；
- 禁止手工维护两套 OpenAPI DTO 或两套平台事件；
- 禁止为了单仓库而强制前后端共用同一个依赖管理器或同一个发布节奏；
- 禁止为客户复制前后端 Feature、API、Worker 或整仓代码；
- 禁止把前端隐藏菜单当作企业能力授权；
- 禁止在未授权时签发云资源凭据、下载 Sidecar 或调度能力包任务；
- 禁止让 AI 中台 Core 依赖视频剪辑、自动运营或任何客户解决方案内部代码。
