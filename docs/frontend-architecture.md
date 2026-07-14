# AI 数字员工平台前端架构

> 状态：已确认的实现基线  
> 确认日期：2026-07-12  
> 适用范围：Web 客户端、Tauri 桌面客户端及未来客户端的公共交互协议

## 1. 建设目标

建设一套面向企业用户的数字员工客户端，为不同底层运行方式提供一致体验。用户只需要理解“数字员工、任务、审批、知识、Skill、工具和产物”，不需要知道员工底层使用 Deep Agents、LangGraph 或未来的 Temporal。

前端需要满足：

- 同一套 React 业务代码同时运行于 Web 和 Tauri；
- 支持对话型、表单型和混合型数字员工；
- 实时展示任务进度、计划、工具、Skill、审批和产物；
- 支持多企业、权限隔离和企业管理；
- 对后端运行时保持解耦，只依赖平台 API 和平台事件；
- 能逐步扩展桌面原生能力，并为未来 App 保留稳定协议；
- 具备企业级错误处理、安全、观测和自动化测试能力。

## 2. 已确认的核心决策

### 2.1 技术栈

- React + TypeScript：核心开发语言和 UI 框架；
- Vite：开发服务器、资源处理和生产构建；
- Ant Design：企业后台、表格、表单、弹窗、树和上传等基础组件；
- Ant Design X：对话、输入、附件、任务步骤、引用和产物等 AI 场景组件；
- React Router：客户端路由；
- Axios：普通 HTTP 请求、认证头、超时和统一错误转换；
- TanStack Query：服务端状态、请求生命周期、缓存与失效；
- Zustand：少量跨页面、纯客户端状态；
- Zod：运行时数据校验和客户端 Schema；
- ECharts：统计、成本和运行观测图表；
- Vitest + Testing Library：单元和组件测试；
- Playwright：正式 E2E 自动化测试；
- Tauri：桌面容器和必要的系统原生能力。

平台是登录后的企业应用，第一阶段不需要 SSR 和 SEO，因此不引入 Next.js。未来公开官网应作为独立应用选择合适的 SEO 技术栈，不改变平台客户端架构。

### 2.2 Web 与桌面共用一套业务代码

Web 与 Tauri 复用：

- 页面、路由和业务组件；
- API、SSE 客户端和平台事件模型；
- TanStack Query、Zustand 和 Zod；
- 权限、任务、审批、知识库、Skill、工具和数字员工业务逻辑；
- 单元测试、组件测试及大部分 E2E 场景。

平台差异统一通过 `PlatformAdapter` 处理。Tauri 不复制一套业务页面，也不默认内置 Python Agent 后端。

### 2.3 平台协议优先

前端只依赖 FastAPI 提供的平台 REST、SSE 和必要的 WebSocket 协议：

```text
React Feature
    │
    ▼
平台 Query / Mutation / Event Model
    │
    ▼
Axios / Streaming Client
    │
    ▼
FastAPI 平台 API
```

前端禁止直接使用 LangGraph、Deep Agents、RAGFlow 或 Temporal 的内部数据结构和事件格式。

## 3. 总体架构

```text
页面与路由
    │
    ▼
Feature 业务模块
    ├── 页面与业务组件
    ├── Query / Mutation
    ├── Feature Schema
    └── Feature State
    │
    ├───────────────┐
    ▼               ▼
公共 UI / AI 组件   PlatformAdapter
    │               ├── Web 实现
    │               └── Tauri 实现
    ▼
API 与事件层
    ├── Axios REST
    ├── SSE / Fetch Stream
    └── WebSocket（按需）
    │
    ▼
统一平台 API 与事件协议
```

依赖方向必须自上而下。基础层不得反向依赖具体 Feature，Feature 之间不得通过相互导入内部文件形成隐式耦合。

## 4. 分层与职责

### 4.1 App 层

负责：

- 全局 Provider；
- 路由和路由守卫；
- 页面布局；
- 主题、国际化和全局错误边界；
- Query Client、认证和平台适配器初始化。

App 层只负责装配，不承载具体业务规则。

### 4.2 Feature 层

每个业务域独立维护：

- 页面；
- 业务组件；
- Query 和 Mutation；
- API DTO 到领域模型的转换；
- Feature 内部 Schema、Hook、状态和测试。

跨 Feature 协作优先通过路由、公共领域 ID、平台事件或明确公开的 Feature 入口完成，不得任意导入其他 Feature 的内部实现。

### 4.3 公共组件层

- `components/ui/`：对 Ant Design 的通用薄封装；
- `components/ai/`：把平台任务和事件模型转换为 AI 交互组件；
- 公共组件不得直接发起业务请求；
- 只有被多个 Feature 真实复用的组件才能进入公共层。

Ant Design X 必须封装在平台业务组件之后，例如：

```text
EmployeeConversation
RunProgress
ApprovalCard
ArtifactCard
KnowledgeSources
SkillExecution
```

业务页面不得直接绑定 Ant Design X 的请求协议。`ThoughtChain` 等组件只展示计划、节点、工具和进度，不展示模型隐藏推理过程。

### 4.4 API 与事件层

- `api/client.ts`：Axios 实例、认证、超时、取消和统一错误；
- `api/streaming.ts`：SSE 或 Fetch Stream 的连接、重连和事件解析；
- `api/query-client.ts`：TanStack Query 默认策略；
- REST DTO、平台事件和前端领域模型之间必须有明确边界；
- API 模块不直接操作 React 组件或 Zustand Store。

### 4.5 Platform 层

所有浏览器和桌面差异均封装为 `PlatformAdapter`。业务代码只依赖公共接口，不感知 Web 或 Tauri。

## 5. 前端代码组织边界

前端位于单仓库的 `frontend/` 中，与 `backend/` 保持独立依赖、测试和构建配置。完整物理目录、文件归属和跨端契约以 [`project-structure.md`](project-structure.md) 为唯一权威说明，本文不重复维护目录树。

逻辑组织约束：

- Feature 内部代码就近维护，禁止把所有业务代码堆入全局目录；
- 顶层公共目录只接收真正跨 Feature 复用的内容；
- 单元和组件测试优先与被测文件就近放置；
- `e2e/` 只保存跨页面 Playwright 用户流程；
- `src-tauri/` 只承载原生能力、权限和打包配置，不放平台业务逻辑。

## 6. 页面与信息架构

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

导航可根据租户功能开关和用户权限裁剪。无权限的入口不显示，直接访问受限路由时仍必须由路由守卫和后端共同校验。

模型配置页面面向业务用户只展示和编辑平台模型 alias，例如 `general-purpose`。前端不得暴露 LiteLLM、供应商、真实模型名、API Base URL 或密钥字段；供应商路由属于后端基础设施配置，不属于数字员工表单。

## 7. 数字员工统一交互模型

客户端依据员工定义中的能力声明和输入 Schema 动态组合界面：

- 对话输入；
- 结构化业务表单；
- 文件和附件；
- 可选知识库；
- 执行参数；
- 审批动作；
- 任务进度和产物。

自主型、流程型和混合型员工使用相同任务页面骨架。前端不得展示 `runtime_type`，也不得根据底层框架名称硬编码页面分支。

## 8. 状态管理边界

### 8.1 TanStack Query

负责所有服务端状态：

- 当前用户、企业和权限；
- 数字员工列表与详情；
- 任务、审批、知识、Skill、工具和产物；
- 分页、筛选、缓存、失效和后台刷新；
- Mutation 状态及完成后的缓存同步。

Query Key 必须由各 Feature 统一工厂生成，并始终包含必要的 `tenant_id` 或当前租户作用域，避免切换企业后缓存串用。

### 8.2 Zustand

只负责纯客户端状态，例如：

- 当前界面偏好；
- 尚未提交的复杂编辑器草稿；
- 面板展开状态；
- 跨组件但不属于服务端的数据。

禁止把 TanStack Query 已管理的服务端对象再复制到 Zustand，避免双重数据源。

### 8.3 URL 状态与组件状态

- 可分享、可返回的筛选、分页、Tab 和实体 ID 优先进入 URL；
- 只影响单个组件的短期状态保留在组件内部；
- 表单状态由表单层负责，提交成功后通过 Query 失效或精确更新同步服务端状态。

## 9. API、Schema 与错误模型

- 后端 OpenAPI 是请求和响应契约的来源；
- 可生成基础 TypeScript DTO，但生成类型不能直接替代领域模型和运行时校验；
- 外部边界的关键数据使用 Zod 校验；
- 时间、枚举、分页和错误结构在 API 层统一转换；
- UI 只处理统一的 `AppError`，不直接判断 Axios 或后端框架异常。

统一错误至少区分：

```text
authentication
authorization
validation
conflict
rate_limit
network
timeout
server
unknown
```

错误展示分为字段错误、局部错误、页面错误和全局故障，不应把所有失败都弹成全局 Toast。

## 10. 实时任务与事件流

普通资源操作使用 REST；任务执行事件默认使用 SSE。只有出现高频双向交互需求时才引入 WebSocket。

```text
创建任务 Mutation
    │ 返回 run_id
    ▼
订阅 /runs/{run_id}/events
    │
    ▼
校验事件版本与序号
    │
    ├── 更新实时任务投影
    ├── 更新进度、消息、审批和产物 UI
    └── 完成后失效相关 Query
```

前端消费的标准事件包括：

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

实时层必须处理：

- 单调事件序号和重复事件去重；
- 断线重连与最后事件位置；
- 页面刷新后通过任务快照恢复，再继续订阅；
- 任务终态后关闭连接；
- 网络离线和后台标签页；
- 未知事件版本的受控降级。

SSE 事件用于构建当前运行的前端投影，但服务端任务快照仍是刷新和恢复时的权威来源。

## 11. PlatformAdapter

接口至少覆盖：

- 保存、打开和选择文件；
- 在系统文件管理器中显示文件；
- 系统通知；
- 打开外部链接；
- 安全凭据读写；
- OIDC 回调和 Deep Link；
- 自动更新检查；
- 窗口、托盘、菜单和快捷键；
- 能力检测。

强制约束：

- `platform/index.ts` 是运行环境判断的唯一入口；
- 业务代码不得直接导入 `@tauri-apps/*`；
- 业务代码不得访问 `window.__TAURI__` 或散落 `isTauri` 判断；
- Web 和 Tauri 实现必须拥有统一输入、输出和错误语义；
- 不支持的能力返回明确能力状态或受控错误；
- 新增能力先修改公共接口，再实现两个平台版本；
- 核心能力需要 Web/Tauri 契约测试。

Tauri 只承载客户端原生适配。只有明确提出离线 Agent 执行需求，并完成沙箱、安全和升级设计后，才能引入本地 Sidecar。

## 12. 认证、权限与安全

- 第一阶段必须提供邮箱注册和邮箱密码登录页面，并保留 OIDC / 企业 SSO 登录入口的扩展位置；
- 内测早期邮箱注册提交成功后可直接登录，不要求验证码或验证链接；界面不得错误展示“邮箱已验证”；
- Web 使用后端建立的服务端 Session 和 `HttpOnly`、`Secure`、合适 `SameSite` 属性的 Cookie，React 不持久化密码或长期 Token；
- 登录、注册、退出、Session 过期和当前用户恢复统一由 `features/auth/` 管理；
- 前端需要支持后端以后开启邮箱验证，而不重写整个认证流程；
- OIDC / 企业 SSO 后续作为并行登录方式接入，不替换本地邮箱账号能力；
- Tauri 的长期凭据必须使用系统安全存储，不能放入 `localStorage`；
- 前端权限只负责体验裁剪，后端必须再次授权；
- 切换企业时清理或隔离租户相关缓存、流式连接和草稿；
- 富文本、Markdown、模型输出和外部链接均按不可信内容处理；
- 禁止渲染未经净化的 HTML；
- 文件上传校验类型、大小和数量，并展示后端扫描与处理状态；
- 日志、埋点和错误上报不得包含凭据、完整 Prompt 或敏感文件内容。

## 13. 设计系统与可访问性

- 统一使用 Ant Design Token 和平台语义 Token，不在业务组件散落魔法颜色；
- 不同时引入 MUI、shadcn/ui 等第二套基础设计系统；
- 密度、字号、间距、圆角、状态色和暗色模式由主题集中管理；
- 键盘导航、焦点、表单标签和对比度满足企业应用基本可访问性要求；
- 加载、空状态、无权限、错误、部分成功和任务取消均有统一表达；
- AI 生成内容与系统确定性数据在视觉上可区分；
- 高风险 Tool 操作必须明确展示对象、参数摘要、影响和审批动作。

## 14. 性能与可靠性

- 路由和重型 Feature 按需加载；
- 大型表格、日志和长消息使用分页、虚拟化或增量渲染；
- 流式事件按帧或小批次合并更新，避免每个 Token 触发整页重渲染；
- Query 使用合理的 `staleTime`、取消和去重，避免页面切换产生请求风暴；
- 上传和下载支持进度、取消及失败重试；
- Error Boundary 隔离全局、路由和高风险组件故障；
- 任务运行不依赖页面常驻，刷新或关闭页面后可依据 `run_id` 恢复；
- 第一阶段不承诺完整离线业务能力，只提供明确离线状态和恢复策略。

## 15. 前端可观测性

前端观测至少记录：

- 页面和核心交互性能；
- API 请求结果、耗时和错误分类；
- SSE 连接、重连、事件延迟和事件版本错误；
- JavaScript 异常和 React Error Boundary；
- Web/Tauri 平台及客户端版本；
- 与后端对应的 `request_id`、`run_id` 和脱敏后的租户上下文。

前端日志和后端 OpenTelemetry Trace 应能通过关联 ID 串联，但不得上传敏感输入和模型完整输出。

## 16. 测试策略

### 16.1 单元测试

使用 Vitest 测试：

- 领域转换和 Zod Schema；
- Query Key、缓存更新和事件 Reducer；
- 权限判断；
- PlatformAdapter 公共契约；
- 错误和协议兼容逻辑。

### 16.2 组件测试

使用 Testing Library，从用户行为角度测试：

- 动态员工输入；
- 任务进度和流式消息；
- 审批卡片；
- 错误、权限和空状态；
- 文件上传和产物展示。

### 16.3 E2E

使用 Playwright 覆盖核心用户旅程：

- 登录与租户切换；
- 选择数字员工并创建任务；
- 接收实时进度和结果；
- 人工审批、中断、恢复和取消；
- 上传知识文档并查看处理状态；
- 下载或打开任务产物；
- 权限拒绝和会话过期。

测试代码必须纳入仓库和 CI。`agent-browser` 仅作为 AI 操作浏览器的手脚，用于探索、临时交互和辅助排查，不是项目 E2E 框架，也不计入覆盖率。

## 17. 构建、配置与发布

- Vite 环境变量只存放可公开的构建配置，不得包含密钥；
- 运行时 API 地址、功能开关和租户品牌配置应有明确加载策略；
- Web 构建产物作为静态资源部署，并由独立环境配置连接 FastAPI；
- Tauri 复用同一前端构建，增加签名、Capabilities、更新通道和平台安装包；
- Web 与 Tauri 使用相同的平台 API 版本和兼容策略；
- CI 至少执行类型检查、Lint、单元测试、组件测试、构建和 Playwright 核心流程；
- 桌面发布必须具备签名、版本、升级和回滚策略。

## 18. 未来 App 边界

未来移动 App 继续复用：

- 平台 REST / SSE / WebSocket 协议；
- OpenAPI DTO 和领域 Schema 的可移植定义；
- 统一认证、权限、任务和事件语义；
- 产品信息架构和设计 Token。

不预设移动端必须直接复用 React DOM 组件。移动 App 的具体技术栈在需求明确后决定，不能为了未知的代码复用提前破坏 Web/Tauri 架构。

## 19. 分阶段实施

### 第一阶段：客户端骨架

- React、Vite、路由、主题和基础布局；
- Axios、TanStack Query、邮箱注册登录、Session 恢复和错误模型；
- PlatformAdapter Web/Tauri 骨架；
- 数字员工、任务详情和基础 SSE 闭环；
- Vitest、Testing Library 和 Playwright 基线。

### 第二阶段：平台业务

- 动态输入 Schema 和完整任务交互；
- 审批、知识库、Skill、工具与 MCP；
- 文件产物、运行观测和企业管理；
- Tauri 文件、凭据、通知和更新能力；
- 完整权限、可访问性和前端观测。

### 第三阶段：规模与体验

- 长任务和大数据量性能优化；
- 多窗口、托盘、Deep Link 等桌面体验；
- 主题、国际化和租户品牌；
- 更完整的协议兼容、灰度和故障恢复；
- 根据真实需求决定移动 App 技术栈。

## 20. 尚未锁定的实现细节

- 邮箱验证、密码找回和反滥用交互；
- OIDC / SSO 产品及本地账号绑定交互；
- 包管理器和 Monorepo 工具；
- OpenAPI TypeScript 代码生成工具；
- 表单 Schema 到 UI 的具体映射库；
- 前端错误采集与性能平台；
- 国际化方案；
- 移动 App 技术栈。

这些选择不得改变本文确认的核心边界：React + TypeScript + Vite、Ant Design / Ant Design X、服务端状态与客户端状态分离、平台事件解耦、Web/Tauri 业务代码复用、平台差异统一经过 `PlatformAdapter`、Playwright 作为 E2E 框架。
