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

### 2.6 分阶段认证体系

平台认证不能只支持 OIDC。第一阶段同时建设本地账号体系，提供：

- 邮箱注册；
- 邮箱密码登录；
- 登录会话、退出登录和当前用户查询；
- 后续接入 OIDC / 企业 SSO 的扩展边界。

内测早期注册只校验邮箱格式和唯一性，不发送验证码或验证链接，用户提交后即可注册成功。该行为必须由明确配置项控制，例如 `REQUIRE_EMAIL_VERIFICATION=false`，不能作为无法关闭的永久逻辑。

即使暂不验证邮箱归属，也必须满足：

- 邮箱去除首尾空格并进行大小写规范化，数据库使用大小写不敏感唯一约束；
- 密码只保存经过 Argon2id 等现代密码哈希算法处理的结果，不保存明文或可逆密文；
- 注册和登录接口具备输入校验、限流和防账户枚举策略；
- 登录成功后使用可撤销、可过期的服务端 Session；
- 未验证邮箱不能作为企业归属或可信身份依据，不能仅凭邮箱域名自动加入企业；
- 将来启用邮箱验证、找回密码或绑定 OIDC 身份时，必须验证邮箱控制权，防止账号被冒领或错误合并。

公网正式开放前必须重新评估免验证策略，并具备启用邮箱验证、反滥用和账号恢复的能力。

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

有外部副作用的工具调用采用明确的崩溃安全边界：每次调用都分配唯一 `invocation_id`，Tool Gateway 必须先独立提交 `tool.started` 审计，再执行外部调用。生产数据库审计实现将 `tool.started` 作为 invocation claim：在同一事务中锁定 Run 行、检查 Run 未终止且没有未处理的 `CANCEL`，再写入 STARTED；取消 API 也必须先锁同一 Run 行，因此由锁顺序线性化“调用已开始”与“取消先发生”。调用前的普通 guard 只能作为尽力而为的快速拒绝，不能宣称消除了 TOCTOU。若 Worker 恢复时发现同一租户、Run 和 `invocation_id` 已有 STARTED 审计，但运行时检查点仍停在原审批中断，平台必须以稳定错误 `tool_execution_uncertain` 终止该 Run，绝不能自动重放。若检查点已前进到下一中断或终态，则数据库状态只向前对齐，并仅结算旧审批命令。平台内部的这套协议无法替代外部系统幂等：所有产生副作用的工具适配器仍必须把 `invocation_id` 作为外部幂等键传递，或提供等价的查询、去重或补偿能力；不具备这些能力的工具必须显式声明其“结果不确定、禁止自动重试”语义。

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

Worker 的 Redis Stream 投递重试次数以 Consumer Group 的持久 delivery count 为准，不使用进程内计数。超过有界阈值后，PostgreSQL `run_dead_letters` 是死信唯一真相源，结算采用可靠的两阶段协议，而不宣称死信记录与业务收敛跨事务原子：平台先耐久写入稳定错误类型和最小元数据；取得对应 Run 的 runtime ownership 后，再在独立结算事务中锁定 Run 行，必要时将原任务标记失败、将原命令标记已处理，并写入 `settled_run_id`。若仍有存活的外部 Worker 持有 ownership，死信保持待结算且 Redis 原消息不得 ACK，后续以同一 delivery 重入并继续幂等结算；只有死信已经结算，或畸形消息无法通过受限字段交叉验证到原 Run/Command 时，才能 ACK 原消息。Redis DLQ Stream 只提供幂等、可补偿的运维镜像。死信重放必须创建新的 Run 与 RunCommand，再由正常 Dispatcher 投递；不得复用原 command ID，也不得向终态旧 Run 追加重放命令。合法消息不复制原 payload，畸形消息只保存受限字段元数据、大小和摘要，不保存原值或异常文本。

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
- 第一阶段支持本地邮箱注册和邮箱密码登录，后续并行支持 OIDC / 企业 SSO；
- 本地密码必须使用现代密码哈希，Session 必须可撤销、可过期并通过安全 Cookie 传输；
- 免邮箱验证仅是可配置的早期策略，未验证邮箱不得用于证明企业归属或自动合并身份；
- RBAC 控制员工、Session、知识库和工具访问；
- 高风险工具必须人工审批；
- Deep Agents 的 Shell、文件和代码执行必须位于沙箱；
- 工具凭据存入密钥服务，不进入 Prompt、日志或数据库明文；
- 模型输出视为不可信输入，不能直接成为数据库、Shell 或浏览器操作参数；
- 所有外部副作用操作必须考虑幂等、超时、重试和补偿；
- Prompt、文件和工具结果进入观测系统前必须脱敏。

### 11.1 租户 RBAC 契约

后端是授权判断的唯一真相源。客户端不得根据 `owner`、`admin`、`member` 角色名自行推导按钮和操作权限，而应消费当前用户工作区响应中的 `permissions` 稳定权限码；前端隐藏入口仅改善体验，不能替代每个后端接口的权限校验。

当前权限矩阵如下：

| 稳定权限码 | Owner | Admin | Member |
|---|---:|---:|---:|
| `workspace.manage` | 是 | 否 | 否 |
| `employees.manage` | 是 | 是 | 否 |
| `knowledge.manage` | 是 | 是 | 否 |
| `skills.manage` | 是 | 是 | 否 |
| `tools.manage` | 是 | 是 | 否 |
| `operations.manage` | 是 | 是 | 否 |
| `runs.execute` | 是 | 是 | 是 |
| `runs.manage` | 是 | 是 | 否 |

租户成员身份仅提供受资源规则约束的读取能力，不代表可以读取租户内全部数据：

- Member 只能读取已发布且租户可见的数字员工；草稿、私有员工及员工版本对其隐藏；
- Member 只能读取 Skill 的已发布版本，响应不得泄漏未发布版本的描述、版本号或文件；
- Member 只能读取和控制自己创建的任务；访问其他用户任务统一按资源不存在处理；审批和拒绝需要 `runs.manage`；
- MCP Server 与 Tool 的读取和修改都需要 `tools.manage`，避免泄漏企业工具拓扑与连接信息；
- 知识库创建、上传和删除需要 `knowledge.manage`，检索和读取可由租户成员使用；
- 死信查看和重放需要 `operations.manage`。

所有租户接口先校验成员身份，再校验稳定权限码，并在查询或资源装载阶段实施行级隔离。当前阶段只定义以上三种角色及其授权行为，不在此扩展成员管理 API。

## 12. 后端代码组织边界

后端位于单仓库的 `backend/` 中。完整物理目录、文件归属、测试位置和跨工程契约以 [`project-structure.md`](project-structure.md) 为唯一权威说明，本文不重复维护目录树。

本文只规定后端逻辑边界：平台业务、Agent 运行时、Knowledge Service、Tool Gateway、记忆、沙盒、基础设施和观测必须保持职责分离；FastAPI API 与 Agent Worker 共用同一套业务模块，但作为独立进程运行。

## 13. 前端协议边界

Web、Tauri 和未来 App 共同依赖本文第 5 节定义的平台 API 与统一事件，不直接访问 LangGraph、Deep Agents、RAGFlow 或 Temporal。

前端的技术栈、分层、工程结构、状态边界、实时事件、`PlatformAdapter`、安全、测试和发布策略，以 [`docs/frontend-architecture.md`](frontend-architecture.md) 为唯一权威说明。

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

- 本地账号的邮件服务、邮箱验证和密码找回方案；
- OIDC / SSO 产品及与本地账号的绑定策略；
- Redis 任务队列实现；
- 沙箱后端；
- OpenTelemetry 的具体存储与展示组件；
- RAGFlow 与轻量知识实现的切换策略；
- Temporal 的引入时机；
- 桌面端和 App 的具体技术栈。

这些选择不得改变本文确认的核心边界：Python 优先、LangGraph 统一内核、Deep Agents 自主员工、知识独立、工具统一授权、状态唯一来源、平台 API 自研。
