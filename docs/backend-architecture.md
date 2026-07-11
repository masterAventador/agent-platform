# AI 数字员工平台后端架构

> 状态：已确认的实现基线  
> 确认日期：2026-07-12  
> 适用范围：企业级数字员工平台后端、Agent 运行时、知识库、Skill、工具、任务与观测体系

## 1. 建设目标

建设一套可供 Web、桌面端和 App 共同使用的企业级数字员工平台。平台需要支持：

- 多企业、多用户和权限隔离；
- 自主规划型数字员工；
- 固定流程型数字员工；
- Skill 按需加载；
- MCP 和企业工具调用；
- 企业知识库；
- 短期状态与长期记忆；
- 人工审批、中断、恢复和取消；
- 文件与任务产物管理；
- 调用链观测和企业审计；
- 后续扩展超长业务流程。

面向用户统一使用“数字员工”概念。用户不需要知道某个员工底层使用 Deep Agents、LangGraph 或未来的 Temporal。

## 2. 已确认的核心决策

### 2.1 Python 优先

后端平台、Agent 运行时和 Worker 统一使用 Python，避免平台服务与 Agent 服务之间产生不必要的跨语言 RPC、事件转换和双重数据模型。

主要技术组件：

- FastAPI：平台 REST、SSE 和 WebSocket 接口；
- Pydantic：请求、响应和内部数据模型；
- SQLAlchemy + Alembic：业务数据访问与数据库迁移；
- LangGraph：唯一的 Agent 和 AI 工作流执行内核；
- Deep Agents：自主型数字员工的默认实现；
- OpenTelemetry：统一观测标准；
- pytest：自动化测试。

### 2.2 不采用 AgentOS 作为正式核心组件

AgentOS 对外部 LangGraph/Deep Agents 主要提供注册、基础 API、SSE、Session 和工具事件适配。平台仍需自行实现企业、租户、业务权限、统一事件、知识、记忆、Skill、审批、沙箱和可靠任务执行。

继续引入 AgentOS 会形成以下冗余链路：

```text
客户端 -> 自研接口层 -> AgentOS -> LangGraph 适配 -> Deep Agents/LangGraph
```

正式架构直接采用：

```text
客户端 -> 自研平台 API -> Deep Agents/LangGraph
```

AgentOS 仅作为参考实现，不作为运行依赖。

### 2.3 LangGraph 是唯一执行内核

所有数字员工最终统一为可运行的 LangGraph：

- 自主型员工：使用 Deep Agents 创建，底层仍是编译后的 LangGraph；
- 流程型员工：直接使用 LangGraph 定义确定性节点、分支、循环、审批和恢复；
- 混合型员工：LangGraph 作为流程主干，部分节点调用 Deep Agents 完成开放任务。

### 2.4 Deep Agents 负责自主型员工

Deep Agents 用于需要自主规划、Todo、Skill、文件工作区、沙箱、子智能体和长上下文管理的任务，例如：

- 行业研究；
- 竞品分析；
- 报告生成；
- 办公助理；
- 文件整理；
- 复杂数据分析。

固定、严格、可审计的业务流程不强制套用 Deep Agents。

### 2.5 Temporal 按需后置

第一阶段不引入 Temporal。以下需求出现后再引入：

- 任务持续数小时、数天或数月；
- 跨多个企业系统；
- 服务或 Worker 崩溃后必须自动接管；
- 外部操作必须保证幂等；
- 需要复杂重试、超时、补偿或业务 SLA。

Temporal 负责宏观业务流程的可靠执行；复杂 AI 子流程继续由 LangGraph/Deep Agents 执行。简单的单次大模型调用可以直接作为 Temporal Activity。

## 3. 总体架构

```text
Web / App / Desktop
          │
          ▼
Agent Platform API（FastAPI）
企业、用户、RBAC、员工、任务、审批、审计
          │
          ▼
EmployeeRuntime 统一协议
    ├── DeepAgentRuntime
    │     └── 自主型数字员工
    └── WorkflowRuntime
          └── 流程型/混合型数字员工
          │
          ▼
LangGraph Runtime
状态、检查点、流式事件、中断、恢复、长期记忆
          │
    ┌─────┼───────────┬───────────┐
    ▼     ▼           ▼           ▼
Skills  Tool Gateway  Knowledge   Sandbox
        / MCP         Service
                       │
                       ▼
                    RAGFlow
```

## 4. 数字员工统一抽象

### 4.1 员工类型

```text
数字员工
├── autonomous：Deep Agents 自主员工
├── workflow：LangGraph 固定流程员工
└── hybrid：LangGraph 主流程 + Deep Agents 智能节点
```

`runtime_type` 仅供后端使用，不对普通用户展示。

### 4.2 EmployeeRuntime 协议

所有运行时至少实现：

```text
start()
stream()
send_message()
approve()
reject()
resume()
cancel()
get_state()
get_history()
get_artifacts()
```

客户端只依赖统一协议，不依赖 LangGraph 或 Deep Agents 的内部数据结构。

### 4.3 数字员工定义

数字员工至少包含：

- 员工 ID、名称、头像和岗位说明；
- 所属企业和可见范围；
- 运行类型；
- 模型和系统指令；
- 输入与输出 Schema；
- 可用 Skill；
- 可用工具/MCP；
- 可用知识库；
- 权限与审批策略；
- 是否支持对话、定时任务和文件上传；
- 当前版本、发布状态和灰度策略。

## 5. 统一 API 与事件

### 5.1 核心 API

```text
POST /employees/{employee_id}/runs
GET  /runs/{run_id}
GET  /runs/{run_id}/events
POST /runs/{run_id}/messages
POST /runs/{run_id}/approve
POST /runs/{run_id}/reject
POST /runs/{run_id}/resume
POST /runs/{run_id}/cancel
GET  /runs/{run_id}/artifacts
```

Web、桌面端和 App 共同使用这一套稳定接口，不直接访问底层运行时。

### 5.2 统一事件

不同运行时的内部事件统一转换为平台事件：

```text
run.started
run.progress
message.output
plan.updated
skill.loaded
tool.started
tool.completed
subagent.started
subagent.completed
approval.required
artifact.created
run.completed
run.failed
run.cancelled
```

事件至少携带 `tenant_id`、`employee_id`、`run_id`、时间、序号和事件版本。

## 6. Skill 与工具

### 6.1 Skill

Skill 遵循 Agent Skills 开放规范：

```text
skill-name/
├── SKILL.md
├── scripts/
├── references/
└── assets/
```

平台需要补充：

- Skill 注册、版本、发布和回滚；
- 企业级可见范围；
- 来源、签名和安全审核；
- Skill 与员工绑定；
- Skill 加载和执行审计。

### 6.2 Tool Gateway / MCP

数字员工不得绕过平台直接访问企业系统。所有敏感工具通过统一网关执行：

- 身份与租户校验；
- 工具级权限；
- 参数校验和数据脱敏；
- 审批策略；
- 限流、超时和重试；
- 幂等键；
- 调用审计；
- 凭据隔离。

## 7. 知识库

知识库独立为 `Knowledge Service`，Agent 不直接绑定具体实现。

统一能力包括：

- 创建知识库；
- 上传、更新和删除文档；
- 查询处理状态；
- 检索、重排和元数据过滤；
- 返回切片、分数、来源和引用；
- 知识权限校验。

默认高级实现使用 RAGFlow，适合复杂 PDF、扫描件、表格、图文混排、可视化切片和引用溯源。普通轻量场景也可以增加其他实现，但同一份知识只保留一个权威数据源。

## 8. 数据归属与唯一数据源

必须避免 Session、Thread、记忆和知识在多套框架中重复保存。

| 数据 | 唯一负责方 |
|---|---|
| 企业、用户、角色、员工定义 | 平台 PostgreSQL |
| 任务元数据和业务状态 | 平台 PostgreSQL |
| Agent 执行状态 | LangGraph Checkpointer |
| 长期记忆 | LangGraph Store |
| 企业知识 | Knowledge Service / RAGFlow |
| 上传文件和任务产物 | MinIO / S3 |
| 缓存、事件和任务协调 | Redis |
| 企业审计 | 独立审计表/审计库 |
| 技术调用链 | OpenTelemetry 后端 |

`run_id` 是平台任务主键；`thread_id` 是 LangGraph 执行线程标识。二者建立明确映射，不创建第三套会话状态。

## 9. 基础设施

### 9.1 PostgreSQL

保存：

- 企业、用户、角色和权限；
- 数字员工、版本和发布信息；
- 任务、审批和产物索引；
- 审计日志；
- LangGraph Checkpoint；
- LangGraph Store。

### 9.2 Redis

用于：

- 缓存；
- 实时事件和 SSE 协调；
- 分布式锁；
- 任务协调；
- 限流。

Redis 不作为最终业务数据的唯一存储。

### 9.3 MinIO / S3

保存：

- 用户上传文件；
- Agent 工作文件；
- PDF、Word、PPT、图片等产物；
- 大型工具输出；
- 临时文件和版本化资产。

### 9.4 API 与 Worker 分离

第一阶段保持一个代码仓库，但运行成独立进程：

```text
客户端 -> FastAPI API -> 创建任务 -> Agent Worker
                           │             │
                           └── Redis/SSE ◄┘
```

- API 负责认证、校验、任务创建和事件订阅；
- Worker 负责 LangGraph/Deep Agents 执行；
- API 与 Worker 可以独立扩容；
- 后续根据可靠性需求升级任务队列或引入 Temporal。

## 10. 观测与审计

### 10.1 OpenTelemetry

OpenTelemetry 是统一观测标准，记录：

- API 请求；
- 数字员工运行；
- LangGraph 节点；
- 模型调用、Token 和耗时；
- Tool/MCP 调用；
- RAGFlow 检索；
- 数据库和对象存储操作；
- 错误、重试和超时。

建议链路：

```text
OpenTelemetry SDK
  -> OpenTelemetry Collector
      -> Tempo / Jaeger
      -> Prometheus
      -> Loki
      -> Grafana
```

LangSmith 仅作为开发期可选的 Agent 调试和评测工具，不作为生产运行依赖。敏感 Prompt、用户文档、凭据和完整工具结果默认不得发送到外部观测平台。

### 10.2 企业审计

OpenTelemetry 不能替代审计日志。审计必须记录：

- 谁运行了哪个数字员工；
- 谁查看或修改了知识；
- 谁批准或拒绝了敏感操作；
- 数字员工调用了哪个企业工具；
- 操作前后关键业务数据；
- 执行结果和责任主体。

## 11. 安全基线

- 所有数据按 `tenant_id` 隔离；
- 认证优先接入成熟的 OIDC/企业 SSO，不自行实现密码体系；
- RBAC 控制员工、Session、知识库和工具访问；
- 高风险工具必须人工审批；
- Deep Agents 的 Shell、文件和代码执行必须位于沙箱；
- 工具凭据存入密钥服务，不进入 Prompt、日志或数据库明文；
- 模型输出视为不可信输入，不能直接成为数据库、Shell 或浏览器操作参数；
- 所有外部副作用操作必须考虑幂等、超时、重试和补偿；
- Prompt、文件和工具结果进入观测系统前必须脱敏。

## 12. 建议项目结构

```text
ai-agent-platform/
├── apps/
│   ├── api/
│   └── worker/
├── platform/
│   ├── tenants/
│   ├── users/
│   ├── employees/
│   ├── runs/
│   ├── approvals/
│   ├── permissions/
│   └── audit/
├── runtimes/
│   ├── base.py
│   ├── deep_agent.py
│   └── langgraph.py
├── skills/
├── tools/
├── knowledge/
├── memory/
├── observability/
└── tests/
```

具体创建项目时再根据 Python 工程规则和测试策略细化包结构，不在当前架构阶段锁定所有实现细节。

## 13. Web 客户端架构

### 13.1 技术栈

Web 客户端采用 React 技术体系：

- React + TypeScript：核心开发语言和 UI 框架；
- Vite：开发和构建工具；
- Ant Design：企业后台、表格、表单、弹窗、树和上传等通用组件；
- Ant Design X：对话、输入、附件、任务步骤、引用和文件产物等 AI 组件；
- React Router：客户端路由；
- TanStack Query：服务端数据、缓存和请求状态；
- Zustand：少量跨页面客户端状态；
- Zod：前端运行时数据校验；
- ECharts：统计与观测图表；
- Vitest + Testing Library：单元和组件测试；
- Playwright：项目 E2E 自动化测试。

平台是登录后的企业应用，第一阶段不需要服务端渲染和 SEO，因此使用 Vite，不引入 Next.js。若未来建设公开官网，可独立使用适合 SEO 的技术栈。

### 13.2 组件边界

组件体系统一使用 Ant Design + Ant Design X，不同时引入 shadcn/ui、MUI 等第二套基础设计系统。

Ant Design X 必须封装在平台自己的业务组件之后，例如：

```text
EmployeeConversation
RunProgress
ApprovalCard
ArtifactCard
KnowledgeSources
SkillExecution
```

业务页面只依赖平台事件和业务组件，不直接绑定 Ant Design X 的请求协议或底层事件格式，确保以后可以替换 UI 组件库而不影响后端协议。

`ThoughtChain` 等组件只展示任务计划、节点状态、工具调用和进度，不展示模型隐藏推理过程。

### 13.3 页面结构

```text
平台
├── 工作台
├── 数字员工
│   ├── 员工广场
│   ├── 我的员工
│   └── 员工详情
├── 任务中心
│   ├── 执行中
│   ├── 等待审批
│   └── 历史任务
├── 知识库
├── Skill 中心
├── 工具与 MCP
├── 审批中心
├── 文件与产物
├── 运行观测
├── 审计日志
└── 企业管理
    ├── 成员
    ├── 角色权限
    ├── 模型配置
    └── 配额
```

不同类型的数字员工对用户保持一致的产品体验。页面根据员工的输入 Schema 和能力声明动态显示聊天输入、业务表单、文件上传、审批卡片、任务进度和结果产物，不展示 `runtime_type` 等内部实现信息。

### 13.4 实时通信

前端通过统一平台 API 使用 SSE 接收任务事件，必要时再使用 WebSocket 支持高频双向交互。前端只消费本文第 5.2 节定义的平台事件，不直接消费 LangGraph 或 Deep Agents 原始事件。

### 13.5 测试策略

- 业务函数、状态和数据转换使用 Vitest；
- React 组件交互使用 Testing Library；
- 核心用户流程使用 Playwright E2E，并将测试代码纳入仓库和 CI；
- `agent-browser` 仅作为 AI 操作浏览器的“手脚”，用于开发过程中的探索、临时交互和辅助排查，不作为项目 E2E 测试框架，也不计入自动化测试覆盖。

### 13.6 前端工程结构

Web 和 Tauri 桌面端共用一个前端工程，按业务功能组织代码，并通过 `PlatformAdapter` 隔离平台差异：

```text
frontend/
├── src/
│   ├── app/
│   │   ├── providers/             # Router、Query、主题、权限等全局 Provider
│   │   ├── router/                # 路由定义和路由守卫
│   │   ├── layouts/               # 平台、认证和全屏任务布局
│   │   └── App.tsx
│   ├── features/
│   │   ├── auth/                  # 登录、OIDC 回调和当前用户
│   │   ├── dashboard/             # 工作台
│   │   ├── employees/             # 数字员工广场、详情和配置
│   │   ├── runs/                  # 任务创建、执行、历史和实时事件
│   │   ├── approvals/             # 审批中心
│   │   ├── knowledge/             # 知识库
│   │   ├── skills/                # Skill 中心
│   │   ├── tools/                 # 工具与 MCP
│   │   ├── artifacts/             # 文件与任务产物
│   │   ├── observability/         # 运行观测
│   │   ├── audit/                 # 审计日志
│   │   └── organization/          # 成员、角色、模型和配额
│   ├── components/
│   │   ├── ui/                    # 对 Ant Design 的通用薄封装
│   │   └── ai/                    # 对 Ant Design X 的平台业务封装
│   ├── api/
│   │   ├── client.ts              # Axios 实例、认证和统一错误转换
│   │   ├── streaming.ts           # SSE / Fetch Stream 客户端
│   │   └── query-client.ts        # TanStack Query 全局配置
│   ├── platform/
│   │   ├── types.ts               # PlatformAdapter 接口和公共类型
│   │   ├── index.ts               # 唯一的运行环境选择入口
│   │   ├── web.ts                 # 浏览器实现
│   │   └── tauri.ts               # Tauri 实现
│   ├── stores/                    # 仅存放跨功能的纯客户端状态
│   ├── schemas/                   # 真正跨功能复用的 Zod Schema
│   ├── hooks/                     # 真正跨功能复用的 Hook
│   ├── styles/                    # 主题 Token、全局样式和CSS变量
│   ├── assets/                    # 静态资源
│   ├── test/                      # 测试环境和公共测试工具
│   └── main.tsx
├── e2e/                           # Playwright E2E 测试
├── public/                        # 原样复制的公共静态资源
├── src-tauri/
│   ├── capabilities/              # Tauri 最小权限配置
│   ├── src/
│   │   ├── commands/              # 文件、凭据、更新等原生命令
│   │   ├── lib.rs
│   │   └── main.rs
│   ├── icons/
│   ├── Cargo.toml
│   └── tauri.conf.json
├── index.html
├── package.json
├── playwright.config.ts
├── tsconfig.json
└── vite.config.ts
```

目录约束：

- `features/` 内部就近维护页面、组件、Query、Mutation、Schema 和测试，禁止把所有业务代码堆进全局目录；
- 只有被多个功能真实复用的内容才能进入顶层 `components/`、`hooks/`、`schemas/` 和 `stores/`；
- `components/ai/` 负责把统一平台事件转换为 Ant Design X 展示，不允许业务页面直接消费 Deep Agents 或 LangGraph 原始事件；
- `api/client.ts` 负责普通 REST 请求，`api/streaming.ts` 负责 SSE/流式数据，两者职责不得混用；
- TanStack Query 管理服务端数据，Zustand 只管理纯客户端状态，禁止复制同一份服务端数据；
- `platform/index.ts` 是运行环境判断的唯一入口，业务代码不得直接导入 `@tauri-apps/*` 或访问 `window.__TAURI__`；
- `src-tauri/` 只承载桌面原生能力、权限和打包配置，不放置平台业务逻辑；
- 单元和组件测试优先与被测文件就近放置，`e2e/` 只存放跨页面的 Playwright 用户流程。

## 14. 分阶段实施

### 第一阶段：核心闭环

- 企业、用户和基础 RBAC；
- 数字员工注册和版本；
- `EmployeeRuntime`；
- 一个 Deep Agents 自主员工；
- 一个自定义 LangGraph 流程员工；
- 任务、SSE、取消、审批和恢复；
- PostgreSQL、Redis、MinIO；
- RAGFlow 接口；
- 基础 OpenTelemetry 和审计。

### 第二阶段：平台化

- Skill 注册、版本、发布和安全审核；
- Tool Gateway/MCP 管理；
- 知识权限和多知识库；
- 员工灰度发布和回滚；
- 质量评测、成本和配额；
- 更完整的沙箱和凭据管理。

### 第三阶段：规模与可靠性

- 独立任务队列和 Worker 集群；
- 崩溃接管、租约、死信和补偿；
- 大规模并发和容量治理；
- 按需引入 Temporal；
- 企业级高可用、备份和容灾。

## 15. 示例库使用原则

`awesome-llm-apps` 等仓库只作为案例和模板来源，不引入其中多套 Agent 底座。可以提取：

- 岗位 Prompt；
- 工具定义；
- 执行流程；
- 输出格式；
- RAG 和 MCP 模式。

然后统一改写为 Deep Agents Skill、自定义 LangGraph 或平台 Tool，不直接混用 Agno Agent、OpenAI Agents SDK、Google ADK、CrewAI 等运行时。

## 16. 尚未锁定的实现细节

以下内容在进入详细设计和 PoC 后决定：

- OIDC/SSO 产品；
- Redis 任务队列实现；
- 沙箱后端；
- OpenTelemetry 的具体存储与展示组件；
- RAGFlow 与轻量知识实现的切换策略；
- Temporal 的引入时机；
- 桌面端和 App 的具体技术栈。

这些选择不得改变本文确认的核心边界：Python 优先、LangGraph 统一内核、Deep Agents 自主员工、知识独立、工具统一授权、状态唯一来源、平台 API 自研。
