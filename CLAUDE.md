# AI Agent Platform 项目规则

## 项目架构基线（强制）

每个新会话首次进入本项目时，必须完整阅读一次 `docs/project-structure.md`、`docs/backend-architecture.md` 和 `docs/frontend-architecture.md`，同一会话内无需在每次设计、实现或修改前重复读取。仅当对应文档在会话期间发生变化，或当前上下文中已无法确认其内容时，才需要重新读取。这三份文档分别是整体工程结构、后端架构和前端架构的权威说明；如果代码、临时方案或其他文档与其冲突，应先向用户说明并确认是否修改架构基线，禁止自行引入另一套技术体系。

### 整体工程结构

- 项目采用单一 Git 仓库，前端和后端必须分别位于根目录 `frontend/` 与 `backend/`；
- 前后端保持独立依赖、测试、构建和部署能力，禁止拆成两个仓库，也禁止在根目录混放业务代码；
- FastAPI API 与 Agent Worker 共用 `backend/` 的 Python 包，但作为独立进程和镜像运行；
- Web 与 Tauri 共用 `frontend/` 的 React 业务代码，桌面原生能力集中在 `frontend/src-tauri/`；
- 跨端协议产物放在 `contracts/`，内置 Skill 源文件放在根目录 `skills/`，基础设施配置放在 `infra/`；
- 完整目录、依赖方向和文件归属以 `docs/project-structure.md` 为准。

### 后端架构

- 后端统一以 Python 为主，使用 FastAPI 提供自研平台 API、SSE 和 WebSocket；客户端不得直接依赖底层 Agent 框架协议；
- 不使用 AgentOS 作为平台管理层，不为了适配第三方管理层再叠加一层 BFF；企业、租户、权限、任务、审批、知识、Skill、工具和审计能力由本平台统一实现；
- LangGraph 是唯一的 Agent 与 AI 工作流执行内核，也是运行状态的唯一来源；
- 自主型数字员工使用 Deep Agents 实现，流程型数字员工直接使用 LangGraph，混合型数字员工以 LangGraph 为主流程并在节点中调用 Deep Agents；三者对用户统一表现为“数字员工”；
- Temporal 第一阶段不引入，仅在出现跨系统、超长周期、高可靠业务编排需求后用于宏观流程；AI 子流程仍由 LangGraph / Deep Agents 执行；
- 知识库、长期记忆、Skill、Tool、凭据和权限是平台级能力，不绑定在某个数字员工或第三方框架内部；
- 第一阶段必须支持本地邮箱注册和邮箱密码登录，邮箱暂不验证归属；同时保留 OIDC / 企业 SSO 扩展能力，不得把认证体系锁死为仅支持 OIDC；
- 使用 OpenTelemetry 建立统一可观测性，平台事件协议不得直接暴露 LangGraph 或 Deep Agents 的内部事件格式。

### 前端架构

- 使用 React + TypeScript + Vite，组件体系采用 Ant Design 与 Ant Design X；
- Axios 负责 HTTP 传输，TanStack Query 负责服务端状态，Zustand 负责必要的客户端状态，Zod 负责运行时数据校验；
- 使用 React Router 管理路由，通过统一平台 API 与 SSE / WebSocket 消费后端任务和事件；
- Web 与 Tauri 复用同一套页面、组件、状态、数据模型和业务逻辑，所有平台差异必须经过下文定义的 `PlatformAdapter`；
- 前端按业务功能组织，具体目录结构、模块职责和依赖方向以 `docs/frontend-architecture.md` 为准。

## Web / Tauri 平台适配规范（强制）

**核心规则：** Web 和 Tauri 桌面端共用同一套 React 业务页面、组件、API、状态管理和数据模型。所有浏览器与桌面平台存在差异的能力，必须通过统一的 `PlatformAdapter` 接口访问。

### 适配器结构

项目必须保持类似以下结构：

```text
platform/
├── types.ts       # PlatformAdapter 接口和公共类型
├── web.ts         # 浏览器实现
└── tauri.ts       # Tauri 实现
```

`PlatformAdapter` 至少负责以下平台相关能力：

- 保存、打开和选择文件；
- 在系统文件管理器中显示文件；
- 系统通知；
- 打开外部链接；
- 安全凭据读写；
- 自动更新检查；
- 窗口、托盘和桌面端专属能力。

### 禁止事项

- 业务页面、业务组件、Store 和普通 API 模块禁止直接导入 `@tauri-apps/*`；
- 禁止在业务代码中直接访问 `window.__TAURI__`；
- 禁止在各页面散落 `isTauri`、User-Agent 或运行环境判断；
- 禁止分别维护两套 Web/Tauri 业务页面；
- 禁止把长期Token或企业敏感凭据直接存入 `localStorage`；
- 禁止为了桌面端功能破坏 Web 端的标准 HTTP/SSE 接口。

### 正确做法

- 运行环境判断集中在平台适配器初始化处；
- 业务代码只依赖 `PlatformAdapter`，不感知具体运行平台；
- Web 实现使用浏览器标准 API；
- Tauri 实现使用 Tauri Command、Plugin、Channel 和 Capability；
- 不支持的能力必须返回明确的能力状态或受控错误，不允许静默失败；
- 新增平台能力时先扩展公共接口，再分别实现 Web 和 Tauri 版本；
- 两个实现必须具有统一的输入、输出和错误语义；
- 平台适配器需要独立测试，核心能力应具有 Web/Tauri 契约测试。

### 复用边界

以下内容应在 Web 和 Tauri 中直接复用：

- React 页面和业务组件；
- Ant Design / Ant Design X 业务封装；
- Axios、TanStack Query 和 SSE 客户端；
- Zustand 状态；
- Zod Schema；
- 权限、任务、审批、知识库、Skill 和数字员工业务逻辑。

以下内容允许通过 `PlatformAdapter` 分平台实现：

- 文件系统和系统文件选择器；
- OIDC 登录回调和 Deep Link；
- 安全凭据存储；
- 系统通知；
- 自动更新；
- 窗口、菜单、托盘和全局快捷键；
- 桌面端原生流式通道。

### 后端边界

Tauri 只承载桌面客户端和必要的原生适配，不默认内置 Python Agent 后端。Web、Tauri 和后续 App 统一通过 HTTPS、SSE 或 WebSocket 访问云端 FastAPI 平台。只有明确提出离线执行需求并完成安全与升级设计后，才能引入本地 Sidecar。
