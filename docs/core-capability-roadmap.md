# AI 中台 Core 能力建设路线图

> 文档性质：Core 缺口清单、依赖图、推荐实施波次与完成状态的唯一执行台账
> 建立日期：2026-07-14
> 当前阶段：基础能力补全
> 适用范围：AI 中台 Core；不包含 `video-studio`、`social-operations` 等客户可选能力包

## 1. 目标与边界

本项目采用“稳定 AI 中台 Core + 可插拔行业能力包 + 客户解决方案包”的架构。本路线图负责把当前可运行的自主型数字员工 Web Demo 补全为可通过 Tauri 交付、可由企业持续使用、可观测、可授权和可运维的 AI 中台底座。

视频剪辑、自动运营及其 RPA Sidecar 不属于 Core。Core 按本文声明的依赖 DAG 分波次受控并行，C 编号用于稳定标识而不是绝对串行关系；C01 完成后，行业能力包可在独立分支/工作树按自身依赖路线并行开发。业务包只能通过稳定公开协议和测试替身对接未完成能力，不能复制 Core，也不能在对应 Core 门禁完成前宣称集成完成。

## 2. 状态和更新规则

状态统一使用以下标记：

| 状态 | 含义 |
| --- | --- |
| `⬜ 未开始` | 尚未进入实现，允许补充调研和验收标准 |
| `🚧 进行中` | 当前正在实施；同一时间最多两个满足并行门禁的 Core 条目 |
| `🧪 待集成` | 自身实现与隔离测试已通过，等待明确记录的 Core 集成或真实平台门禁 |
| `⛔ 受阻` | 已有可复现的外部阻塞，并记录原因、证据和解除条件 |
| `✅ 已完成` | 全部完成定义、自动化测试和真实验收均已通过 |

执行要求：

1. 按第 4 节依赖 DAG 和推荐波次实施；编号不是依赖，明确列出的前置条件才是依赖；
2. 同时最多实施两个互不构成直接依赖、主要修改边界可隔离的 Core 条目；开始前分别更新为 `🚧 进行中`并记录开始日期；
3. 代码存在不等于完成，必须逐项满足该条目的“完成定义”；
4. 有用户界面的能力必须包含正式 Playwright E2E；纯后端能力必须包含真实依赖集成/API 测试；
5. 相关测试、类型检查、静态检查和构建全部通过后，才能改为 `✅ 已完成`；
6. 完成状态更新必须与该能力实现放在同一个任务提交中，记录完成日期、提交标识和验证命令；提交标识可以写“本任务提交”，精确哈希由 Git 历史追溯，禁止为了把提交自身的哈希写回文档制造循环提交；
7. 新发现的 Core 缺口必须补进本文，并放在依赖关系正确的位置；
8. 不得删除未完成项来制造“全部完成”，正式取消范围必须记录决策依据；
9. 每个阶段完成后运行全量回归，并更新第 6 节验证基线；
10. 文档状态与代码冲突时，以可运行代码和测试证据为事实，并立即修正文档。

## 3. 当前能力盘点

### 3.1 已形成真实闭环的能力

- 本地邮箱注册、密码登录、HttpOnly Session 和登录限流；
- 企业/工作区数据隔离、Owner/Admin/Member 基础 RBAC；
- 数字员工草稿、发布版本和自主型 Deep Agents 运行时；
- LangGraph Checkpoint、任务状态、平台事件、SSE 和任务控制；
- PostgreSQL Run/Command Outbox、Redis Stream、Dispatcher 和 Worker；
- 运行时租约、崩溃恢复、死信结算、补偿和安全重放；
- RAGFlow 数据集、文档上传、解析启动、列表和独立检索；
- Skill 注册、版本、发布、对象存储和运行时物化；
- MCP Server、Tool Registry、Tool Gateway、风险审批和工具调用审计；
- 本机 Docker Sandbox Controller、租约清理和安全资源限制；
- LiteLLM 官方镜像、稳定模型别名和阿里百炼 `qwen-plus` 真实推理调用；
- OpenTelemetry Trace 和本机 Compose 基础设施；
- React Web 的认证、工作区切换、员工、任务、知识、Skill、Tool 和死信页面。

### 3.2 仍不完整或尚未实现的能力

| 能力域 | 当前状态 | 主要缺口 | 对应任务 |
| --- | --- | --- | --- |
| 工程质量基线 | 已完成 | 后端 0 失败；真实依赖跳过项均明确标注所需外部依赖 | C01 |
| Tauri 桌面客户端 | 已完成 | 共享 React、`PlatformAdapter`、macOS/Windows 构建和真实桌面 E2E 已落地 | C02 |
| 全栈真实验收与工作台 | 已完成 | 本地 Stub 成功/受控失败整栈闭环、工作台真实数据、Tauri 内核心流程及百炼真实请求均已通过 | C03 |
| 文件与产物 | 已完成 | 租户隔离的附件、沙箱物化、持久产物目录与客户端闭环已落地 | C04 |
| 多轮会话 | 待集成 | 已具备会话/消息/任务关系、追加输入、错误重试、权限隔离和正式 E2E；活跃任务后的自动排队续跑与会话页直接取消体验仍需主线收口 | C05 |
| 动态输入输出 | 未实现 | Schema 只存储，未驱动表单、校验和结构化结果展示 | C06 |
| 知识运行时 | 部分完成 | 知识库能独立检索，但未形成员工绑定和 RAG 注入闭环 | C07 |
| Skill 生命周期 | 部分完成 | 缺安全审核、下线/删除、内置安装和完整运维能力 | C08 |
| Tool/MCP 生命周期 | 部分完成 | 缺自动发现同步、连接测试、编辑删除和凭据产品化 | C09 |
| 长期记忆 | 未实现 | 无 Memory 领域、数据库、API、运行时和用户管理能力 | C10 |
| 工作流/混合员工 | 未开放 | 前端强制禁用，生产 Workflow Registry 没有可用流程 | C11 |
| 定时任务 | 未实现 | 配置字段存在但强制关闭，无调度、历史和失败治理 | C12 |
| 审批中心 | 部分完成 | 只有任务详情控制，无独立审批记录、待办、超时和通知 | C13 |
| 审计与可观测性 | 部分完成 | 审计只覆盖工具；只有 Trace，无 Metrics/Logs/告警 | C14 |
| 企业与账号管理 | 部分完成 | 缺成员邀请、角色管理、密码找回、验证、SSO/MFA | C15 |
| 模型治理、评测、成本与配额 | 部分完成 | 只有共享 Worker Key，无租户模型、预算、质量评测和用量中心 | C16 |
| Capability/Entitlement | 未实现 | 无能力注册表、企业授权、交付 Profile 和三层启用校验 | C17 |
| 生产凭据与沙箱 | 部分完成 | 本地凭据和 ARM64 开发沙箱不能作为生产多租户方案 | C18 |
| 协议契约自动化 | 部分完成 | 缺 OpenAPI 快照、TS 生成、事件全量导出和漂移检查 | C19 |
| 发布、升级与灾备 | 未实现 | 缺签名、公证、自动更新、灰度、备份、恢复、HA 和容灾 | C20 |

## 4. 依赖图与推荐实施波次

### 4.1 并行门禁

- C01-C04 构成当前基础纵切，保持顺序完成；从 C04 完成后开始按依赖 DAG 分波次并行；
- 同一直接依赖链严格串行；无直接依赖的条目只有在独立工作树、独立提交、独立测试和高冲突文件唯一写入方均已明确后才能并行；
- 数据库迁移编号、共享 API/事件契约、根依赖锁文件、Tauri 主壳、公共前端导航和组合 Profile 由主代理指定唯一写入方；
- 自身实现已通过但集成前置尚未完成的条目只能标记 `🧪 待集成`；所有完成定义和真实门禁满足后才能标记 `✅ 已完成`；
- 推荐每波采用“两个 Core”或“一个 Core + 两个行业条目”，为主代理保留冲突协调、审查和最终验收能力；不得为了填满并发槽位启动没有清晰验收边界的任务。

### 4.2 直接依赖与推荐波次

| 条目 | 开工直接前置 | 允许提前隔离开发但完成前仍需 | 推荐并行说明 |
| --- | --- | --- | --- |
| C01 → C04 | 前一编号 | — | 基础纵切保持串行 |
| C05 | C04 | — | 可与 C08、C13 或 C14 中一个并行 |
| C06 | C04 | C05 的会话/附件消息契约 | 避免与 C05 同时修改任务表单和运行入口 |
| C07 | C04 | — | Knowledge Profile 独立，可与会话或治理条目并行 |
| C08 | C03 | — | Skill 边界独立，优先作为并行条目 |
| C09 | C03 | C13 审批协议、C18 生产凭据 | 可先完成生命周期与测试替身，缺集成时标记待集成 |
| C10 | C05 | — | 多轮记忆链，C05 未完成前不得开工 |
| C11 | C05、C06 | C13 审批协议 | 固定/混合工作流在输入与会话契约稳定后实施 |
| C12 | C03 | C16 配额接入 | 调度主链可独立开发，C16 完成后补配额门禁 |
| C13 | C03 | — | 审批公共层，可与 C05、C07 或 C08 并行 |
| C14 | C03 | — | 跨模块治理面较宽，并行时由主代理独占共享接线文件 |
| C15、C16、C17 | C14 | — | 三者互不直接依赖，可分波次并行；共享导航和审计接线唯一写入 |
| C18 | C14、C17 | — | 生产安全基线，不与 C20 倒置 |
| C19 | C17 | C18 的生产凭据/沙箱契约 | 可先生成稳定契约，最终快照在 C18 后复核 |
| C20 | C01-C19 全部完成 | — | 最终发布与容灾条目，保持收口串行，不以发布排除项降低 Core 完成范围 |

推荐起始波次：C04 完成后先并行 `C05 + C08`；随后根据可用并发在 `C06`、`C07`、`C13`、`C14` 中选择无冲突组合。每次开工前必须基于最新代码重新做依赖和冲突检查，表格不是跳过检查的授权。

**以下各条目保留稳定编号，其完成定义不因并行策略而降低。**

### C01 工程质量基线归零

**状态：`✅ 已完成`**

**开始日期：2026-07-14**

**完成日期：2026-07-14**

完成定义：

- 修复 Worker 未配置时错误顺序测试，配置错误必须早于数据库连接错误暴露；
- 后端全量 Pytest 达到 0 失败，所有跳过项有明确的外部依赖原因；
- Ruff、Mypy、前端 Vitest、Lint、Typecheck 和 Build 全部通过；
- 固化一组无需付费模型调用的默认回归入口；
- 更新第 6 节基线并记录验证命令。

### C02 Tauri 客户端骨架与 PlatformAdapter

**状态：`✅ 已完成`**

**开始日期：2026-07-14**

**完成日期：2026-07-14**

完成定义：

- 建立 `frontend/src-tauri/`、Tauri 配置、Rust 入口和开发/构建命令；
- 建立 Web/Tauri 共用的 `PlatformAdapter` 契约及两个实现；
- 接通安全凭据、文件选择/保存、外部链接、系统通知和能力探测；
- 业务页面不直接依赖 `@tauri-apps/*`，Web 业务页面保持复用；
- macOS 桌面开发版可启动，Windows 构建链路具备自动化编译验证；
- Vitest、Playwright Web 业务回归、PlatformAdapter 契约测试和 Tauri Rust 测试通过；
- 使用 WebdriverIO 与 `@wdio/tauri-service` 建立真实 Tauri 桌面 E2E，覆盖应用启动、IPC 和代表性原生桥接；测试驱动只进入测试构建；
- macOS 本机桌面 E2E 与冒烟通过，Windows 在 CI 或 Windows 设备完成构建和桌面冒烟；具体分层遵循 [`tauri-testing-strategy.md`](tauri-testing-strategy.md)。

### C03 本机完整栈、真实 AI 链路与工作台

**状态：`✅ 已完成`**

**开始日期：2026-07-14**

完成定义：

- MVP Profile 一次启动 PostgreSQL、Redis、MinIO、LiteLLM、API、Dispatcher、Worker、Sandbox 和客户端；RAGFlow 在 C07 的 Knowledge Profile 单独启用；
- 完整经过注册、登录、发布员工、发起任务、百炼推理、事件持久化和 UI 终态；
- 正式自动化回归使用本地 Stub，另保留显式启用的百炼最小真实烟测；
- 工作台展示员工、任务、失败状态和系统健康等当前阶段已有的真实数据；C04、C13、C16 完成时再分别增加最近产物、待审批和模型用量卡片；
- Tauri 内完成核心流程，不要求用户切回浏览器；
- 完整 Playwright 和真实运行时 E2E 通过，测试结束自动清理本轮服务。

### C04 文件上传、任务工作区与产物系统

**状态：`✅ 已完成`**

**开始日期：2026-07-15**

**完成日期：2026-07-15**

**质量收口日期：2026-07-16**

完成定义：

- 建立文件、任务附件和 Artifact 领域模型、迁移、仓储与 API；
- 建立供应商无关 `ArtifactStorageProvider`；MinIO 用于本地开发，腾讯云 `TencentCosArtifactProvider` 兼容 LighthouseCOS，数据库只保存稳定元数据与权限关系；
- 员工任务支持文件输入，沙箱可在授权范围读取，Agent 可创建产物；
- `EmployeeRuntime.get_artifacts()` 返回真实数据；
- 客户端支持选择、上传、预览、下载、定位和删除；
- 跨数据库与对象存储变更通过持久操作日志、协调器重放和取消归并形成可恢复 Saga；
- 覆盖租户隔离、大小/类型/内容限制、并发事件序号、失败恢复、恶意路径和权限 E2E。

2026-07-16 完成最终质量收口：上传请求体在 multipart 解析和认证前即受控限流，重复/伪造长度头失败关闭；API、Controller 与真实 Sandbox 统一为 25 MiB；未绑定文件具备客户端补偿和服务端 TTL 两级回收，清扫成功或失败都受固定间隔节流；任务提交同时具备同步互斥和服务端幂等键，任务意图改变会生成新键；对象存储 SDK 的单次调用与重试受操作期限约束，迟到 put tombstone 在持久观察窗口内重扫，最终删除失败也会记录并退休；首次附件物化异常或取消都会删除新建 Sandbox/lease。本轮质量收口新增 `20260716_0020` 前向迁移，C04 既有领域建模迁移 `20260716_0018`/`20260716_0019` 保持可升级、可降级。

### C05 多轮会话与追加输入

**状态：`🧪 待集成`**

**开始日期：2026-07-16**

**待集成日期：2026-07-16**

完成定义：

- 建立会话、消息、任务和线程之间的稳定关系；
- 支持运行前后追加消息、`waiting_for_input` 提交真实内容和会话恢复；
- 提供会话列表、消息时间线、附件消息和错误重试；
- 上下文裁剪、重启恢复、跨任务续聊和取消语义明确；
- 非管理员只能访问自己的会话，管理员权限有服务端校验；
- 多轮对话 API、运行时和 Playwright E2E 通过。

2026-07-16 待集成记录：已新增 `20260716_0021` 会话迁移，建立 `conversations`、`conversation_messages` 与 `runs.conversation_id`，真实 PostgreSQL 升级已由 Playwright global setup 验证；后端契约覆盖新建/列表/详情/追加消息、`waiting_for_input` 真实 `MESSAGE` command、失败重试、上下文裁剪和 Owner/Admin/Member 访问隔离；Worker 会把 `message.output` 和失败事件落回会话时间线；前端提供会话中心、详情消息时间线、员工详情“开始会话”、追加输入、附件 ID 展示和失败重试；正式 Playwright 覆盖“发布员工 → 开始会话 → 追加两轮输入 → 回到会话中心持久列表”。

待主线收口风险：活跃 `queued/running/waiting_approval` 任务期间追加消息当前持久化为 `queued_after_current`，尚未自动在当前任务终结后派生下一轮 Run；会话页展示关联任务和失败重试，但取消仍依赖既有任务详情控制入口，后续应补会话内直接取消/跳转体验后再转为 `✅ 已完成`。

### C06 动态输入 Schema 与结构化输出

**状态：`⬜ 未开始`**

完成定义：

- 员工 `input_schema` 驱动字符串、数字、布尔、枚举、日期、数组和文件表单；
- 前端 Zod 与后端 JSON Schema 执行一致的运行时校验；
- `output_schema` 驱动结构化结果卡片、表格、JSON 和导出；
- Schema 版本随员工发布版本固定，旧任务可继续解释；
- 无效 Schema、超大输入和不兼容输出有受控错误；
- 契约、组件和真实任务 E2E 通过。

### C07 知识库完整生命周期与运行时 RAG

**状态：`⬜ 未开始`**

完成定义：

- 员工编辑器可选择有权限的知识库，发布时校验引用；
- Worker 通过 Knowledge Service 检索并将结果注入运行时；
- 输出事件和界面展示可追溯引用，不暴露越权片段；
- 支持文档解析进度、失败重试、删除、更新和批量导入；
- 支持多知识库、元数据过滤、召回参数和重排配置；
- RAGFlow 断连、畸形响应、租户隔离和引用 E2E 通过。

### C08 Skill 完整生命周期与安全审核

**状态：`⬜ 未开始`**

完成定义：

- 支持 Skill 草稿、版本、审核、发布、下线、删除和回滚；
- 内置 Skill 可从根目录 `skills/builtin/` 幂等安装；
- 增加压缩包、路径、脚本、依赖、大小和危险内容审核；
- 已发布版本被员工引用时具备稳定兼容和删除保护；
- 提供安全审核结果、版本差异和使用关系界面；
- 生命周期、物化、沙箱执行和租户隔离测试通过。

### C09 Tool/MCP 完整生命周期与凭据产品化

**状态：`⬜ 未开始`**

完成定义：

- MCP Server 支持连接测试、工具自动发现、同步、编辑、禁用和删除；
- Tool Schema、风险等级和审批策略可校验、更新和回滚；
- 凭据通过平台凭据服务配置，只向授权执行器短时解析；
- 工具调用超时、重试、熔断、错误转换和审计完整；
- 客户端可查看连接状态、同步差异、调用记录和失败原因；
- HTTP/stdio、恶意 Server、凭据脱敏和审批 E2E 通过。

### C10 平台级长期记忆

**状态：`⬜ 未开始`**

完成定义：

- 建立企业、用户、员工、会话等命名空间的 Memory 模型和 API；
- 支持记忆提取、写入、检索、更新、删除、过期和禁用；
- 记忆与 LangGraph Checkpoint 职责分离；
- 运行时按权限读取和写入，用户可查看和纠正个人记忆；
- 敏感信息、租户越权、提示注入和错误记忆有治理策略；
- 隔离、召回、删除、恢复和多轮使用 E2E 通过。

### C11 固定工作流与混合型数字员工

**状态：`⬜ 未开始`**

完成定义：

- 建立 Workflow 定义、版本、注册、发布、回滚和稳定引用；
- 支持节点、条件分支、重试、子流程、Interrupt 和人工节点；
- 固定流程型员工直接使用 LangGraph，混合型在节点中调用 Deep Agents；
- 编辑器开放 `workflow` 和 `hybrid`，禁止引用未注册流程；
- 任务、事件和状态仍只暴露统一平台协议；
- 两种员工代表性端到端流程通过。

### C12 定时与预约任务

**状态：`⬜ 未开始`**

完成定义：

- 支持 Cron、单次预约、时区、启停和下次执行时间；
- 调度产生正常 Run/Command，不建立旁路执行体系；
- 支持错过执行、并发策略、重试、暂停和历史；
- 已有权限和员工发布状态在每次调度时重新校验；C16 引入配额后同步把配额校验接入调度入口；
- 客户端提供创建、编辑、暂停和执行记录；
- 时区、重启恢复、重复触发和权限 E2E 通过。

### C13 独立审批中心

**状态：`⬜ 未开始`**

完成定义：

- 建立 Approval 记录、状态、审批人/角色、风险和业务上下文；
- 提供待办列表、详情、批准、拒绝、理由、转交和历史；
- 支持超时、撤回、重复请求幂等和系统通知；
- Tool 审批、工作流审批和未来能力包审批复用同一平台协议；
- 审批决定与 Run、Tool Invocation、用户和审计记录可追溯；
- 并发审批、越权、过期和 Playwright E2E 通过。

### C14 全平台审计、Metrics、Logs 与告警

**状态：`⬜ 未开始`**

完成定义：

- 建立通用平台审计协议、存储、查询和扩展接口，先覆盖当前已有认证、成员、权限、员工、任务、知识、Skill 和 Tool；
- 审计支持租户隔离、筛选、导出、保留和敏感字段脱敏；
- OpenTelemetry 同时接入 Trace、Metrics 和结构化 Logs；
- 建立 API、Worker、队列、模型网关、RAGFlow、沙箱和客户端关键指标；
- 提供运维 Dashboard、告警规则、关联 ID 和故障定位入口；
- 后续 C15-C17 新增的企业管理、模型治理和能力授权必须在各自任务中接入同一审计协议；
- 审计不可抵赖、日志脱敏、指标和告警测试通过。

### C15 企业、成员与完整账号体系

**状态：`⬜ 未开始`**

完成定义：

- 支持企业设置、成员邀请、列表、角色变更、移除和 Owner 转移；
- 支持用户资料、修改密码、邮箱验证、找回密码和 Session/设备管理；
- 接入 OIDC/企业 SSO 扩展边界，并为高风险操作预留 MFA；
- 支持自定义角色或权限组时仍保持资源级服务端授权；
- 全部管理操作接入 C14 平台审计，禁止仅依赖前端隐藏；
- 邀请、角色、账号恢复、会话撤销和越权 E2E 通过。

### C16 模型治理、质量评测、成本与配额

**状态：`⬜ 未开始`**

完成定义：

- 平台管理供应商、模型别名、可用模型、fallback、超时和重试；
- LiteLLM Key 从应用级共享升级为可归因、可撤销的企业/工作负载凭据；
- 记录模型、Token、延迟、错误、费用和任务归属；
- 支持企业预算、用户/员工配额、限流和用量告警；
- 建立固定数据集、回归评测、人工反馈和版本对比；
- 客户端提供模型、用量、成本、预算和评测页面；
- 模型配置、凭据、预算和配额变更接入 C14 平台审计；
- 供应商故障、配额耗尽、fallback 和账单归因测试通过。

### C17 Capability Registry、Entitlement 与交付 Profile

**状态：`⬜ 未开始`**

完成定义：

- 建立能力清单、版本、后端路由、Worker、前端入口和健康检查注册协议；
- 建立企业 Entitlement、有效期、来源和授权变更审计；
- 最终可用性统一为 `deployment_installed && tenant_entitled && user_permitted`；
- 前端菜单、路由和懒加载由服务端裁剪后的能力清单驱动；
- 建立 Core-only、Core+视频、Core+自动运营和目标客户组合 Profile；
- 未安装/未授权能力无法调用 API、调度 Worker、下载 Sidecar 或签发云凭据；
- 能力授权、到期和交付 Profile 变更接入 C14 平台审计；
- 组合矩阵和后端越权测试通过。

### C18 生产凭据、生产沙箱与跨平台执行基线

**状态：`⬜ 未开始`**

完成定义：

- 接入 Vault/KMS/云密钥服务，支持加密、轮换、撤销和访问审计；
- Tauri 使用系统安全凭据存储，禁止长期 Token 落入 `localStorage`；
- Demo 明文密钥全部废止；正式交付分支、配置和制品不得继续包含，已经进入 Git 历史的泄漏凭据通过禁用和轮换处置；
- 生产沙箱使用独立隔离服务，具备租户隔离、网络策略和容量治理；
- 本地开发沙箱覆盖 ARM64/AMD64，并明确 macOS/Windows/Linux 支持矩阵；
- 凭据泄漏、跨租户访问、镜像供应链和沙箱逃逸防护测试通过。

### C19 OpenAPI、事件与客户端契约自动化

**状态：`⬜ 未开始`**

完成定义：

- FastAPI/Pydantic 自动导出并提交 OpenAPI 稳定快照；
- 自动生成 TypeScript DTO/客户端，业务层通过 Adapter 使用；
- 导出完整平台事件和能力清单 JSON Schema；
- CI 检查生成产物漂移、破坏性变更和兼容期；
- Web/Tauri/后续 App 只依赖平台契约，不依赖框架内部类型；
- 契约回放、旧客户端兼容和升级测试通过。

### C20 安装包、自动更新、发布、备份与容灾

**状态：`⬜ 未开始`**

完成定义：

- 生成 macOS 和 Windows Tauri 安装包，完成签名、公证和来源校验；
- 建立受签名自动更新、灰度、回滚和版本兼容策略；
- 建立 API、Worker 和基础设施正式部署清单与容量基线；
- PostgreSQL、MinIO、RAGFlow、LiteLLM 配置具备备份和恢复演练；
- 建立高可用、健康检查、故障切换、灾备目标和操作手册；
- Core-only 全量自动化、安装升级、回滚和恢复演练全部通过。

## 5. Core 与能力包的并行边界

C01 完成并建立质量基线后，以下能力包可以在独立分支/工作树与 Core 主线并行开发：

1. `video-studio`：LighthouseCOS/COS 素材、自研 Timeline、腾讯云 MPS、模板、剪辑任务和成片；
2. `social-operations`：平台账号、浏览器自动化、本地 RPA、微信、朋友圈、私信、发布和曝光；
3. 客户解决方案包：AI 角色、工作流、提示词、知识绑定、审批规则、品牌和业务参数。

并行只放开业务包自身领域模型、Adapter、Sidecar、UI 模块和隔离测试。业务包依赖的 Tauri、Artifact、审批、审计、Capability/Entitlement、凭据和发布能力仍分别由 C02、C04、C13、C14、C17、C18、C20 提供；对应 Core 条目未完成时，业务条目只能标记为“待集成”，不得复制底座或降低验收标准。Core-only 回归始终是主线合并门禁。

视频剪辑与自动运营的完整功能清单、证据等级、B01-B17 实施顺序和完成状态，统一维护在 [`industry-capability-roadmap.md`](industry-capability-roadmap.md)。

腾讯云最小成本部署、当前 Lighthouse 实测资源、RAGFlow 延后方案和扩容门槛，统一维护在 [`tencent-cloud-mvp-deployment.md`](tencent-cloud-mvp-deployment.md)。

能力包必须复用 Core 的用户、权限、任务、审批、产物、模型、知识、Skill、Tool、审计和客户端骨架，不得复制底座。

## 6. 当前验证基线

基线日期：2026-07-16。

| 验证项 | 当前结果 |
| --- | --- |
| 后端 Pytest | 默认环境收集 958 项：919 通过、39 跳过、0 失败；39 个条件跳过均明确标注缺少 PostgreSQL、Redis、MinIO、破坏性本地 Docker 沙箱或显式真实腾讯云 COS 凭据，不计作对应真实依赖验收通过 |
| 后端 Unit + Contract | 780 项通过；新增覆盖前置请求体限流与重复长度头、9 MiB/25 MiB 上传到物化、未绑定文件补偿/TTL 节流、Run 幂等与任务意图换键、SDK 硬超时/有界 tombstone 退休、Worker 首次物化异常/取消回收和 CORS 幂等头，并保留既有 Saga phase/lease/CAS/heartbeat、取消与提交失败回归 |
| C04 真实依赖专项 | `bash infra/platform/test-c04-artifacts.sh` 先执行 46 项 C04 单元/契约/迁移门禁并按条件跳过 1 项无显式凭据的真实 COS 测试，再通过 1 项真实 Docker Sandbox 25 MiB 边界测试，然后以随机端口启动 PostgreSQL、Redis、MinIO、LiteLLM Stub、API、Dispatcher、Worker、Sandbox Controller/Janitor 和 Web。正式无头 Playwright 3 项通过；附件场景在上传请求被延迟时同步双击并断言仅 1 次上传、1 个 Run，随后真实 Agent 在实际 Sandbox 读取附件、发布产物并完成预览、下载、刷新、定位和删除。真实 PostgreSQL Saga 并发 2 项通过；随机 profile 容器、网络、Volume 均为 0，未触碰运行中的 `agent-platform-dev` 12 个服务 |
| Ruff | 通过 |
| Mypy | 170 个源码文件通过 |
| 前端 Vitest | 28 个测试文件、119 项测试通过；新增同步双击互斥、上传后 Run 失败补偿、任务意图换键和幂等请求头契约 |
| 前端 Lint | 通过 |
| 前端 Typecheck | 通过 |
| 前端 Build | 通过，存在单个 500KB 以上分包警告 |
| Playwright Web 业务回归 | 15 项完整回归通过；PostgreSQL、Redis、MinIO 与 API 均支持测试进程传入的随机隔离端口，C04 附件场景以延迟上传同步双击验证 1 upload/1 Run；另有正式随机 MVP Profile 3 项真实 Worker/Sandbox 纵切通过，测试容器、网络和卷已销毁 |
| Tauri Rust | 2 项凭据键校验与 3 项本地执行器集成测试通过；`cargo fmt --check`、`cargo clippy --all-targets --all-features -- -D warnings` 通过 |
| PlatformAdapter | Web/Tauri 双实现覆盖文件、外链、通知和安全凭据；2 个测试文件、6 项测试通过，业务源码无 Tauri 直连 |
| Tauri 桌面 E2E | macOS 本机 3 项真实应用启动、IPC、凭据失败关闭与无端口 Sidecar 生命周期通过；另有 1 项固定 Demo 账号的完整 MVP 核心纵切通过。测试 App 隐藏且不占 Dock，正式构建无 WebDriver 测试标记 |
| 百炼最小真实请求 | `bash infra/litellm/test.sh real-provider` 通过 `general-purpose` 稳定别名、隔离 LiteLLM 和北京地域兼容接口调用 `qwen-plus` 成功，返回 23 Token；临时容器、网络和卷清零 |
| 无付费模型默认回归 | LiteLLM 配置契约 17 项、Stub HTTP 协议 5 项通过；本地 Stub 协议矩阵通过并自动清理临时 Compose 项目；包含仅由显式测试场景触发的确定性 HTTP 500，以及真实附件相对路径的 `glob → read_file → write_file → create_artifact` 序列 |
| C03 MVP Profile 基础设施验收 | 隔离唯一随机端口真实启动 PostgreSQL、Redis、MinIO、LiteLLM Stub、API、Dispatcher、Worker、Sandbox Controller/Janitor 和 Web；生产 `LiteLLMChatModelFactory → LiteLLM → Stub` 调用、状态查询、重复启动、故障健康检查、保留卷停止、失败重启清理与恢复、同 Profile 并发拒绝、工作树镜像隔离及最终容器/网络/卷清理通过；dotenv 不执行、运行目录/权限/端口/网络配置校验通过；平台契约 42 项通过；行为回归额外覆盖无 `rg` 时预存卷保护、重复启动失败不拆既有容器、缺失环境状态时停止失败关闭、LiteLLM 网络检查异常失败关闭、外来网络拒绝删除、网络删除失败传播、Compose `up` 前分组端口占用拒绝，以及启动期间 `INT`/`TERM`/`ERR` 的退出码、差集清理和锁释放；正式 Playwright 业务纵切完成成功与受控模型失败两条真实链路，并验证工作台聚合、事件持久化、页面终态与刷新恢复；RAGFlow 未启动 |
| 完整本机栈 E2E | 本地 Stub 下 2 项正式 Playwright 场景通过：成功场景完成注册、登录、员工发布、任务执行并在工作台展示真实员工/任务状态；失败场景经生产 Dispatcher/Worker/LiteLLM 返回确定性 HTTP 500，持久化 `failed` Run、错误码和 `run.failed` 事件，并在工作台展示真实失败计数。macOS 真实 Tauri 另以固定 Demo 账号完成登录、员工发布、任务执行、终态和工作台聚合纵切；后端工作台契约/映射 8 项、前端工作台 9 项通过。百炼真实 `qwen-plus` 请求通过 LiteLLM 稳定别名完成并返回真实用量 |
| macOS/Windows Tauri 构建 | GitHub Actions `Tauri desktop validation` 运行 29334098300 双平台通过：正式桌面构建、Rust 测试与 2 项真实桌面冒烟均通过 |

当前已知失败：无。

## 7. 完成记录

| 任务 | 状态 | 开始日期 | 完成日期 | 提交 | 验证证据 |
| --- | --- | --- | --- | --- | --- |
| C01 | 已完成 | 2026-07-14 | 2026-07-14 | 本任务提交 | `cd backend && uv run pytest -ra`；`uv run ruff check .`；`uv run mypy`；`cd ../frontend && pnpm test && pnpm lint && pnpm typecheck && pnpm build`；`bash infra/litellm/test.sh config`；`bash infra/litellm/test.sh stub-matrix` |
| C02 | 已完成 | 2026-07-14 | 2026-07-14 | 本任务提交 | pnpm 11 工作区配置与构建脚本白名单通过 `pnpm install --frozen-lockfile` 校验；`pnpm test && pnpm lint && pnpm typecheck && pnpm build`；`pnpm exec playwright test --trace=off`；`cargo test --locked`；`cargo clippy --all-targets --all-features -- -D warnings`；`pnpm test:tauri`；GitHub Actions 运行 29334098300 的 macOS/Windows 正式构建与真实桌面冒烟通过 |
| C03 | 已完成 | 2026-07-14 | 2026-07-15 | 本任务提交 | MVP Profile 纵切：`python3 infra/platform/test_contract.py`（42 项通过）；`bash infra/compose/test.sh config`；`bash infra/litellm/test.sh config`（17 项配置契约、3 项 Stub HTTP 协议通过）；`bash infra/litellm/test.sh stub-matrix`；`bash infra/platform/test.sh config`；`bash infra/platform/test-mvp-profile.sh`；`uv run --directory backend pytest tests/unit tests/contract -q`（580 项通过）；`uv run --directory backend pytest tests/unit/workers tests/integration/database/test_migrations.py -q`（65 项通过）；工作台后端契约/映射 8 项、前端工作台 API/查询/页面 9 项及前端全量 98 项通过；`uv run ruff check . ../infra/platform/test_contract.py`；`uv run mypy`。Profile 已具备私有 allowlist dotenv、路径/权限/端口/网络校验、同 Profile 锁、失败启动按容器/网络/卷稳定名称快照清理本轮差集、环境状态缺失与 LiteLLM 网络检查异常时失败关闭、外来网络保留并报错、网络删除失败传播、分组端口预检、启动中断按 `INT=130`、`TERM=143` 与原始 `ERR` 状态仅清理一次、当前工作树专属镜像与真实恢复验收。本地 Stub 的 Playwright 纵切已覆盖成功与受控失败两条真实链路；工作台以租户和既有 RBAC 语义聚合员工、任务、全部运行状态、失败数及系统健康，失败链路同时校验 PostgreSQL 中的 Run、错误码和 `run.failed` 事件。`TAURI_MVP_WEB_URL=http://127.0.0.1:18080 pnpm test:tauri` 在隐藏、无 Dock 的真实 macOS App 中以固定 Demo 账号完成登录、员工发布、任务执行、终态与工作台纵切；`pnpm test:tauri` 的 3 项原生冒烟通过；`bash infra/litellm/test.sh real-provider` 通过隔离 LiteLLM 的 `general-purpose` 别名调用阿里百炼 `qwen-plus`，返回 23 Token，临时 Docker 资源清零 |
| C04 | 已完成 | 2026-07-15 | 2026-07-16（质量收口） | 本任务提交 | 原纵切保持闭环，并完成最终质量加固：ASGI 层在 multipart/认证前对重复、伪造声明长度与流式 receive 统一限流；API、Controller 和 Sandbox 统一 25 MiB，9 MiB 与边界上传→Run→物化均通过；未绑定文件采用客户端补偿 + 服务端 TTL，原子保护已绑定文件且清扫受 300 秒节流；同步 UI mutex 与服务端幂等键共同防重，改变任务意图会换键；存储 Provider 与底层 SDK 具备硬超时/禁重试边界，持久 tombstone 在覆盖超时证明边界的窗口内重扫迟到 put、跨重启续扫并在最终删除失败时记录后退休；首次 Worker 物化异常或取消均删除新建 Sandbox/lease。本轮质量收口新增 0020 迁移，连同 C04 既有 0018/0019 建模迁移，升级/降级/存量数据通过。后端 919/39 skip、前端 Vitest 119、Ruff/Mypy/Typecheck/Build 全过；正式 C04 脚本含 46 通过/1 条件 skip、真实 25 MiB Docker Sandbox、随机完整栈 Playwright 3 项和 PostgreSQL Saga 并发 2 项，资源清零且未触碰 `agent-platform-dev`；真实 COS 保留显式外部门禁，本轮无凭据故 1 skip |
| C05 | 待集成 | 2026-07-16 | — | 本任务提交 | RED：`cd backend && uv run pytest tests/contract/conversations/test_conversations.py -q` 首次 404；`cd backend && uv run pytest tests/integration/queue/test_run_worker.py::test_worker_persists_message_output_into_conversation_timeline -q` 首次会话消息为空；`cd frontend && pnpm test -- src/features/conversations/api/conversations.test.ts src/features/conversations/pages/ConversationDetailPage.test.tsx` 首次缺模块；员工详情“开始会话”用例首次找不到按钮。GREEN：`cd backend && uv run ruff check . && uv run mypy && uv run pytest tests/integration/database/test_migrations.py tests/contract/conversations/test_conversations.py -q`（11 passed）；`cd backend && uv run pytest tests/contract/conversations/test_conversations.py tests/integration/database/test_migrations.py tests/integration/queue/test_run_worker.py::test_worker_persists_message_output_into_conversation_timeline -q`（12 passed）；`cd frontend && pnpm exec vitest run src/features/conversations/api/conversations.test.ts src/features/conversations/pages/ConversationDetailPage.test.tsx src/features/employees/pages/EmployeeDetailPage.test.tsx src/app/App.test.tsx src/features/runs/pages/RunDetailPage.test.tsx --reporter=dot`（47 passed）；`cd frontend && pnpm lint && pnpm typecheck`；`PLAYWRIGHT_*` 隔离端口下 `pnpm exec playwright test e2e/conversations.spec.ts --reporter=line`（1 passed，真实 PostgreSQL/Redis/MinIO/API/Web） |
| C06-C20 | 尚未开始 | — | — | — | 按第 4 节逐项更新 |

后续每完成一项，将其拆成独立行记录，禁止只修改第 4 节状态而不留下提交标识和验证证据。
