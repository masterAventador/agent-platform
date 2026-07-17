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
- OpenTelemetry Trace/Metrics/Logs、告警规则和本机 Compose 基础设施；
- 全平台审计协议、HMAC-SHA256 密钥签名审计链、脱敏、保留清扫和运维审计入口；
- 平台级长期记忆：四级命名空间、运行时数据通道注入与受控提取、用户记忆中心；
- 独立审批中心：审批状态机、Tool 风险审批统一协议接入、待办/批准/拒绝/转交/超时、审批中心页面；
- 固定/混合工作流数字员工：Workflow 注册与版本固化、LangGraph 编排内核、人工节点接审批中心；
- 企业成员与完整账号体系：成员邀请/角色/Owner 转移、改密/邮箱验证/找回密码（限流防枚举）/会话设备管理；
- React Web 的认证、工作区切换、员工、任务、知识、Skill、Tool 和死信页面。

### 3.2 仍不完整或尚未实现的能力

| 能力域 | 当前状态 | 主要缺口 | 对应任务 |
| --- | --- | --- | --- |
| 工程质量基线 | 已完成 | 后端 0 失败；真实依赖跳过项均明确标注所需外部依赖 | C01 |
| Tauri 桌面客户端 | 已完成 | 共享 React、`PlatformAdapter`、macOS/Windows 构建和真实桌面 E2E 已落地 | C02 |
| 全栈真实验收与工作台 | 已完成 | 本地 Stub 成功/受控失败整栈闭环、工作台真实数据、Tauri 内核心流程及百炼真实请求均已通过 | C03 |
| 文件与产物 | 已完成 | 租户隔离的附件、沙箱物化、持久产物目录与客户端闭环已落地 | C04 |
| 多轮会话 | 已完成 | 会话/消息/任务关系、追加输入、错误重试、权限隔离、活跃任务后自动续跑派生与会话内直接取消已合入主线并通过双复审、完整回归与常驻栈真实冒烟 | C05 |
| 动态输入输出 | 已完成 | `input_schema` 已驱动动态表单、前后端运行时校验和文件输入；`output_schema` 已驱动结构化卡片、表格与 JSON 展示，并在 Worker 与真实运行时边界执行受控校验 | C06 |
| 知识运行时 | 已完成 | 员工绑定、版本化检索配置（召回/阈值/重排/元数据过滤）、运行时 RAG 注入与可追溯引用已合入主线并通过真实 RAGFlow 验收 | C07 |
| Skill 生命周期 | 已完成 | 草稿、版本、安全审核、发布、下线、删除、回滚、内置安装、引用保护、差异和使用关系界面已闭环 | C08 |
| Tool/MCP 生命周期 | 待集成 | C09 已合入主线；C13 审批中心已提供统一审批协议（Tool 风险审批已接入）；剩余生产凭据待 C18、stdio 传输 E2E 缺口待补 | C09 |
| 长期记忆 | 已完成 | 四级命名空间 Memory 领域/API/运行时注入与工具写入/受控提取/治理与记忆中心页面已合入并通过双复审与真实运行时 E2E | C10 |
| 工作流/混合员工 | 已完成 | Workflow 注册/版本/发布/回滚、LangGraph 编排内核（流程型/混合型）、人工节点接 C13 审批、编辑器开放 workflow/hybrid 已合入并通过双复审与真实栈 workflow E2E | C11 |
| 定时任务 | 进行中 | 后端调度主链 + 前端定时任务中心（创建/编辑/暂停/执行记录、按任务时区渲染）与时区/DST/重复触发/重启恢复/权限 Playwright E2E 均已落地；仅剩 C16 配额接入（由 C16 阶段三承接） | C12 |
| 审批中心 | 已完成 | 独立审批记录/状态机/待办/批准/拒绝/转交/超时/幂等、Tool 风险审批接入统一协议、审批中心页面 + 工作台卡片已合入并通过双复审与真实 PG 并发门禁及审批 E2E | C13 |
| 审计与可观测性 | 已完成 | 审计协议、HMAC 密钥签名哈希链、脱敏、保留清扫、Trace/Metrics/Logs、告警规则和运维入口已合入主线并通过隔离验收栈完整回归；剩余威胁面（持钥攻击者、整库回滚到历史合法快照需外部锚定）如实声明归 C18 | C14 |
| 企业与账号管理 | 已完成 | 成员邀请/角色/移除/Owner 转移、改密/邮箱验证/找回密码（限流防枚举）/会话设备管理已合入并通过三轮双复审与真实 PG + Playwright 完成门；OIDC/MFA 保留扩展边界 | C15 |
| 模型治理、评测、成本与配额 | 进行中 | 阶段一（Controller 对账 LiteLLM + 租户可归因/可撤销凭据）已合入并通过双复审；用量/成本留阶段二，预算/配额/限流/告警 + fallback 留阶段三，评测留阶段四，前端 + 审计 + 验收留阶段五 | C16 |
| Capability/Entitlement | 已完成 | 能力注册表、企业 Entitlement、三层启用校验与 Core-only/Core+social/Core+视频/Core+both 四组合矩阵均已合入主线（B04 合入后 video-studio 走生产装配、无夹具旁路）；「调度 Worker」与「下载 Sidecar」两条子句在 Core 中无对象，门禁已点名前移至 B08 与 B02/C20 | C17 |
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

**状态：`✅ 已完成`**

**开始日期：2026-07-16**

**完成日期：2026-07-16**

终审记录：主线收口（自动续跑派生 + 会话内取消）经独立规格复审（PASS，六条完成定义全部满足、E2E 确认真实用户路径）与代码质量复审（PASS，M1 幂等兜底边界/L1 锁序/L4 防线测试按复审整改完成）后以 --no-ff 合入 main；合并后后端全量 1079 通过（0 失败——顺带根治 Alembic fileConfig 禁用既有 logger 导致的套件级日志断言隔离缺陷）、完整 Playwright 20 项与运行时 E2E 5 项通过；常驻栈重建后以真实用户路径完成两轮会话冒烟（发送→回复→追加→第二轮回复上屏）；Demo Seed 演示员工已开箱启用会话能力（幂等纠偏，验收无需手工开能力）。遗留观察项（不阻塞）：员工下线期间保留意图与手动新轮的轻度双重消费语义、派生失败后至下一结算点的 UX 延迟，见质量复审记录。

完成定义：

- 建立会话、消息、任务和线程之间的稳定关系；
- 支持运行前后追加消息、`waiting_for_input` 提交真实内容和会话恢复；
- 提供会话列表、消息时间线、附件消息和错误重试；
- 上下文裁剪、重启恢复、跨任务续聊和取消语义明确；
- 非管理员只能访问自己的会话，管理员权限有服务端校验；
- 多轮对话 API、运行时和 Playwright E2E 通过。

2026-07-16 待集成记录：已新增 `20260716_0021` 会话迁移，建立 `conversations`、`conversation_messages` 与 `runs.conversation_id`，真实 PostgreSQL 升级已由 Playwright global setup 验证；后端契约覆盖新建/列表/详情/追加消息、`waiting_for_input` 真实 `MESSAGE` command、失败重试、上下文裁剪和 Owner/Admin/Member 访问隔离；Worker 会把 `message.output` 和失败事件落回会话时间线；前端提供会话中心、详情消息时间线、员工详情“开始会话”、追加输入、附件 ID 展示和失败重试；正式 Playwright 覆盖“发布员工 → 开始会话 → 追加两轮输入 → 回到会话中心持久列表”。

2026-07-16 主线收口记录（分支 `task/c05-conversation-closure`，与 C07/C09/C17 并行，经用户批准；无新增迁移，待主代理终审后转 `✅ 已完成`）：

- **自动续跑派生**：活跃 `queued/running/waiting_approval` 期间追加的消息，除持久化为 `queued_after_current` 外，还会在当前活跃 Run 上落一条 `FOLLOWUP` 意图命令（payload 携带 `message_id` 与 `requested_by`，创建时即置 `dispatched_at`，永不进入执行队列）；Worker 在每个终态结算点（正常结算、准备失败、续租失败、孤儿恢复、pre-start 取消、terminal-noop、命令重投递、死信结算）之后，在独立安全事务中锁定会话行、扫描会话内全部未消费意图、确认无活跃 Run 后合并派生下一轮 Run——复用与 API 相同的 `create_conversation_run`/`build_conversation_run_input` 共享创建路径（新模块 `conversation_dispatch.py`），派生 Run 使用 `uuid5(conversation_id, 触发消息)` 确定性幂等键，消息绑定、附件物化、START 命令与意图结清同事务提交；多条排队消息合并为一轮（`message` 取最后一条、上下文含全部）；派生 Run 归属触发消息作者（`requested_by`）；员工版本沿用会话既有语义（派生时的当前发布版本），员工不可运行时受控跳过并保留意图；派生失败仅记录日志，不阻塞原 Run 结算；API 追加消息与 Worker 派生通过会话行锁（`get_for_update`）互斥，消除结算瞬间的丢失/双跑竞态；`dispatch=false` 存储型消息不带意图命令，永不触发自动续跑。
- **会话内取消**：`RunResponse` 暴露 `created_by`；会话详情页对活跃关联任务提供「取消任务」（复用 `/runs/{id}/control` cancel，仅创建者或 RUNS_MANAGE 可见）与「任务详情」跳转；会话详情在存在活跃任务时 2 秒轮询刷新，取消与自动续跑结果实时反映到时间线。
- **质量复审整改（同分支）**：M1——uuid5 幂等冲突实际在仓储 flush 边界抛出，原兜底只包住 commit 导致并发派生被误报 `conversation_followup_dispatch_failed`（ERROR）；已按 RED（`test_followup_uuid5_conflict_is_treated_as_already_derived_not_failure` 先复现 ERROR 误报）→ GREEN 把 IntegrityError 兜底扩到整个创建区段，命中时按 WARNING `conversation_followup_already_derived` 处理且结算不受影响。L1——`_persist_renewal_failure` 终态分支先提交释放 Run 行锁再派生，与其余终态分支一致。L4——新增 `tests/unit/queue/test_dispatcher.py` 直接覆盖 dispatcher 对 FOLLOWUP 命令“兜底结清但绝不入队”的防线。整改后 `uv run pytest tests/contract/conversations tests/integration/queue/test_run_worker.py tests/unit -q`（697 passed，2 skipped）、`uv run pytest tests/unit tests/contract -q`（900 passed）、`ruff check .`、`mypy` 全过。

### C06 动态输入 Schema 与结构化输出

**状态：`✅ 已完成`**

**开始日期：2026-07-16**

**完成日期：2026-07-16**

完成定义：

- 员工 `input_schema` 驱动字符串、数字、布尔、枚举、日期、数组和文件表单；
- 前端 Zod 与后端 JSON Schema 执行一致的运行时校验；
- `output_schema` 驱动结构化结果卡片、表格、JSON 和导出；
- Schema 版本随员工发布版本固定，旧任务可继续解释；
- 无效 Schema、超大输入和不兼容输出有受控错误；
- 契约、组件和真实任务 E2E 通过。

2026-07-16 完成 C06：后端新增统一动态 IO 校验边界，员工创建、更新、发布会校验 `input_schema`/`output_schema`，并拒绝当前动态表单无法表达的输入 Schema；声明了 `properties` 的动态表单必须显式关闭 `additionalProperties`，避免后端接受前端固定字段表单无法产生或校验的额外字段；运行入口也复用同一表单兼容性校验，历史已发布版本若带 `properties` 却未关闭 `additionalProperties` 会 fail-closed，不会在创建任务时继续放行额外字段；文件型动态字段必须同时启用 `capabilities.file_upload`，文件控件只允许受控元数据和 `binary/file` 语义，拒绝 `pattern`、`enum`、长度等前端文件控件无法等价校验的约束，数组元素也拒绝文件语义，避免发布后才在创建任务阶段失败；运行入口会用发布版本的 `capabilities.file_upload` 重新校验 `input_schema`，历史已发布版本即使文件字段是可选且本次未提交文件，也不会在文件上传能力关闭时继续放行该非法 Schema；字符串 `pattern` 只接受浏览器 `RegExp` 可编译的受控子集，拒绝 Python 命名分组等前端无法执行的正则，同时前端对残留不兼容 pattern 返回受控表单错误而不是抛未捕获异常；`const`、`oneOf`、`anyOf`、`allOf`、`if/then/else`、`dependentRequired`、`patternProperties` 等前端动态表单无法一致表达的 JSON Schema 关键字会在员工定义阶段受控拒绝。创建任务固定读取已发布员工版本并按版本化 `input_schema` 校验输入；带 `Idempotency-Key` 的旧任务重放会优先返回原 Run，并按原 `employee_version` 读取 `output_schema`，不会被当前新发布版本的输入/输出 Schema 重新解释；动态文件字段值还必须与本次 `attachment_ids` 精确绑定，前端在响应丢失后的幂等重试会复用未删除的已上传文件 ID，字段值或文件选择变化才会换键。Worker 对有效 `output_schema` 执行结构化输出校验，不再把历史默认空对象 Schema 误判为必须输出对象，违规输出会以 `output_schema_validation_failed` 受控失败并过滤无效完成事件。DeepAgentRuntime 在真实运行时边界根据有效 `output_schema` 将模型返回的 JSON 字符串解析为结构化结果，覆盖对象、数组和数字等标量输出，同时不会把 `type: string` 下的普通数字文本误转为数字，任务响应同时返回版本化 `output_schema`，旧任务可按发布版本解释。前端员工编辑器、员工详情运行弹窗和任务详情页已接入 Schema 驱动的动态输入、文件选择上传、结构化卡片/表格/JSON 展示；员工编辑器提供真实 `input_schema`/`output_schema` JSON 配置入口，真实运行时 E2E 不再通过路由拦截篡改员工 Schema；零字段且 `additionalProperties=false` 的 Schema 会按动态空输入提交 `{}`；动态输入在提交前执行与后端允许 Schema 子集一致的 Zod 校验，覆盖可选布尔字段不静默提交 `false`、必填布尔字段未触碰时按可见状态提交 `false`、日期、正则、数值倍数、数组长度和唯一性，并覆盖无效 Schema、超大输入、权限可见性和真实 Worker/Sandbox 任务闭环。

### C07 知识库完整生命周期与运行时 RAG

**状态：`✅ 已完成`**

**开始日期：2026-07-16**

**完成日期：2026-07-17**

终审记录：经独立规格复审（结论 a——完成定义六条全部满足，引用 E2E 零 route 拦截全真实链路）与代码质量复审（PASS，校验单源/fail-closed/事件幂等/租户隔离/瞬永错误分类经对抗推敲）后合入 main。合入前完成迁移 0027 重链至 0026、README 与 manage.sh 的 TEI 默认模型矛盾修正（默认 bge-small-en-v1.5 英文-only，中文语料需 RAGFLOW_TEI_MODEL=BAAI/bge-m3 覆盖）。合并后后端全量 1198 通过、前端 213、默认 Playwright 23/23、runtime E2E 6/6（含知识引用闭环与 C05 场景）；真实 RAGFlow v0.25.6 独立栈集成验收通过（数据集/双文档/meta_fields/解析/检索契约/top_k/阈值/元数据过滤命中与不命中/删除，栈用后销毁）；常驻栈重建后真实用户冒烟通过（知识库页 + 员工编辑器知识绑定与引用说明）。已声明限制：重排端到端待配置重排模型的实例后补验（rerank_id 传参已在单元层验证，用例留有 TEST_RAGFLOW_RERANK_ID 开关）；knowledge.retrieved 事件键当前支持单 run 单检索；编辑器元数据过滤字段名无候选提示。附带发现并规避：默认 Playwright 套件多 worker 缩容存在 worker 退出挂起（与本条目无关，已固定单 worker 并记录待查）。

2026-07-16 进度与复审记录：实现工作在 `task/c07-knowledge-runtime` 分支进行（WIP 提交 `8d483ce`），员工编辑器知识库选择、发布引用校验、文档批量上传/重试/替换/删除、`knowledge.retrieved` 事件与任务详情引用展示已有代码和单元/契约测试；独立评估确认架构方向正确（RAGFlow 零侵入、Provider 检索 fail-closed、越权片段过滤），可作为继续开发基线。**该分支曾把本条目虚标为已完成，已纠正，以主线本文为准**。继续开发必须先解决：`workers/main.py` 未注入 `knowledge_provider_registry`（生产 Worker 知识链路整体断开）；RAGFlow 断连被误映射为永久性 `invalid_runtime_definition`；畸形响应未捕获无测试；`knowledge_retrieval` 元数据过滤为端到端死代码（领域快照与 API 均无该字段）；重排配置未实现；批量上传/替换中途失败无补偿；引用与文档生命周期 Playwright E2E 缺失。分支叠在 C14 分支之上，回主线时仅 `knowledge/ragflow.py` 的 `operation` kwarg 依赖 C14 新签名，需一并处理。

2026-07-16 高危缺陷修复轮（分支已 rebase 到最新 main、与 C14 解耦，`operation` kwarg 已随 rebase 移除）：按 TDD 完成上述阻断项修复——生产 Worker 装配注入 RAGFlow `knowledge_provider_registry`（配置来源与 API 侧一致，RED `test_production_worker_assembly_wires_knowledge_runtime_for_bound_employees`）；断连改映射 `TransientRuntimePreparationError` 交由队列重投递重试、畸形响应受控映射稳定错误码 `invalid_knowledge_provider_response`（RED `test_knowledge_provider_outage_is_transient_and_never_a_permanent_definition_error`、`test_malformed_knowledge_provider_response_fails_with_a_stable_controlled_code`）；`knowledge.retrieved` 事件改按 run 确定性 `event_id` 并在事件流重收集分支重建，重投递不重复不丢失（RED `test_knowledge_event_survives_redelivery_without_duplicate_or_loss` 等 2 项）；批量上传加 20 个上限并在中途失败时尽力补偿删除已上传文档，替换失败补偿删除新文档保持旧文档原状（RED 契约用例 4 项）；`knowledge_retrieval` 解析死代码按删除规范整段移除，**元数据过滤/召回参数后续随全链路（领域实体、API、发布快照、前端编辑器）单独实现**；前端知识库详情页重试/删除改按行 pending、操作失败显示 Alert 提示，并删除死代码 `useUploadKnowledgeDocument`；同步 `contracts/events/platform-event.schema.json` 补上 `knowledge.retrieved`。验证：`cd backend && uv run pytest tests/unit/knowledge tests/unit/workers tests/contract/knowledge tests/contract/employees tests/integration/queue/test_run_worker.py -q`（175 passed, 2 skipped）；`uv run pytest tests/unit tests/contract -q`（875 passed）；`uv run ruff check .`、改动文件 `ruff format --check` 通过（全仓库 format 未达标为 main 既有状态，非本轮引入）；`uv run mypy`（181 文件通过）；`cd frontend && pnpm exec vitest run src/features/knowledge src/features/employees/pages/EmployeeEditorPage.test.tsx src/features/runs --reporter=dot`（37 passed）；`pnpm lint && pnpm typecheck` 通过。仍未满足的完成定义：重排配置、元数据过滤/召回参数全链路、引用与文档生命周期 Playwright E2E、真实 RAGFlow 集成验收。

2026-07-16 中级缺陷修复（复审发现：知识 Provider 错误分类过粗）：此前 `knowledge/ragflow.py` 的 `_request` 把 `raise_for_status()` 的 4xx（API Key 错、无权限、数据集已删）和 RAGFlow `code != 0` 业务错误一律抛成瞬态 `KnowledgeProviderUnavailable`，Worker 侧再映射为 `TransientRuntimePreparationError`，导致永久性配置/权限/资源错误也烧满 5 次队列重投才进死信，且死信呈现为泛化 `DELIVERY_PROCESSING_ERROR`。按 TDD 修复：client 层新增永久性错误 `KnowledgeProviderRequestRejected`（消息仅含脱敏稳定原因——HTTP 状态码或业务错误码，不泄露原始响应体），4xx 与 `code != 0` 抛该类型，网络错误/超时/5xx 保持瞬态 `KnowledgeProviderUnavailable`；Worker 侧 `runtime_composition.py` 新增 `KnowledgeRuntimeRequestRejected(PermanentRuntimePreparationError)`，稳定错误码 `knowledge_provider_rejected`，立即受控永久失败不重投；API 侧新增异常处理器把该类型映射为 502 `knowledge_provider_rejected`（瞬态错误维持 503 `knowledge_provider_unavailable` 契约不变）。RED 用例：`test_ragflow_4xx_status_is_permanent_rejection_without_leaking_response[401/403/404]`、`test_ragflow_business_error_envelope_rejects_permanently_without_leaking`、`test_ragflow_transport_and_server_failures_stay_transient_unavailable`（4 参数瞬态回归）、`test_rejected_knowledge_provider_request_is_permanent_with_stable_code`、`test_rejected_knowledge_provider_fails_permanently_without_redelivery`、`test_knowledge_provider_rejection_returns_permanent_bad_gateway_error`。验证：`uv run pytest tests/unit/knowledge tests/unit/workers tests/contract/knowledge tests/integration/queue/test_run_worker.py -q`（166 passed, 2 skipped）；`uv run pytest tests/unit tests/contract -q`（883 passed）；`uv run ruff check .` 与 `uv run mypy`（181 文件）通过。

2026-07-16 复审低级遗留（暂不修复，后续轮次处理）：其一，未配置 RAGFlow（或知识库记录的 provider 名未注册）时，`KnowledgeProviderRegistry.resolve` 抛瞬态 `KnowledgeProviderUnavailable`，绑定知识库的任务会走满重投递才失败，失败模式噪声大，宜识别为受控永久失败（**已在收口轮修复，见下**）；其二，`knowledge.retrieved` 事件 `event_id` 仅按 `run.id` 取键，单次运行仅支持一条检索事件，未来同一 run 多次检索（多轮对话中途检索、Agent 主动检索工具）时需扩展事件键设计（仍遗留）。

2026-07-16 收口轮（召回参数/重排/元数据过滤全链路 + 引用与文档生命周期 E2E + 真实 RAGFlow 验收；分支基于 merge origin/main 后的工作区，待主代理验收提交）：
- **检索配置全链路（TDD）**：新增平台单一配置来源 `platform/knowledge/retrieval.py`（`KnowledgeRetrievalConfig`，字段名对齐 RAGFlow v0.25.6 官方检索 API：`page_size` 默认 5=原行为、`similarity_threshold` 0-1 默认 0.2、`vector_similarity_weight` 0-1 默认 0.3、`top_k` ≥1 默认 1024、`keyword`、`rerank_id`（重排模型 ID，None=关闭）、`metadata_condition`（`logic` and/or + `conditions[{name, comparison_operator∈官方 11 种, value}]`），strict + extra=forbid fail-closed；无效配置在员工定义阶段受控 422 `invalid_knowledge_retrieval`（含 path/reason，对齐 C06 风格）。全链路 = `EmployeeDraft.knowledge_retrieval` 领域字段 + 发布 snapshot 固化全量显式值（旧发布版本无该键 → Worker 按默认配置解释，版本化语义成立）+ API 请求/响应模型 + 迁移 `20260716_0027`（employees 表新增 JSON 列；编号协调：0025 为 C14 HMAC、0026 为 C17，暂 down_revision=0024，后合入者重链）+ 前端员工编辑器「知识检索配置」面板（召回条数/相似度阈值/向量权重/Top K/关键词增强/重排模型 ID/元数据过滤 Form.List，选中知识库时显示，编辑回填）+ Worker `PublishedRuntimeCapabilities.knowledge_retrieval` 解析（非法快照 fail-closed `UntrustedRuntimeDefinition`）传入 `provider.retrieve(options=...)`；知识库详情页检索测试接口复用同一配置模型。RED→GREEN：`tests/unit/knowledge/test_retrieval_config.py`（先 ModuleNotFoundError）、`test_ragflow_retrieve_sends_all_v0_25_6_retrieval_options`、`test_published_knowledge_retrieval_config_is_honored_per_version`、`test_invalid_published_knowledge_retrieval_config_fails_closed`、契约 `test_knowledge_retrieval_config_full_chain_create_publish_and_version_freeze`/`test_knowledge_retrieval_defaults_apply_when_config_is_omitted`/`test_invalid_knowledge_retrieval_config_is_rejected_fail_closed`（5 参数）、前端编辑器 3 项新用例。
- **真实契约缺陷修复**：对照 v0.25.6 官方 tag 源码（`api/apps/restful_apis/chunk_api.py` 的 key_mapping）发现真实检索响应文档名字段是 `document_keyword` 而非 `document_name`，且 `document_metadata` 仅在请求携带 `include_metadata` 时注入——原 client 与 Stub 均用错误字段名，对真实实例检索会全部落入 `invalid_knowledge_provider_response`。已修复 client（改用 `document_keyword`、请求恒发 `include_metadata: true`、`document_metadata` 可缺省）并将 `tests/fixtures/ragflow_stub.py` 重写为契约对齐形态（真实 chunk 字段、`top_k<=0` 业务错误信封、`include_metadata` 语义、`page_size` 截断、`metadata_condition` 过滤语义、`parse-fail` 文件名确定性解析失败场景）。RED：`test_ragflow_retrieve_parses_the_real_v0_25_6_chunk_shape`。
- **未注册 provider 遗留修复**：`KnowledgeProviderRegistry.resolve` 未注册名改抛新永久错误 `KnowledgeProviderNotConfigured`；Worker 映射 `KnowledgeRuntimeNotConfigured`（code `knowledge_provider_not_configured`，立即受控永久失败不烧重投，RED `test_unregistered_knowledge_provider_is_a_permanent_configuration_error`）；API 侧映射 503 `knowledge_provider_not_configured`（契约用例同步收紧）。
- **E2E（隔离端口 + 随机 Compose project）**：`knowledge.spec.ts` 新增文档生命周期用例（批量上传 2 文档、确定性解析失败、重试解析转成功、替换文档、删除文档、检索引用指向替换后文档），2 项通过；新增 `knowledge-runtime.spec.ts` 引用闭环（创建知识库→上传→解析→创建员工绑定知识库并配置召回条数/关键词增强→发布→发起任务→真实 Worker 检索→任务详情 `knowledge.retrieved` 引用卡片含文档名与片段内容→刷新后仍在），随 `bash infra/platform/test-runtime-e2e.sh`（真实 PostgreSQL/Redis/MinIO/API/Dispatcher/Worker/Sandbox Controller/RAGFlow Stub/Web）4 项全过（3 项既有 runtime 回归 + 1 项新引用闭环）；`employees.spec.ts`、`runs.spec.ts` 回归通过；每轮结束随机 project 容器/网络/卷复查为零。
- **真实 RAGFlow v0.25.6 集成验收（通过）**：`infra/ragflow/manage.sh` 拉起锁定 v0.25.6 官方独立栈（官方华为云镜像源 `swr.cn-north-4`，独立网络/卷/端口，未触碰 `agent-platform-dev` 常驻栈与他项目 `ragflow-local-threshold-*`）。过程中按 TDD 之外的基础设施最小修正修复 manage.sh 三处真实问题：①宿主端口硬编码与常驻开发栈 Redis(16379)/SSH 隧道(18080/19000/19001)冲突——新增 `RAGFLOW_*_PORT` 环境变量覆盖（默认不变）；②官方 profile 组合未启用任何 embedding，文档解析必然失败 "No default embedding model is set"——默认启用官方 `tei-cpu` profile，并经 `compose.override.yml` 向 ragflow 容器透传实际 `COMPOSE_PROFILES`（官方 .env 不透传该值，RAGFlow 依赖它启用 TEI 默认 embedding）；③TEI 预置 Qwen3-Embedding-0.6B 与 bge-m3 在本机 CPU warmup OOM（exit 137 崩溃循环）——默认改为最小预置模型 `bge-small-en-v1.5`，可 `RAGFLOW_TEI_MODEL` 覆盖；README 同步更新，`manage.sh config` 契约断言（13306/ES_JAVA_OPTS/mem_limit）复核通过。验收用例 `tests/integration/knowledge/test_real_ragflow.py`（`TEST_RAGFLOW_URL`/`TEST_RAGFLOW_API_KEY` 显式门禁，无凭据 skip，对齐真实 COS 门禁模式）对真实实例 1 项通过：数据集创建、双文档上传、官方 API 设置 `meta_fields`、解析等待 DONE、默认检索（引用返回、`document_keyword` 契约、`document_metadata` 注入、score∈[0,1]）、`page_size=1` 限流、`top_k`/`similarity_threshold` 生效、高阈值过滤为空、元数据过滤命中（仅 HR 文档）与不命中（空结果）、文档删除、数据集删除；结束后本轮栈已 down、容器/网络/卷复查为零。**重排端到端未在真实实例验证**：TEI 仅提供 embedding，该实例无已配置的重排模型；`rerank_id` 传参正确性已在单元层验证，真实重排验证需配置重排模型的实例（用例支持 `TEST_RAGFLOW_RERANK_ID` 显式启用），列入遗留。
- **验证命令**：`cd backend && uv run pytest tests/unit/knowledge tests/unit/workers tests/contract/knowledge tests/contract/employees tests/integration/queue/test_run_worker.py -q`（219 passed, 2 skipped）；`uv run pytest tests/unit tests/contract -q`（951 passed）；`uv run pytest tests/integration/database/test_migrations.py tests/integration/queue/test_run_worker.py -q`（71 passed, 2 skipped，升级/降级含 0027）；`uv run ruff check .`、`uv run mypy`（186 文件）；`cd frontend && pnpm exec vitest run src/features/knowledge src/features/employees --reporter=dot`（47 passed）、全量 vitest 204 passed（复跑两次确认；一次偶发 social-operations 5s 超时经干净树对照与单独复跑确认为并行负载下的既有脆弱用例、非本轮引入）、`pnpm lint`/`pnpm typecheck`/`pnpm build` 通过；Playwright 全部使用 `PLAYWRIGHT_COMPOSE_PROJECT_NAME` + 随机 `PLAYWRIGHT_*_PORT` 隔离端口。
- **本轮遗留**：①`knowledge.retrieved` 事件键单检索事件限制（见上）；②真实实例重排端到端验证（需配置重排模型）；③员工编辑器元数据过滤字段名无候选提示（依赖知识库文档 metadata 治理，随后续知识库元数据管理功能考虑）。

- 员工编辑器可选择有权限的知识库，发布时校验引用；
- Worker 通过 Knowledge Service 检索并将结果注入运行时；
- 输出事件和界面展示可追溯引用，不暴露越权片段；
- 支持文档解析进度、失败重试、删除、更新和批量导入；
- 支持多知识库、元数据过滤、召回参数和重排配置；
- RAGFlow 断连、畸形响应、租户隔离和引用 E2E 通过。

### C08 Skill 完整生命周期与安全审核

**状态：`✅ 已完成`**

**开始日期：2026-07-16**

**完成日期：2026-07-16**

完成定义：

- 支持 Skill 草稿、版本、审核、发布、下线、删除和回滚；
- 内置 Skill 可从根目录 `skills/builtin/` 幂等安装；
- 增加压缩包、路径、脚本、依赖、大小和危险内容审核；
- 已发布版本被员工引用时具备稳定兼容和删除保护；
- 提供安全审核结果、版本差异和使用关系界面；
- 生命周期、物化、沙箱执行和租户隔离测试通过。

2026-07-16 完成 C08：Skill 增加显式生命周期状态和版本级安全审核结果，上传后自动记录压缩包、路径、大小、脚本、依赖和危险内容审核，危险脚本、非可信依赖来源和提示注入/疑似密钥会阻断发布；发布旧版本作为回滚入口，下线后不再作为新绑定候选，删除前扫描员工草稿与已发布员工版本并进行引用保护；员工发布版本会固化 `skill_versions`，Worker 运行时优先按固定 Skill 版本物化，因此后续 Skill 回滚不会改变旧员工版本的运行语义。根目录 `skills/builtin/` 已具备内置 Skill 源文件，平台提供幂等安装器和管理端接口；客户端 Skill 详情页展示安全审核结果、版本差异和使用关系，并提供发布、下线和删除操作。新增 `20260716_0023` 迁移补齐持久化字段，`down_revision` 指向 `20260716_0021`，保持主线 Alembic 单头迁移链。

### C09 Tool/MCP 完整生命周期与凭据产品化

**状态：`🧪 待集成`（已合入主线；C13 已提供审批协议并接入 Tool 风险审批；剩余生产凭据服务待 C18、stdio 传输 E2E 缺口待补）**

**开始日期：2026-07-16**

开工说明：前置 C03 已满足；经用户批准与 C07/C17/C14 收尾并行（文件域独立）。实现分支 `task/c09-tool-mcp-lifecycle`，迁移编号占用 `20260716_0027`（down_revision 暂指 0024，主代理合并时统一重链）。审批协议集成待 C13、生产凭据待 C18，对应部分按 `🧪 待集成`处理。

完成定义：

- MCP Server 支持连接测试、工具自动发现、同步、编辑、禁用和删除；
- Tool Schema、风险等级和审批策略可校验、更新和回滚；
- 凭据通过平台凭据服务配置，只向授权执行器短时解析；
- 工具调用超时、重试、熔断、错误转换和审计完整；
- 客户端可查看连接状态、同步差异、调用记录和失败原因；
- HTTP/stdio、恶意 Server、凭据脱敏和审批 E2E 通过。

2026-07-16 实现记录（本任务提交，待主代理验收）：

- **生命周期**：MCP Server 增加连接测试（`POST /mcp-servers/{id}/connection-test`）、自动发现同步（`POST /{id}/sync`，按 name 对齐、新增/变更/上游移除差异语义、每 Server 保留最近 20 条同步报告）、编辑（PATCH，transport 不可变）、删除（对齐 C08 引用保护：被员工草稿/已发布版本引用时 409）；同步在 Server 行锁内应用（并发互斥），新发现工具 fail-closed 默认 `enabled=false + risk=external`，上游移除的 discovered 工具标记 `upstream_missing` 保护而非删除，调用点由 Gateway 以 `tool_upstream_missing` 拒绝；
- **版本化与审批策略**：`tools` 增加 `origin/approval_policy/upstream_missing/version`，新增 `tool_versions` 快照表（initial/update/sync/rollback 变更来源），定义变更自动升版本、支持回滚为新版本；审批策略 `risk_based/always/never` 三档，destructive+never 在校验层拒绝且策略引擎纵深防御强制审批；
- **凭据产品化**：复用 `infrastructure/secrets/` 本地凭据服务，新增 `LocalFileCredentialStore`（0600、原子替换、flock 互斥、仓库外强制），`PUT/DELETE /mcp-servers/{id}/credentials` 使用服务端生成的 `local://mcp-servers/{id}` 引用；凭据仅在探测/执行边界短时解析，API 响应、审计与日志不回显明文（API 级契约验收覆盖）；
- **网关韧性**：`ResilientToolExecutor` 稳定错误码转换（tool_timeout/tool_remote_error/credential_unavailable 等）+ 仅只读工具有界重试（副作用工具绝不自动重试）；每 (tenant, server) 内存熔断器（阈值/冷却可配、容量有界），熔断拒绝发生在 STARTED claim 之前，不改既有 invocation claim/TOCTOU 崩溃安全协议；stdio 传输经 `AllowlistStdioExecutionPolicy`（默认全拒绝，`AGENT_PLATFORM_MCP_STDIO_ALLOWED_COMMANDS` 显式放行）；
- **调用记录**：新增 `GET /tool-invocations`（tenant 隔离、按 tool/server 过滤），前端工具页展示连接状态、同步差异弹窗、版本历史/回滚、调用记录与失败原因；员工可用工具过滤补充 `upstream_missing`；
- **运行语义修正**：员工绑定的工具被禁用/上游移除时不再让整个任务在准备阶段失败，改为组装后在调用点由 Gateway 拒绝并写 `tool.rejected` 审计（已删除引用仍 fail-closed）；
- **验收**：`tests/fixtures/mcp_stub.py`（官方 FastMCP 协议栈 + 故障注入控制端点）支撑三层真实边界验收——API 级恶意/超慢/畸形 Server 与凭据脱敏（`tests/integration/mcp/test_lifecycle_api_with_real_stub.py`）、Playwright 真实用户路径（`e2e/tools.spec.ts`：注册→连接测试→同步→差异→凭据配置→脱敏断言）、真实 Worker 端到端（`e2e/runtime.spec.ts`：员工任务经 Tool Gateway 调用 stub 工具成功，禁用后调用被拒且审计与界面记录可见）；
- **待集成**：审批策略与独立审批中心的协议对接（C13）；生产级凭据服务 Vault/KMS 与轮换（C18，当前本地凭据服务仅限开发/演示）；stdio 真实拉起进程的端到端验收依赖部署侧允许清单配置，当前以单元/契约层验证 allowlist 语义。
- **已知取舍 / follow-up**（2026-07-16 质量复审后记录）：
  - 员工编辑器的可绑定校验（`are_bindable`）尚未把 `upstream_missing` 计入不可绑定条件——上游已移除的工具仍出现在绑定候选（运行时调用点会拒绝，安全不受影响），属于 UX 不一致，待后续对齐；
  - `tool_versions` 随显式更新/回滚/同步变更增长且无裁剪策略；增长由人工操作驱动、速率可控，暂不设上限，量级出现问题时再补保留策略；
  - 凭据配置为“先写本地凭据文件、后提交 DB `secret_reference`”两步操作：DB 提交失败会留下孤立的凭据文件条目（不泄露、不影响正确性，重新配置即覆盖），生产凭据服务（C18）引入事务性/对账机制时一并解决；
  - 同步遇到与 MANUAL 工具同名的上游工具时静默跳过（不覆盖管理员定义、计入未变化），暂未在同步报告中单列“冲突”类别，需要更强可观测性时再扩展报告结构。

验证命令（本任务实际执行）：`uv run pytest tests/unit tests/contract tests/integration/tools tests/integration/mcp tests/integration/database -q`、`TEST_DATABASE_URL=<真实PG> uv run pytest tests/integration/database/test_migrations.py -q`（含既有 tool 行回填的真实 PostgreSQL 迁移回归）、`uv run ruff check . && uv run mypy`、`pnpm exec vitest run`、`pnpm lint && pnpm typecheck && pnpm build`、隔离栈 `pnpm exec playwright test e2e/tools.spec.ts`（PLAYWRIGHT_COMPOSE_PROJECT_NAME + 随机端口）、`pnpm e2e:runtime`。

### C10 平台级长期记忆

**状态：`✅ 已完成`**

**开始日期：2026-07-17**

**完成日期：2026-07-17**

开工说明：前置 C05 已完成。经既定并行门禁与 C13 并行。实现分支 `task/c10-long-term-memory`（worktree `wt/c10`）；迁移开发期占用 `20260716_0031`，合入时按「先合入者保留编号」惯例改号为 `20260716_0029`（down_revision=`20260716_0028` 单头；C13 占用 0030，B04 合入时再改号重链）。

2026-07-17 完成记录（本任务提交，merge 合入，全程先 RED 后 GREEN）：平台自有 `memories` 表（迁移 `20260716_0029`）承载企业/用户/员工/会话四级命名空间长期记忆，键 `(tenant_id, scope, scope_ref)`，`(tenant, scope, scope_ref, source, key)` 唯一索引 + `begin_nested` 收编保证并发/重投递幂等；与 LangGraph Checkpoint 职责分离（Checkpoint=运行内线程状态，Memory=跨任务长期知识，全 diff 无 checkpoint 交叉引用）。生命周期：`/api/v1/memories` REST（写入/检索/更新纠正/删除/过期/禁用启用），过期读取时判定（active_only 与运行时召回过滤三处一致），自动来源记忆按命名空间 200 条容量裁剪（manual 不参与），单条 4000 字符、提取每 run 5 条、注入 20 条均单一常量来源。运行时接入（零侵入）：召回经 Worker 准备阶段注入 `input_data["memory_context"]` 纯数据通道（与 C07 knowledge_context 同模式，测试断言注入文本不进 system_prompt/employee_definition，防提示注入放大）；运行中写入经 `StructuredTool` `save_memory` 工具，只允许写发起用户自身 user/conversation 命名空间（模型不可伪造 scope_ref，防投毒放大）；完成后提取只收编显式 `<remember>` 声明（确定性、零模型成本），在终态结算后的独立安全事务执行，失败仅受控日志不阻断 Run 收尾（C05 会话投影同模式）。治理：写入统一敏感信息脱敏/整条敏感受控拒绝 422、跨租户/跨命名空间越权 404/403 fail-closed、员工 `capabilities.memory` 非布尔视为关闭全链一致、用户经「记忆中心」页面查看/纠正/删除/禁用个人记忆。Demo Seed 幂等写入三级命名空间演示记忆并为演示员工开启记忆能力。双复审均 PASS：质量复审确认模型写入半径收敛、API fail-closed、提取事务隔离、容量单一来源、E2E 真实闭环非夹具直写；规格复审逐条核完成定义并亲自复跑全部验证（含隔离栈 Playwright 与 runtime E2E）。登记的后续观察项（不阻断）：LIKE 子串检索无索引（租户内量级受容量约束，增长后可加 trigram 索引）、PATCH 无法把 expires_at 清回 null、manual 记忆无自动容量上限、`<remember>` 标记在输出中不剥离、无 memories.* 独立权限码（复用 employees.manage/runs.execute，细粒度随 C15/C18 演进）。验证命令：`cd backend && uv run pytest tests/unit/platform/memory tests/unit/workers/test_memory_runtime.py tests/contract/memories tests/integration/database/test_migrations.py tests/integration/queue/test_run_worker.py -q`（139 passed）；`uv run pytest -q`（1324 passed, 42 skipped）；`uv run ruff check . && uv run mypy`（205 文件）；`cd frontend && pnpm test`（46 文件 230）；`pnpm lint && pnpm typecheck && pnpm build`；随机隔离栈默认 Playwright 全量 26 passed（含 memories.spec 2 项）；`infra/platform/test-runtime-e2e.sh` 9 passed（含 memory-runtime 2 项：多轮召回→纠正生效→删除不可召回 + 禁用不提取）；复审代理独立复跑全部通过。

完成定义：

- 建立企业、用户、员工、会话等命名空间的 Memory 模型和 API；
- 支持记忆提取、写入、检索、更新、删除、过期和禁用；
- 记忆与 LangGraph Checkpoint 职责分离；
- 运行时按权限读取和写入，用户可查看和纠正个人记忆；
- 敏感信息、租户越权、提示注入和错误记忆有治理策略；
- 隔离、召回、删除、恢复和多轮使用 E2E 通过。

### C11 固定工作流与混合型数字员工

**状态：`✅ 已完成`**

**开始日期：2026-07-17**

**完成日期：2026-07-17**

开工说明：前置 C05/C06/C13 均已完成合入。与 C15 并行。分支 `task/c11-workflow-employees`；迁移合入时按「先合入者保留编号」重链——B04 先合入占 0031(video)/0032(crc64)，本 workflows 迁移重编为 `20260716_0033`（接 0032 单头）。

2026-07-17 完成记录（本任务提交，merge 合入，全程先 RED 后 GREEN）：Workflow 注册表 + 版本快照（迁移 `20260716_0033` 建 workflows/workflow_versions 两表），发布/回滚/稳定引用，员工发布时固化 `workflow_version`——`SqlAlchemyWorkflowSpecLoader` 始终按固化 (workflow_id, version) 加载，workflow 回滚不改旧员工运行语义。执行内核 `runtimes/workflow_graph.py` 把平台自研图定义编译为 LangGraph `StateGraph`，六类节点（agent/tool/subagent/human_approval/branch/subflow），条件分支/重试/子流程/Interrupt/人工节点齐备；环/不可达/无终态/深度上限在注册期静态 fail-closed。流程型员工直接用 LangGraph、混合型在节点内经公开 `create_deep_agent` 调 Deep Agents。**零侵入硬门禁通过**：只用公开扩展点（StateGraph/add_node(retry_policy)/add_conditional_edges/interrupt/Command/编译子图），全 diff 无 monkey patch/私有 API/复制框架实现。**人工节点接 C13 审批不另起旁路**：human_approval 节点 `interrupt` → 既有 LangGraphRuntime 映射 APPROVAL_REQUIRED → C13 `sync_run_approvals` 幂等建 Approval → 决策驱动 run approve/reject。编辑器开放 workflow/hybrid 且只允许引用已发布 workflow（未注册/未发布 fail-closed）。任务/事件/状态只暴露平台协议。前端 workflow 定义管理页 + 员工编辑器解禁两类型。Demo Seed 幂等补含人工审批节点的演示 workflow + 已发布流程员工。双复审：首轮质量复审 FAIL 抓到 [M1] 分支未命中崩溃（path_map 缺 END 键）、[M2] 子流程人工节点校验时机、[L1] str.format 遍历面、[L2] add_version 裸 500，退回集中修复；二轮质量复审再抓到 L1 修复自身引入的占位符注入（顺序 replace 回灌），改单遍 re.sub 消除。修复后二轮双复审均 PASS。验证：`cd backend && uv run pytest tests/unit/platform/workflows tests/unit/runtimes/test_workflow_graph.py tests/contract/workflows tests/integration/queue/test_run_worker.py tests/integration/database/test_migrations.py -q`（146）；`uv run pytest -q`（合并后全量 1536 passed, 50 skipped）；`uv run ruff check . && uv run mypy`（230 文件）；`cd frontend && pnpm test`（53 文件 262）+ lint/typecheck/build；真实栈 `infra/platform/test-runtime-e2e.sh` workflow-runtime E2E 2/2（流程员工真实 LangGraph 编排跑到终态 + 人工审批经审批中心批准后继续，随机隔离栈验后清零）。登记 follow-up（非阻断）：子流程人工节点已提升为静态拒绝（M2 已修）；状态恢复对流程员工的专门 E2E 待补。

完成定义：

- 建立 Workflow 定义、版本、注册、发布、回滚和稳定引用；
- 支持节点、条件分支、重试、子流程、Interrupt 和人工节点；
- 固定流程型员工直接使用 LangGraph，混合型在节点中调用 Deep Agents；
- 编辑器开放 `workflow` 和 `hybrid`，禁止引用未注册流程；
- 任务、事件和状态仍只暴露统一平台协议；
- 两种员工代表性端到端流程通过。

### C12 定时与预约任务

**状态：`🚧 进行中`**

**开始日期：2026-07-17**

开工说明：前置 C03 已完成。与 C16 并行（互不构成直接依赖，修改边界可隔离）。实现分支 `task/c12-scheduled-tasks`（worktree `wt/c12`）；迁移编号占用 `20260716_0035`（down_revision=`20260716_0034`），按「先合入者保留编号、后合入者重链到当时单头」与 C16 协调——**C12 先合入，故保留 0035；C16 的 0036/0037 由主代理合并时重链**。本条目按阶段推进：阶段一后端调度主链（本轮），阶段二前端页面 + Playwright E2E。**C16 配额接入是本条目的完成前置**（2026-07-17 阶段重排后由 **C16 阶段三**承接，见 C16 条目强制门禁①；此处原写「阶段二」已随重排订正）。

2026-07-17 后端主链进展（本任务提交，先 RED 后 GREEN）：

- **调度语义**：Cron / 单次预约 / IANA 时区 / 启停 / `next_run_at` 全部落地。Cron 解析与 DST 语义委托 `cronsim`（`pyproject.toml` 声明 `>=2.6`，锁文件固定 2.7；零依赖、公开 API、无侵入），春季跳过的本地时间在当日下一个有效时间触发、秋季重复的本地时间只触发一次（fold=0 侧），均有断言 UTC 瞬时的用例覆盖；`schedule.py` 是全平台唯一直接依赖 cronsim 的位置。
- **不建旁路执行体系**：抽出唯一的 Run 创建共享路径 `run_dispatch.create_employee_run`，API 直跑、会话轮次派生与调度三方复用同一实现，调度产生的就是正常 Run + START 命令，由既有 Dispatcher/Worker 执行。
- **多副本安全（双重防线）**：`FOR UPDATE SKIP LOCKED` 认领任务行 + `(scheduled_task_id, scheduled_for)` 唯一索引兜底；执行记录先于 Run 落库，冲突即整事务回滚，绝不留孤儿 Run。已由真实 PostgreSQL 并发门禁 4 用例证明（两副本竞争同一触发点只产生 1 个 Run/1 条命令/1 条执行记录）。
- **策略**：misfire 支持 `skip`（默认）/`run_once`/`run_all`；`run_all` 受补跑窗口（默认 24h）约束、每跳补一个点，避免停机越久补跑越多的无界成本；misfire 判定不回溯枚举错过的触发点（分钟级 Cron 停机一年即数十万个点，枚举本身即无界）。并发策略支持 `allow`/`skip`（默认）/`queue`，`queue` 队列深度恒为 1、后续触发点合并为 `queue_collapsed` 历史。重试为指数退避 + 上限，次数用尽转终态 `failed`。
- **每次调度重新校验（fail-closed）**：创建者成员身份/`runs.execute` 权限、员工发布状态、发布版本的 `scheduled_tasks` 开关、输入对发布版 `input_schema` 的兼容性逐项重查；任一不满足则受控跳过并留下可见执行历史 + 自动暂停任务（避免每跳重复跳过导致历史无界增长）+ 审计 `scheduled_task.auto_paused`。**C16 配额校验的接入点已就位**：`platform/scheduling/guards.py::evaluate_dispatch_guards`。注意区分两类语义：现有守卫原因都是**配置性失效**（不会自愈），登记在 `_GUARD_PAUSE_REASONS` 中触发自动暂停；**配额是瞬态状态，新增的配额 `SkipReason` 必须不进入 `_GUARD_PAUSE_REASONS`**，不登记者会走 `_advance` 分支表现为「本次临时跳过、推进到下一触发点、任务保持启用」——这正是配额需要的语义。详见 C16 条目强制门禁①。
- **解除强制关闭**：`is_runnable_employee_definition` 与员工写契约不再把 `scheduled_tasks` 钉死为 `false`；历史已发布版本一律是 `false`，其解释与 C12 前完全一致（有专门用例锁定该不变量）。
- **API 与审计**：`/api/v1/scheduled-tasks` 提供 CRUD + 暂停/恢复 + 执行历史；权限沿用 runs 语义（统一要求 `runs.execute`，无 `runs.manage` 者只能操作自己创建的任务，他人资源按 404 处理）；创建/修改/暂停/恢复/删除全部接入 C14 统一审计协议（`emit_audit_event`，`resource_type=scheduled_task`），未另起审计通道。路由根已加入 `CORE_API_ROUTE_ROOTS`。
- **无界成本治理**：执行历史按 `scheduled_task_execution_retention_days`（默认 90 天）清理，只删终态、永不删活跃执行；调度循环随 API lifespan 运行（配置驱动间隔、每条独立事务、单条失败仅计数、成功失败同节流、可优雅取消、可由 `scheduler_enabled` 关闭），与既有审批超时/审计保留清扫同构。
- **Demo Seed**：幂等补齐 1 个启用中的工作日 Cron 任务 + 1 个暂停的单次预约 + 1 条成功执行历史（绑定终态 run，不会被 Worker 恢复扫描判为孤儿）。

本阶段验证命令：`cd backend && uv run pytest tests/unit tests/contract -q`（1453 passed）；`uv run pytest tests/integration/scheduling tests/integration/runs/test_run_dispatch.py tests/integration/database/test_migrations.py -q`；`TEST_DATABASE_URL=... uv run pytest tests/integration/scheduling/test_postgres_scheduler_concurrency.py -q`（真实 PG 4/4）；`uv run ruff check .`；`uv run mypy`（249 文件）。

2026-07-17 第二轮（独立双复审 FAIL 后集中整改，先 RED 后 GREEN）：

- **【阻断】暂停语义在派发路径缺失**：`_dispatch_pending_one` 全程不看 `task.enabled`，用户点暂停后，排队中的触发点与退避到点的重试仍会起新 Run（RED 实测 `assert 2 == 1`）。修复：拿任务行锁后判 `enabled`，并把该执行就地结算为 `skipped(task_paused)`（新增 `SkipReason.TASK_PAUSED` 与 `RETRY_WAITING→SKIPPED` 转换），否则它每跳被重复捞出、永不结算。原有「暂停不派发」用例只覆盖了认领路径，派发路径零覆盖。
- **【阻断】Demo Seed 写入产品自身契约禁止的状态**：演示员工发布版 `scheduled_tasks: false`，却给它种了 2 条定时任务——API 对该组合返回 409、调度守卫会把任务一到期就自动暂停，此前靠把 `next_run_at` 硬编码成 2027 远期时间掩盖。修复：演示员工发布版真正开启该能力；`next_run_at` 由 cron 表达式与时区真实推算（不再硬编码）；单次预约用暂停态而非伪造时间表达「先别真跑」；运行态字段（enabled/next_run_at/last_run_at/revision）移出可变字段，建后归调度器所有，重放 Seed 不冲掉调度进度。
- **自动暂停不再靠 CAS 巧合**：`_dispatch_execution` 改为返回 `SkipReason | None`，`_claim_one` 据此区分「已自动暂停 → 绝不 `_advance`」与「未暂停 → 推进到下一触发点」；此前两条 CAS 都用旧 revision，靠第二次静默失败才没把暂停解除。
- **冲突分类收窄**：`IntegrityError` 判定从包住整个 `_claim_one` 收到只包触发点插入那一条语句（savepoint 隔离）；非触发点的完整性故障（Run/命令/审计）不再被伪装成「另一个副本抢先了」静默丢弃，现在计入 `result.failed` 并触发告警（RED 实测 `assert 0 == 1`）。补了让该分支真正被生产路径执行的用例。
- **CAS 漏列补齐**：`update_with_cas` 补写 `misfire_grace_seconds` 与 `misfire_backfill_window_seconds`（RED 实测 `assert 60 == 17`），避免将来 API 暴露这两个字段时改动被静默丢弃。
- **补齐虚假声称的证据**：极端频率（`* * * * *`）此前声称覆盖实为零用例，现补 2 条（默认 SKIP 不堆积、ALLOW 每触发一个 Run）；lifespan 取消此前只有注释无断言，现断言任务具名、`cancelled()` 为真且无悬挂任务（移除 `cancel()` 后 lifespan 直接挂死，证明该路径承重）；「真实 PG 下调度循环与审计清扫并存正常」此前只有口头验证，现落为真实 PG 门禁用例；只读 session 的 idle-in-transaction 实测为块退出即 `idle`（无泄漏）。

2026-07-17 第三轮（双复审二次 FAIL 后整改，先 RED 后 GREEN，所有「已守住」表述均经变异验证）：

- **【M，自查失职】C-4 曾是空门禁**：上一轮写下「补回归门禁守住该性质」，但该用例只在**整跳结束后**采样 `pg_stat_activity`——那时所有 session 必已关闭，断言恒真。复审变异验证（把逐条循环挪进扫描 session）后它仍 passed。已重写为在**逐条处理进行中**采样：正确实现只应有 1 条连接在事务里（逐条 session 自己），扫描 session 若还开着就是 2 条。**变异验证**：挪进扫描 session → `assert [2] == [1]` 转红；还原 → 通过。这是同一失误模式（把不存在的证据写进台账）第二次出现，本轮已对全部「已覆盖/已守住」表述逐条做变异验证，结论见下。
- **【L】暂停分支状态守卫顺序**：`enabled` 判定排在状态守卫之前，多副本竞态下（副本 A 扫到 DEFERRED → 副本 B 抢先派发 → 用户暂停 → 副本 A 重读到 `enabled=False`+`DISPATCHED`）会对 DISPATCHED 调 `skipped()` 抛非法转换，被宽 except 接住计入 `failed` 并误报告警——恰好制造了 A-3 想消灭的反向问题。RED 实测 `InvalidScheduledTaskExecutionTransition: dispatched -> skipped` + `failed=1`。已把 `enabled` 判定移到状态守卫之后。
- **【G1】已派发执行无超时 → 定时任务可能永久静默停摆**：查证结论——孤儿恢复扫描 `recover_incomplete_runs` **兜不住**：它对 `waiting_for_input`/`waiting_for_approval` 是「恢复运行时」而非「终结」，只有 `running` 会被判孤儿失败。其中 `waiting_for_approval` 另有 C13 审批超时（默认 24h）驱动 run reject 兜底；**`waiting_for_input` 无任何机制终结**——调度产生的 Run 没有交互用户会回应，执行永久停在 DISPATCHED，`list_active_for_task` 恒返回它，SKIP/QUEUE 策略下该任务永久静默停摆，且 `purge_terminal_before` 只清终态、永不回收。已实现配置驱动的执行超时（`scheduled_task_execution_timeout_seconds`，默认 24h）：超时把**调度侧执行**结算为 failed，发告警日志与审计（`scheduled_task.execution_timed_out`），不去动 Run 本身（那是 C05/C13 的语义边界），也不触发重试。**超时严格限定在「无人终结」的状态**（`waiting_for_input`）——见下一轮对该范围的收窄。
- **【L5】日志张冠李戴**：`_wait_for_database_ready` 硬编码 `artifact_storage_reconciliation_*`，调度器等待 schema 时打的是 artifact 协调器的日志名。已参数化 `log_scope`，四个调用方各报自己的名字。

剩余未完成（不得据此标记完成）：C16 配额接入（本条目完成前置，由 C16 阶段三承接）。

2026-07-17 阶段二（前端页面 + Playwright E2E，本任务提交，先 RED 后 GREEN）：

- **解除前端对 `scheduled_tasks` 的硬关闭**：阶段一后端已不再把发布版 `capabilities.scheduled_tasks` 钉死为 `false`，但**前端仍留着 C12 前的硬关闭**——`isEmployeeConfigurationAvailable` 把该能力为真直接判成「配置不可用」、编辑器复选框 `disabled` 且保存时恒写 `false`。后果：用户无法通过界面给任何员工开启该能力，自建员工建定时任务必被后端以 409 `scheduled_tasks_disabled` 拒绝，完成定义第 5 条根本走不通。已按 RED→GREEN 解除（写入边界、编辑器回显与提交、以及「历史配置将被复位」提示一并清理）。
- **定时任务中心**：列表（调度、下次执行时间、启用/暂停 + 自动暂停原因）、创建/编辑弹窗（员工、Cron/单次预约、IANA 时区、输入 JSON、错过/并发策略、重试）、暂停/恢复、删除确认；详情页展示概览与执行记录（触发时间、状态、尝试次数、关联 Run、跳过原因/错误、下次重试）。导航与路由按 `runs.execute` 裁剪，路由由 `WorkspaceCapabilityGate` 兜底。
- **时区渲染**：`next_run_at`/`last_run_at`/`scheduled_for` 一律按**任务自己的 IANA 时区**渲染（`zoned-time.ts`），不用浏览器本地时区——否则用户会看到与自己填的 Cron 自相矛盾的「下次执行时间」。单次预约的当地时间↔UTC 换算按两趟偏移校正，跨 DST 边界正确。**变异验证**：把渲染改成浏览器本地时区后，两条时区 E2E 立刻转红（实测显示 21:00/22:00 而非 09:00），证明用例承重。
- **Cron 校验不自建解析器**：前端只做与后端一致的必填/长度校验，表达式合法性交给后端 `cronsim` 判定并把 `invalid_cron_expression` 转为字段错误——自建解析器要么放宽要么误伤合法表达式。时区只从运行时 `Intl.supportedValuesOf('timeZone')` 选，与后端 `ZoneInfo` 判定一致（固定偏移两侧都不接受）。
- **高频 + ALLOW 提示（非门禁）**：按主代理拍板，频率下限治理归 C16 阶段三，前端不设阈值拦截；仅当分钟字段明确高频（`*` 或 `*/n`，n≤5）且并发策略为 `allow` 时给出可见提示，如实告知平台当前不限制调度频率。
- **Playwright E2E**（`frontend/e2e/scheduled-tasks.spec.ts`，4 用例全绿）：① 时区——页面选 `America/New_York` 建 Cron，回显当地 09:00 且库中 UTC 为 13:00/14:00；② DST——同一当地 09:00 在冬/夏令时分别落到 14:00Z/13:00Z，回显都是 09:00；③ 重复触发 + 重启恢复——两个调度副本竞争同一触发点只产生一条执行记录、无孤儿 Run，整组 SIGKILL 后历史冻结、重启后继续推进且无任何触发点重复；④ 权限——同企业 member（有 `runs.execute`、无 `runs.manage`）看不到他人任务，直达详情与带 owner 租户头的 GET/executions/pause/resume/DELETE 一律 404，列表返回空且任务未被越权删除。
- **调度进程承载方式**：调度循环随 API lifespan 运行、无独立入口，故 E2E 用独立 API 进程承载它（`AGENT_PLATFORM_SCHEDULER_TICK_INTERVAL_SECONDS=1`），Playwright 管理的 API 关闭调度器（`playwright.config.ts`）避免竞争。**自查发现并修正的假通过**：`uv run` 会再 fork 出真正的 uvicorn 子进程，最初只 SIGKILL `uv` 包装进程会把调度器留成孤儿（实测 ppid 变 1 且端口仍返回 200），「重启恢复」用例因此**没真正杀掉调度器**、属于因错误原因通过。已改为 `detached` 进程组整组杀 + 以「端口不再服务」为退出判据 + 增加「停机窗口内历史必须冻结」对照断言；**变异验证**：改回只杀包装进程后用例立即转红（`调度进程 … 没有真正停止服务`）。

2026-07-17 阶段二第二轮（独立双复审均 FAIL 后按根因集中整改，先 RED 后 GREEN）：

- **【高危，正确性】`zonedWallClockToUtc` 的「两趟偏移校正」在 DST 边界不成立**：该做法等价于解不动点方程 `offset(t) = asIfUtc - t`，而它在春季跳变缺口内**无解**、在秋季重复小时内**有两解**；二趟迭代既不检测也不消歧，只会静默返回不自洽的瞬时。两个同源症状：① 春季不存在的当地时间（`2026-03-08T02:30` America/New_York）返回 `06:30Z`（= 01:30 EST，落在切换**前**，比用户所填还早 1 小时），且与当时注释声称的「落到切换后」**方向相反**，此前**零用例覆盖**；② 秋季重复小时第二次出现的瞬时往返漂移 1 小时。已按枚举 + 校验重写：用边界两侧偏移各算一个候选，以「候选渲染回当地时间是否等于用户所填」判定候选是否真实存在——1 个成立即普通情况；2 个成立即重复小时，按 `disambiguation` 取前/后；0 个成立即跳变缺口，按 `disambiguation` 显式选择切换前/后一侧。消歧语义与 `Temporal.ZonedDateTime` 对齐（`compatible` 默认：缺口后移、重复取第一次）。**不引第三方依赖**：`Temporal` 在本机 Node 26 仍为 `undefined`（实测），用它需再引 polyfill；`date-fns-tz` 为约 30 行标准逻辑引入运行时依赖不划算。新增用例覆盖跳变缺口、fold 两次出现、`Australia/Lord_Howe` 的 **30 分钟** DST（非整小时偏移，任何按 1 小时硬编码的实现都会露馅）。
- **【高危，命中真实用户路径】编辑 `once` 任务的 UTC→当地→UTC 回环导致静默提前**：当地时间字符串**不携带 fold 标识**，回环必然丢信息，因此显式消歧也救不了——全年扫描（每 30min）实测：修复消歧后 `America/New_York` 仍有 2 个、`Pacific/Chatham` 2 个、`Australia/Lord_Howe` 1 个瞬时不回原点，且**恰好全是 fold 第二次出现**。根治办法是**不做这个回环**：编辑 `once` 任务时若用户未改动预约时间与时区，直接原样复用原 `run_at`（逐字，连格式都不重写），只有真改了才重新换算。此前用户编辑一条落在 fall-back 重复小时的 `once` 任务、**只改名字**保存，任务就被静默提前 1 小时。
- **【原往返用例是教科书式假绿】**：旧用例取样 `2026-11-01T05:30:00.000Z`——同一 fold 的**第一次**出现，恰好能过；与失败样本仅差 1 小时。选中了通过的那个、漏掉了失败的那个，于是 146 个用例全绿的同时函数在静默返错。已补 fold 两次出现的显式往返用例。
- **【完成定义第 5 条点名的「编辑」「暂停」此前只有组件测试】**：项目规则明写组件测试不能单独作为该类功能的完成依据，而上一轮台账「剩余未完成」只写了 C16 配额、**漏登记该 E2E 缺口**，属文档措辞掩盖代码缺口。已补 3 条 Playwright：改 Cron 与时区后列表回显同步更新；**落在 DST 重复小时的 `once` 任务只改名字保存、时刻逐字不变**（正面钉住上述回环缺陷，**变异验证**：撤掉复用修复后该用例立即转红，`Expected 2027-11-07 06:30 / Received 2027-11-07 05:30`）；暂停→恢复在界面与库中同步生效且恢复后重算 `next_run_at`。
- **【竞态用例存在真实假绿面】**：原「无重复 `scheduled_for`」断言在**只有一个副本真正 tick 时同样成立**（去重靠唯一约束），而当时只有 health 探活——活着 ≠ 在调度。且单个每分钟任务下先抢到的副本会吃掉全部触发点，「两副本各自认领过」本身就不确定、直接断言会 flaky。已改为确定性构造：一次放出 **30 个同时到期**的任务并把 `SCHEDULER_TICK_BATCH_LIMIT` 设为 1，两副本只能交替认领；并给每个副本单独落盘日志（后端未配 dictConfig，默认 formatter 会丢弃 `extra`，故 E2E 用**测试专用** `--log-config` 让 `dispatched/skipped` 真正打进日志，未改动任何后端代码），断言两副本**各自**认领数 > 0 且合计覆盖全部触发点。**变异验证**：只起一个副本时新断言立即转红（`claimedByB` `Expected: > 0 / Received: 0`），而旧的「无重复」断言在同一条件下照样通过——正是被堵上的那个假绿面。
- **【注释与代码不符】**：`skipReasonLabels` / `pauseReasonLabels` 的注释称「缺项会让用户看到空白标签」，实际调用方 `?? item.skip_reason` 回落展示原始机器码。已改为与行为一致的表述。同类问题本轮共两处（另一处即上述 DST 注释方向写反），均已修正。

阶段二验证命令：`cd frontend && pnpm test`（61 文件 / **338** 用例全绿）；`pnpm lint`；`pnpm typecheck`；`pnpm build`；`pnpm exec playwright test scheduled-tasks.spec.ts`（**8/8**，随机项目名 + 随机端口隔离栈，验后销毁并复查容器/网络/卷与孤儿 uvicorn 均为 0）；`pnpm exec playwright test`（默认全量 **41 passed / 1 failed**，唯一失败为下方红线 3 登记的既有失效用例）。

阶段二已知缺口（如实声明）：

- **「无 `runs.execute` 的成员看不到入口」无法用真实角色做 E2E**：`_ROLE_PERMISSIONS` 中 owner/admin/member **三个角色都含 `runs.execute`**，产品当前不存在缺该权限的真实角色，E2E 无法构造这样的用户而不伪造角色。该分支由 `App.test.tsx` 的组件级门禁用例覆盖（隐藏入口 + 直达 `/scheduled-tasks`、`/scheduled-tasks/:taskId` 显示统一 403）。真正可达且已 E2E 覆盖的是「有 `runs.execute`、无 `runs.manage` 的成员访问他人任务得 404」。若将来引入只读角色，须补该分支的真实 E2E。
- **`zonedWallClockToUtc` 取候选用的「前后各一天」是 tzdb 的经验性质、不是不变式**（T3 二轮质量复审登记，非阻断）：复审用日粒度扫 418 时区 / 1970–2040 / 20968 次转换，**间隔 < 3 天的转换对 = 0**；又用小时粒度全扫排除「同日内两次转换互相抵消」，仍为 0；并写了**不依赖 ±1 天假设的独立 oracle 对拍**（79 个去重规则时区 × 全部转换 × ±3h/15min × 3 种消歧 = 154,575 样本，0 不一致）。结论：当前 tzdb 下成立。**但未来 tzdb 若出现间隔 < 2 天的转换对，该假设会静默失效**——已在函数 docstring 记明取值理由。
- **`timezoneOffsetMs` 仅在秒对齐瞬时稳定**（同上，当前无缺陷）：非秒对齐瞬时会返回抖动偏移（复审自己的 harness 曾踩到，得出 `-5.000000277` 并二分收敛到亚毫秒伪边界、误报 21 个反例，秒对齐后归零）。生产只在分钟对齐处调用，故当前无缺陷，但对后续调用者是潜在陷阱。
- **`once` 任务落在 DST 重复小时时，界面只能寻址第一次出现**：表单按 `compatible` 消歧，用户在 fall-back 重复小时里填的当地时间恒解析为第一次出现；要精确安排在第二次出现只能通过平台 API 直接给 UTC `run_at`（E2E 即以此构造该前置）。**性质订正（主代理据 T3 二轮规格复审）**：原文写「这是当地时间不带 fold 标识的**固有信息缺失，非缺陷**」——**措辞过头，已改**。`zonedWallClockToUtc` 的 `disambiguation: 'later'` **已经实现**，缺的只是表单没暴露这个选择器；本条下一句自己也承认「需在表单显式提供选择」，前后矛盾。准确表述是：**这是「未暴露消歧 UI 控件」的产品取舍，不是物理不可能。**

**不判为完成定义第 5 条的缺口**，理由：第 5 条要求「客户端提供创建、编辑」这项能力存在，未要求覆盖每个 DST 角落；`compatible` 是 `Temporal` / `java.time` / RFC 5545 的行业标准默认，选标准默认站得住；用户拿到的始终是一个与所填当地时间自洽的合法瞬时，损失局限在每时区每年 1 小时窗口的第二次出现。真正有破坏性的变体——**编辑时静默移动既有任务**——才是真缺陷，已修复并由 E2E 用例 4 正面钉住。已保证**既有任务不会因编辑被静默改动**。

**若将来需要界面寻址第二次出现**：表单显式提供「第一次/第二次」选择即可，底层 `later` 无需改动。
- **执行历史保留与 Demo 数据的张力未决**：阶段一登记的 `_DEMO_STARTED_AT` + 90 天保留将在约 2026-09-29 后清掉演示执行历史；本阶段未改动该策略（属独立产品取舍，且不影响页面正确性），仍待决定延长保留或改用相对时间。

2026-07-17 第四轮（代码质量复审 FAIL 后整改，先 RED 后 GREEN，新增/修改门禁均经变异验证）：

- **【M】超时机制误杀健康长跑 Run**：第三轮我把超时的正当性完全建立在「`waiting_for_input` 无人终结」上（该论证属实），但**实现的触发条件是「所有非终态」**——实现范围远大于论证范围。后果：一个正常跑着的 Run 超过 24h 就被记为 failed；它随后真正 COMPLETED 时执行已离开 `list_dispatched` 集合，**永远不会被回填**，用户历史里留下与事实相反的失败记录。配置下限 `ge=60` 让该路径极易触达（运维把超时调到 5 分钟就会误杀所有 >5min 的正常 Run）。RED 实测：`running`/`queued`/`waiting_for_approval` 三态各自被误杀 + 健康 Run 成功后仍为 failed。**已收窄为 `_UNTERMINATED_RUN_STATUSES = {waiting_for_input}`**（外加「run_id 非空但 run 行已消失」这一当前不可达的防御性分支——全仓无任何删除 Run 的代码路径）。逐状态的终结者论证：`running` → Worker `recover_incomplete_runs` 判孤儿失败；`queued` → 命令进死信后由死信结算（`_settle_valid`）驱动 run FAILED；`waiting_for_approval` → C13 审批超时驱动 reject；只有 `waiting_for_input` 无任何终结者。
- **【L】超时结算补审计**：`scheduled_task.execution_timed_out`（此前只有日志），与派发/自动暂停一致。
- **【L】`skipped` 指标失真**：`_dispatch_pending_one` 的所有 `False` 返回都被计入 `result.skipped`，良性竞态（执行已被别副本推进、上一轮仍在跑）混进业务跳过计数。已改为三值 `_PendingOutcome{DISPATCHED,SKIPPED,NOOP}`，NOOP 不计入任何业务指标。
- **【LOW】C-4 门禁收敛**：docstring 概括为「扫候选 session 必须在逐条处理前关闭」，但采样只经 `create_employee_run`，实际只覆盖 `_claim_due_tasks` 一相。**已把三个相位全部纳入采样**（参数化 `_settle_one`/`_dispatch_pending_one`/`_claim_one`），并对每一相各自做变异验证（把该相位的扫描 session 按住不放 → 三条全部独立转红）。选择扩大门禁而非收窄措辞，因为「声称比证据宽」已是我连续两轮的失误模式。

2026-07-17 第五轮（双复审均 PASS 后的收尾整改，均为文档/注释级）：

- **【规格 LOW】`run 引用已丢失` 的因果方向写反**：注释与第四轮记录都写「FK `ondelete=SET NULL` → 触发超时」，但 SET NULL 产生的是 `run_id IS NULL`，命中的是更早的 `if execution.run_id is None: return False`，**根本走不到超时**。已改为准确表述：真正能进 `run is None` 的是「run_id 非空但 run 行消失」，而 SET NULL 恰恰阻止这种形态；两个分支当前都不可达（全仓无删除 Run 的代码路径）。**选择改措辞而非把 `run_id is None` 纳入超时**，理由：为不可达状态预建代码路径属于为假设性未来需求过早抽象；且将来真引入 Run 删除时，正确答案未必是超时（级联删执行记录、或删 Run 时就地结算执行可能更好），预建会把后来者引向可能错误的方案。改为在代码原地与 C20 各留一条前向门禁。
- **【质量 L-1】`queued` 终结者论证的条件性未声明**：已改为条件表述（见下方已知局限）。
- **【G】游离 `@pytest.mark.asyncio`**（落在辅助函数 `_prepare_phase` 上的重命名残留，不被收集、无害）已删。

已知局限（如实声明，非缺陷）：

- **`queued` 的终结者论证条件于队列消息未丢失**：正常路径下命令重投失败会进死信、由死信结算驱动 run FAILED，因此 `queued` 无需调度侧超时。但若 Redis 整体丢数据（无持久化重启），命令已离开 `pending()`、DLQ 也无记录、`list_recovery_candidates` 又不含 QUEUED → run 永久停在 `queued`、执行永久 DISPATCHED、SKIP 任务永久静默停摆。触发条件：Redis 数据丢失这一基础设施级故障。修复归属队列层（QUEUED 对账器）而非调度器，超出 C12 范围。注：该场景为静态推断，未构造 Redis 丢数据实测。
- **超时之后 `ConcurrencyPolicy.SKIP` 不再保证「同一时刻只有一个 Run」**：超时结算解除了 `list_active_for_task` 闸门，而被卡住的那个 Run（`waiting_for_input`）可能仍活着，下一个触发点会正常派发，因此可能出现两个 Run 并存。这是**刻意取舍**：替代方案是任务永久静默停摆，明显更糟。用例 `test_a_timed_out_execution_unblocks_the_task_for_later_triggers` 固化了该行为。触发条件：仅当某个执行的 Run 卡在 `waiting_for_input` 超过 `scheduled_task_execution_timeout_seconds`（默认 24h）。
- `_DEMO_STARTED_AT`（2026-07-01）+ 执行历史保留 90 天 → 约 2026-09-29 后常驻开发栈的清理循环会删掉演示执行历史；重放 Demo Seed 可恢复该历史记录，但与「Demo 数据默认保留」略有张力，待前端任务接入时一并决定是否延长保留或改用相对时间。

完成定义：

- 支持 Cron、单次预约、时区、启停和下次执行时间；
- 调度产生正常 Run/Command，不建立旁路执行体系；
- 支持错过执行、并发策略、重试、暂停和历史；
- 已有权限和员工发布状态在每次调度时重新校验；C16 引入配额后同步把配额校验接入调度入口；
- 客户端提供创建、编辑、暂停和执行记录；
- 时区、重启恢复、重复触发和权限 E2E 通过。

### C13 独立审批中心

**状态：`✅ 已完成`**

**开始日期：2026-07-17**

**完成日期：2026-07-17**

开工说明：前置 C03 已完成。与 C10 并行。实现分支 `task/c13-approval-center`（worktree `wt/c13`）；迁移合入时按「先合入者保留编号」重链——C10 记忆迁移取 0029，本条目 `20260716_0030` 的 down_revision 重链至 0029 保持单头。

2026-07-17 完成记录（本任务提交，merge 合入，先 RED 后 GREEN）：审批领域与状态机（迁移 `20260716_0030`，pending→approved/rejected/expired/withdrawn/transferred 全终态封死，转交产生新 pending 双向链接、不重置超时）；`/api/v1/approvals` 提供待办列表/详情/批准/拒绝(必填理由)/转交/历史，超时以决策/读取惰性判定为权威 + 后台清扫兜底（配置驱动间隔、每租户独立事务、单条失败仅计数），撤回、request_key 建记录幂等 + decision_key 决策幂等，系统通知走平台 run 事件不引第三方。**Tool 风险审批接入统一协议不另起旁路**：deep agent interrupt → worker 落 APPROVAL_REQUIRED 事件（携 tool_name/arguments 快照）→ 同事务 `sync_run_approvals` 以 `request_key=tool:{run_id}:{approval_id}` 幂等建 Approval → `ApprovalService` 决策统一驱动既有 run approve/reject 命令 + 事件 + 审计；run control 入口 `control_run` 复用 `decide_by_invocation`，**run 处于 WAITING_FOR_APPROVAL 却查无审批记录时 fail-closed 409（不回退 legacy 直发 raw 命令，堵旁路窗口）**。审批决定与 Run/Tool Invocation/用户/审计四方可追溯（resource_type=approval，脱敏）。前端审批中心页面 + 工作台待审批卡片；Demo Seed 幂等 1 pending + 1 approved 历史。**修复的真实生产缺口**：`run_worker_service`（worker 进程）从不装配审计 HMAC（此前只有 `create_app` 装配），C13 作为首个从 worker 投递路径写审计的功能首次暴露——worker 现按与 API 完全相同的 `configure_audit_hashing(app_settings.audit_hmac_key.get_secret_value())` 同源装配、同一条 HMAC 链、dev/test 回退与 staging/production fail-closed 一致（双复审逐字核对确认）。双复审：首轮质量复审 FAIL 抓到 [M] run 控制 fail-open 旁路、[M] 并发/CAS 只在 SQLite 验证、[L] invocation_id=None 守卫缺失、[L] 假并发测试命名，退回集中修复；修复后 fail-closed 堵旁路 + 新增真实 PostgreSQL 并发门禁 3 用例（并发决策只一方胜/并发同 request_key 只留一条/清扫 vs 决策 CAS 一致）+ invocation_id 守卫 + 测试改名，主代理接管复跑全部通过。合入时解决与 C10 的 4 处共享文件冲突（导航/testMatch/demo_seed ID/迁移测试双函数语义重建）并重链迁移。验证命令：`cd backend && uv run pytest tests/unit/platform/approvals tests/contract/approvals tests/integration/queue/test_run_worker_approvals.py tests/integration/database/test_migrations.py -q`（含审批 86）；`TEST_DATABASE_URL=... uv run pytest tests/integration/approvals -q`（真实 PG 3/3）；`uv run pytest -q`（合并后全量 1399 passed, 45 skipped）；`uv run ruff check . && uv run mypy`（212 文件）；`cd frontend && pnpm test`（48 文件 242）+ lint/typecheck/build；`infra/platform/test-runtime-e2e.sh` 审批 3 用例 + 合并后审批 3 + 记忆 2 交叉验证全绿（随机隔离栈，验后清零）。

完成定义：

- 建立 Approval 记录、状态、审批人/角色、风险和业务上下文；
- 提供待办列表、详情、批准、拒绝、理由、转交和历史；
- 支持超时、撤回、重复请求幂等和系统通知；
- Tool 审批、工作流审批和未来能力包审批复用同一平台协议；
- 审批决定与 Run、Tool Invocation、用户和审计记录可追溯；
- 并发审批、越权、过期和 Playwright E2E 通过。

### C14 全平台审计、Metrics、Logs 与告警

**状态：`✅ 已完成`**

**开始日期：2026-07-16**

**完成日期：2026-07-17**

2026-07-17 隔离验收栈终验记录（本任务提交）：`bash infra/platform/test-mvp-profile.sh` 完整通过（exit 0，随机项目名 `agent-platform-mvp-test-27090`，验后容器/网络自动销毁、保留卷断言通过）——镜像构建与迁移（含 `20260716_0025` HMAC TOFU 回填）、Demo Seed、Playwright mvp-profile 3 项（含附件→真实 Agent→派生产物全栈闭环）、真实 Tauri 桌面 wdio 1 项、生产 Worker→LiteLLM→Stub 链路、真实 PostgreSQL 并发下 artifact 租户边界与 Saga claim/CAS/renewal、profile 状态/保留卷/失败重启清理/并发启动拒绝/工作树镜像隔离全部通过。前两轮同套件功能测试全绿，但执行环境缺 `rg` 二进制导致脚本自身断言步骤 exit 127 误判失败；已把 `infra/platform/test-mvp-profile.sh`（10 处）与 `infra/platform/test.sh`（2 处）的 `rg --quiet` 替换为 POSIX `grep -q`/`grep -Eq`，消除未声明的工具依赖（同批提交）。日志中 postgres 的 duplicate key/FK 报错为 PG 并发约束测试故意触发的负路径，非缺陷。

2026-07-16 复审退回记录：实现位于 `task/c14-audit-observability` 分支（`d310b52`、`f8edb27`、`6923a52`），独立代码质量复审结论为 FAIL，退回实现状态，禁止按当前 HEAD 合入。阻断项：S1 审计失败指标全链路未接线（`repositories/audit.py` 从不记录 `audit.events.failed`，critical 告警永不触发，单测直接构造终态制造覆盖假象）；S2 审计保留仅有 `purge_before` 库级原语，无任何调度或端点，审计表无界增长；S3 注册流程对同一语义重复写入 5 条审计事件且 `tenant_membership`/`tenant_member` 两套 resource_type 并存；S4 每租户序列并发只在 SQLite（`with_for_update` no-op）验证；S5 多个已声明指标 operation 为死代码。修复须先补 RED 测试再集中修复，并基于新 HEAD 重新执行双重复审。合入时必须剔除该分支夹带的 `12c58da` CLAUDE.md 串行规则改动（用户已取消，撤销提交 `0abbbf1` 在 C07 分支上）；该分支迁移 `20260716_0024` 与 B04 分支同号，先合入者保留 0024，后合入者改号。

2026-07-16 阻断项修复记录（本任务提交，状态保持进行中，等待重新双重复审）：已先合入最新 main（CLAUDE.md 取 main 版本，`git diff origin/main -- CLAUDE.md` 为空，`12c58da` 夹带的串行规则改动已被 main 覆盖）。逐项修复（均先 RED 后 GREEN）：S1 审计仓储 `add`/`verify_integrity`/`purge_before` 经真实写入路径记录 `OperationalMetrics`（AUDIT persist/verify/retention），`Telemetry` 构造时注册进程级 metrics 供仓储回退使用，唯一约束冲突与 DB 异常均触发 `agent_platform.audit.events.failed`，critical 告警链路打通；同时删除 `test_operational_metrics.py` 中 AUDIT 直接构造终态的假覆盖，真实路径断言移至 `tests/unit/observability/test_audit_metrics.py`。S2 复用 C04 后台清扫模式新增配置驱动的审计保留清扫：`audit_retention_days`/`audit_retention_sweep_interval_seconds`/`audit_retention_sweep_batch_limit`，API lifespan 常驻任务按固定间隔（成功失败同节流）调用 `purge_expired_audit_events` 逐租户清理并保持哈希链可校验，失败仅记录受控日志。S3 注册流程审计事件去重为每语义恰好一条（`auth.registered`/`tenant.member_added`/`tenant.role_assigned`），resource_type 全仓统一为 `tenant_membership`。S4 新增 `tests/integration/audit/test_postgres_audit_sequence_concurrency.py`，按既有 `TEST_DATABASE_URL` 门禁在真实 PostgreSQL 上验证 12 并发写入序列唯一、连续且完整性校验通过（本机临时 PG 容器实测通过，无 PG 时条件跳过）。S5 删除声明但从不记录的死 operation（WORKER recovery/heartbeat、QUEUE setup/ack/reclaim、MODEL_GATEWAY chat、SANDBOX heartbeat/file/command、RAGFLOW health）。S6 删除迁移测试中 `audit_chain_state_columns` 的重复计算与重复断言。S7 已知局限：审计哈希链为纯 SHA-256 链接、无 HMAC 密钥与外部锚定，可检测常规篡改，但无法防御能够全量重写数据库（含链头状态）的攻击者伪造整条链；如需更强不可抵赖性需引入密钥化签名或外部锚定，本轮不实现。验证命令：`cd backend && uv run pytest tests/unit/observability tests/contract/audit tests/integration/database/test_migrations.py -q`；`uv run pytest tests/unit tests/contract -q`；`TEST_DATABASE_URL=... uv run pytest tests/integration/audit -q`（真实 PostgreSQL）；`uv run ruff check . && uv run mypy`。

2026-07-16 第二轮复审修复记录（本任务提交，状态保持进行中，等待重新双重复审）：逐项先 RED 后 GREEN。M1 保留清扫由“单 session 遍历全租户、循环结束才一次 commit”改为每租户独立事务提交：`purge_expired_audit_events` 改收 `async_sessionmaker`，逐租户开独立 session 清理并提交，单租户链锁不再跨越后续租户处理期（此前首个租户 `audit_chain_states` 行锁持有到全部租户处理完，期间该租户所有审计写入被整段阻塞）；单租户失败仅记录受控日志并计入 `AuditRetentionSweepResult.failed_tenants`，其余租户照常清理，部分成功语义显式返回，`api/app.py` 分别记录 purged/partial-failure 日志。M2 `_verify_integrity` 由一次性 `all()` 物化整租户审计表改为按 sequence 键集分页滚动校验（`batch_size` 默认 1000、区间 1..10000），滚动哈希前缀语义不变，篡改、跨块边界篡改、尾删检测契约测试全部保持通过；RED 用例以 SQL 语句计数断言按块查询先失败。L1 `Telemetry.shutdown()` 复位自己注册的进程级 `_active_operational_metrics`（仅当全局仍指向自身时复位，不覆盖更新 Telemetry 的注册值），消除 shutdown 后残留已关闭 meter 污染同进程后续用例。L2 仓储 `_record_metric` 增加受控异常隔离（吞异常记 debug 日志），指标 instrument 抛异常不再回滚/阻断已成功的审计写入。G1 修复前端 `src/features/operations/api/audit.test.ts` 断言笔误（mock 返回 `sequence: 2` 却断言 3，该用例自创建起未通过过，属测试笔误非产品缺陷），修复后 `src/features/operations` 5 文件 16 项真实通过。遗留 follow-up（本轮不实现，后续按需处理）：L3 审计写入 flush 成功但外层事务 commit 阶段失败时不计入 `audit.events.failed` 的窄盲区；L4 `ToolAuditSink`（`tool_audit_events` 通道）持久化失败不进审计失败指标，审计失败指标的覆盖范围界定待明确；L5 `/client-events` 上报无速率限制，客户端可高频上报制造告警投毒面；S7 哈希链无 HMAC/外部锚定局限持续有效。验证命令与结果：`cd backend && uv run pytest tests/unit/observability tests/contract/audit tests/integration/database/test_migrations.py -q`（68 项通过）；`uv run pytest tests/unit tests/contract -q`（896 项通过）；`uv run ruff check . && uv run mypy`（185 个源码文件通过）；临时 PG 容器下 `TEST_DATABASE_URL=... uv run pytest tests/integration/audit -q`（7 项通过，容器已删除）；`cd frontend && pnpm exec vitest run src/features/operations --reporter=dot`（5 文件 16 项通过）。

2026-07-16 S7 HMAC 密钥签名加固记录（本任务提交，`task/c14-audit-hmac` 分支，先 RED 后 GREEN）：审计哈希链由纯 SHA-256 升级为 HMAC-SHA256 密钥签名。密钥来源：`AGENT_PLATFORM_AUDIT_HMAC_KEY`（`AppSettings.audit_hmac_key`，SecretStr，绝不落数据库、绝不进日志）；staging/production 缺失、短于 32 字符或等于公开开发密钥时 Settings 校验直接拒绝启动，运行期未装配密钥时审计写入与校验抛受控 `AuditHmacKeyNotConfiguredError` fail-closed，不存在无密钥哈希回退路径；local/development/test 默认回退公开开发密钥 `agent-platform-insecure-dev-audit-hmac-key` 保证本机开箱可用。算法标识：`audit_events.hash_algorithm` 列（legacy `sha256` / 新事件 `hmac-sha256.v1`，带版本号为多密钥轮换预留），HMAC 载荷纳入算法标识做域隔离。链头封印：`audit_chain_states.head_seal`/`head_seal_algorithm` 为链头+保留边界（head_sequence、head_hash、retained_from_sequence、retention_previous_hash）的 HMAC 封印，写入与保留清扫时同步重算；校验时封印缺失或不匹配直接判定失败，使能够全量重写数据库（含链头）的无密钥攻击者无法伪造自洽链。存量兼容：迁移 `20260716_0025` 增列并对存量链头一次性封印回填（TOFU，以迁移时刻状态为信任起点；存在存量链头而非开发环境缺密钥时迁移 fail-closed）；校验按事件各自算法执行，legacy 事件只允许作为链前缀，HMAC 事件之后再出现 legacy 事件即判定完整性失败（禁止静默降级）。开发栈配置同步：`infra/compose/platform.yml` 注入 `AGENT_PLATFORM_AUDIT_HMAC_KEY`（默认开发密钥）、`.env.platform`/`.env.platform.example` 与 `infra/platform/mvp-profile.sh`（生成、允许列表、清理）补 `AUDIT_HMAC_KEY`，既有 `agent-platform-dev` 栈无需重建 env 即可工作。**S7 局限大幅收窄（非完全解除）**：无密钥全量重写攻击已被检出（RED 用例证明旧实现对伪造链返回 valid=True）。剩余威胁面如实声明：a) 持有服务端 HMAC 密钥的攻击者；b) 将整个数据库回滚到历史合法快照的攻击者——历史封印对历史状态自洽、无需密钥即可通过校验，防御需要外部锚定（如定期把链头封印写入对象存储/外部公证）或单调性对账，归 C18。**密钥轮换与外部锚定本轮不做，列为 C18 跟进项**（算法标识与封印算法列已为多密钥版本预留）。安全复审后追加硬化：事件哈希校验改用常量时间比较（hmac.compare_digest）；HMAC 事件载荷加入 purpose 用途域（与链头封印 purpose 域隔离，legacy 载荷字节保持不变）。运维注意：迁移 0025 的 downgrade 会丢弃 hash_algorithm 列，若库中已有 HMAC 事件，降级后再升级会把它们误标为 legacy 导致整链校验失败（fail-closed 非安全漏洞，但对 HMAC 数据不可逆），生产降级前必须备份。新增 RED→GREEN 用例：错误密钥校验失败、无密钥全量重写攻击检出、legacy+HMAC 混合链通过、HMAC 后降级拒绝、密钥缺失 fail-closed、保留清扫封印续算、保留边界篡改检出、迁移封印回填与 downgrade、Settings 密钥策略、`create_app` 装配。验证：`cd backend && uv run pytest tests/unit/observability tests/contract/audit tests/integration/database/test_migrations.py -q`（78 项通过，含既有篡改/尾删/跨块契约全部保持通过）；`uv run pytest tests/unit tests/contract -q`（917 项通过）；`uv run ruff check . && uv run mypy`（187 个源码文件通过）；临时 PG 容器 `TEST_DATABASE_URL=... uv run pytest tests/integration/audit -q`（7 项通过，容器已删除）；`python3 -m unittest discover -s infra/platform -p 'test_contract.py'`（44 项通过）。

完成定义：

- 建立通用平台审计协议、存储、查询和扩展接口，先覆盖当前已有认证、成员、权限、员工、任务、知识、Skill 和 Tool；
- 审计支持租户隔离、筛选、导出、保留和敏感字段脱敏；
- OpenTelemetry 同时接入 Trace、Metrics 和结构化 Logs；
- 建立 API、Worker、队列、模型网关、RAGFlow、沙箱和客户端关键指标；
- 提供运维 Dashboard、告警规则、关联 ID 和故障定位入口；
- 后续 C15-C17 新增的企业管理、模型治理和能力授权必须在各自任务中接入同一审计协议；
- 审计不可抵赖、日志脱敏、指标和告警测试通过。

完成说明：

- 后端新增通用 `audit_events` 存储、Repository、租户隔离查询、JSONL 导出、完整性校验和按时间保留清理；审计事件在仓储边界统一递归脱敏，并通过租户内递增序号、前序哈希和事件哈希形成可校验链；
- 当前已有认证、员工、任务、知识、Skill 和 Tool 关键操作已接入统一审计协议；后续 C15-C17 的成员、权限、模型治理和能力授权操作必须在各自任务中接入同一协议，不得另起审计通道；
- FastAPI、SQLAlchemy、HTTPX、Redis 和结构化日志已接入 OpenTelemetry；API 请求数、耗时和 Trace/Logs 具备 correlation_id 关联；Worker、Redis 队列、模型网关、RAGFlow 客户端和 Sandbox Provider 已通过低基数 `OperationalMetrics` 记录操作次数、耗时和失败计数；
- `infra/observability/alert-rules.yml` 固化 API、Worker、队列、模型网关、RAGFlow、沙箱、客户端和审计写入失败的指标名与告警规则；当前已有 API、Worker、队列、模型网关、RAGFlow 和沙箱指标真实接入，后续客户端错误和审计写入失败按同一低基数指标协议补齐；
- 前端新增“审计与观测”运维入口，可查看审计事件、关联 correlation_id、打开本机 Jaeger，并标明 JSONL 导出接口。

### C15 企业、成员与完整账号体系

**状态：`✅ 已完成`**

**开始日期：2026-07-17**

**完成日期：2026-07-17**

开工说明：前置 C14 已完成合入。与 C11 并行。分支 `task/c15-account-management`；迁移合入时按「先合入者保留编号」重链——前序 B04/C11 占 0031-0033，本 account 迁移重编为 `20260716_0034`（接 0033 单头）。

2026-07-17 完成记录（本任务提交，merge 合入，先 RED 后 GREEN）：企业设置 + 成员邀请（受控 token、有效期、接受/拒绝）+ 成员列表 + 角色变更 + 移除 + Owner 转移（迁移 `20260716_0034` 建 tenant_invitations/account_tokens 表 + user display_name / session user_agent）；服务端强制 Owner 唯一性与最后一个 Owner 保护（lock_members 全成员行 FOR UPDATE 锁内校验，真实 PG 并发门禁验证并发降级不归零 Owner）；**change_member_role 拒绝直接授 Owner（422，Owner 只能经转移产生）**。账号体系：资料/改密（校验旧密码）/邮箱验证/找回密码/会话设备管理（列出、撤销单个/全部，撤销后 token 立即失效）；**找回密码防枚举**——响应体恒等 202 为主防线 + `password_reset`/`password_reset_ip` 端点限流（5/min，Redis 故障 fail-closed）为真实兜底，诚实降级不声称严格 constant-time；token 仅存 SHA256 digest，明文仅 dev 通道且 staging/production 强制 fail-closed。全部管理操作服务端 Owner-only RBAC + 接入 C14 审计（脱敏）。保留 OIDC/SSO 扩展边界（未锁死为仅 OIDC）+ MFA 挂载点。前端成员管理页 + 账号设置页。Demo Seed 幂等补 pending 邀请 + admin/member 演示账号。双复审：首轮 + 主代理判定修 4 项（M2 Owner 授予不变式、M1 时序枚举、L1 审计缺口、L2 仓储回滚边界）；二轮质量复审 FAIL 于 M1 修复不到位（补偿不对称、无限流、测试假通过），三轮改为「端点限流 + 诚实降级 + 改写真限流测试」+ L2 补真实 PG 无半状态门禁；三轮双复审均 PASS。验证：`cd backend && uv run pytest -q`（合并后全量 1596 passed, 52 skipped）；`uv run ruff check . && uv run mypy`（240 文件）；`cd frontend && pnpm test`（55 文件 273）+ lint/typecheck/build；真实 PG 并发/无半状态门禁通过；members/account Playwright 7/7（邀请闭环/角色/移除/Owner 转移失权/越权 403/改密撤销他端/找回密码闭环/会话撤销即时失效，真实 Redis 限流下通过）。登记 follow-up（非阻断，诚实披露）：找回密码残余时序旁道（已诚实降级、限流兜底，未强求完全消除）；reset 端点 IP 限流强度依赖可信客户端 IP 提取（反代部署需 proxy-headers 配置说明）；dev-token 端点仅 dev、staging/prod fail-closed；MFA/OIDC 全流程为扩展边界待后续条目。

完成定义：

- 支持企业设置、成员邀请、列表、角色变更、移除和 Owner 转移；
- 支持用户资料、修改密码、邮箱验证、找回密码和 Session/设备管理；
- 接入 OIDC/企业 SSO 扩展边界，并为高风险操作预留 MFA；
- 支持自定义角色或权限组时仍保持资源级服务端授权；
- 全部管理操作接入 C14 平台审计，禁止仅依赖前端隐藏；
- 邀请、角色、账号恢复、会话撤销和越权 E2E 通过。

### C16 模型治理、质量评测、成本与配额

**状态：`🚧 进行中`**（阶段一已合入并通过双复审；未满足全部完成定义前不得标 ✅）

**开始日期：2026-07-17**

开工说明：前置 C14 已完成合入。与 C12 并行（互不构成直接依赖，修改边界可隔离）。实现分支 `task/c16-model-governance`（worktree `wt/c16`）；迁移编号原占用 `20260716_0036`（down_revision=`20260716_0034`），按「先合入者保留编号、后合入者重链到当时单头」——**C12 先合入保留 `0035`，本条目的 `0036` 已由主代理在合并时重链至 `0035`，`0037` 接 `0036`，合并后单头 `0037`**。

**已有基线盘点（2026-07-17 主代理核查，提交 `9074a67`，早于本条目开工）**：租户模型网关的第一纵切已在主线——`tenant_model_gateway_policies` 与 `model_gateway_provisioning_commands`（outbox）两表（迁移 `20260714_0017`）、`platform/model_gateway/` 领域实体与服务（revision 乐观并发）、`infrastructure/database/repositories/model_gateway.py` 仓储（策略与 outbox 命令同事务写入）、`GET/PUT /api/v1/model-gateway/policy`（`models.usage.read` 读、`models.manage` 写）、`infrastructure/llm/admin.py` 的 `LiteLLMAdminClient`（租户聚合、受阻虚拟 Key 签发、Key 查询/阻断/解除/删除、租户 spend 分页，含 826 项单元测试）。**关键缺口：该 Admin 客户端目前只被单元测试引用，无任何生产接线**——没有 Controller 消费 outbox、没有真实 LiteLLM 对账、没有租户 Key 下发到 Worker、没有用量/成本记录、没有预算/配额执行、没有评测、没有前端页面、未接 C14 审计。

按可运行纵切分**五个阶段**实施，每阶段完成后做一次全分支差异与失败矩阵审查（阶段检查点不得提前标记条目完成）：

1. **阶段一**：Controller 消费 outbox 对账 LiteLLM，推进 `pending → active/disabled/error`；租户虚拟 Key 签发/轮换/撤销；Worker 从应用级共享 Key 升级为可归因、可撤销的租户凭据 —— 完成定义第 1 条（Controller 侧）与第 2 条；
2. **阶段二（纯观测面，不新增控制面）**：模型/Token/延迟/错误/费用/任务归属记录 —— 完成定义第 3 条；
3. **阶段三（控制面）**：企业预算、用户/员工配额、限流与用量告警 —— 完成定义第 4 条；**并承接完成定义第 1 条的「平台管理 fallback」半句**，以及**配额校验接入 C12 调度入口**；
4. **阶段四**：固定数据集、回归评测、人工反馈与版本对比 —— 完成定义第 5 条；
5. **阶段五**：模型/用量/成本/预算/评测前端页面 —— 第 6 条；模型配置、凭据、预算和配额变更接入 C14 审计 —— 第 7 条；供应商故障、配额耗尽、fallback 与账单归因验收 —— 第 8 条。

**阶段划分修订说明（2026-07-17，主代理）**：原划分把「用量记录」与「预算/配额/限流」并入同一个阶段二。C16 阶段一的实现代理提出应拆开——用量记录是纯观测面，预算/配额/限流是控制面、与 fallback 共用同一套 desired-policy + Controller 机制，混在一个阶段会让观测面阶段被迫新增控制面。**该论证成立，主代理采纳并据此重排**（原阶段三评测顺延为阶段四、原阶段四前端顺延为阶段五；未把评测与前端合并为一个阶段，因为评测自身体量足够独立成阶段）。**但该实现代理是在自己的分支上直接改写主代理拥有的共享台账、未声明**——路线图第 4.1 节规定「数据库迁移编号、共享 API/事件契约…由主代理指定唯一写入方」，台账阶段划分同属主代理决策。此次以主代理在 main 的版本为准；合并 `task/c16-model-governance` 时该文件按本节语义解冲突。

**fallback 承接决策（2026-07-17，主代理拍板）**：完成定义第 1 条的「平台管理…fallback」在阶段一无对象（实测 `infra/litellm/config.yaml` 无 `fallbacks`/`num_retries`，只有 `config.stub.yaml` 有；平台侧无 fallback 管理对象），此前**四个阶段无一承接、构成台账断链**（与 C17 收口时的教训同型）。经实现代理建议 + 主代理采纳，**指派给阶段三**：理由是 fallback 与限流/配额同属「调用时刻 guardrail」，共用同一套 desired-policy + Controller 机制；且完成定义第 8 条把「供应商故障 / 配额耗尽 / fallback」并列，同阶段实施才能验证三者交互。
**阶段三补充要求（来自 C16 阶段一实现代理的调研，主代理采纳）**：阶段三实施 fallback 时需一并决定——LiteLLM router 配置（`config.yaml` 的 `fallbacks`/`num_retries`/`timeout`）应由平台以**受版本管理的配置产物**统一维护，而不是手工编辑。另注：完成定义第 1 条在阶段一只落了「模型别名 / 可用模型」（provider-neutral alias 与租户 `allowed_aliases`），「供应商 / fallback / 超时 / 重试」的平台管理与第 8 条的「供应商故障 / fallback」测试均归阶段三。

**复审修复记录（2026-07-17，双复审 FAIL 后集中修复，本任务提交）**：

- **根因解耦（S1+S2 同源）**：`policy.status` 此前同时承担「对账进度」与「凭据可用性」。新增 `tenant_model_gateway_keys.provisioned_key_version`（迁移 `20260716_0037`）作为「网关侧真实存在且未被阻断的 Key 版本」的唯一真相源，只由 Controller 在真实网关确认后写入；`policy.status` 退回纯进度账本，仅用于在凭据不可用时区分瞬态/永久。Worker 改用 observed 版本派生凭据；
- **S1**（Demo Seed 写 `status=active` 却零 reconcile 命令 → Demo 员工必然 401）：Seed 改为只写 desired（`status=pending`）并与策略同事务入队真实 pending RECONCILE 命令，Key 行与 `provisioned_key_version` 全部交由真实 Controller 对账产生。解耦后该缺陷**结构性不可复发**：Seed 无法伪造 observed 版本。三处宣称相反事实的注释/文档（路线图、`demo_seed.py`、`test_demo_seed.py`）已订正；测试补齐「Seed 必须入队命令」「Seed 绝不标记 provisioned」两条断言；
- **S2**（`pending` 被判 Permanent → 每次策略变更打死窗口内并发 Run）：新增 `ModelGatewayProvisioningInProgress(TransientRuntimePreparationError)`，对账进行中交队列重投；已有 observed 版本时 `pending` **根本不再失败**（改 rpm_limit 的失败窗口从 2 秒降为零）。测试改为断言**归类**（瞬态/永久）而非仅异常类型——这正是原测试抓不到该缺陷的原因。同时把具体原因码（已撤销 / alias 越权 / 对账确定失败）保留到 Run 的 `error_code`，此前全部塌缩为笼统的 `model_gateway_unavailable`；
- **M1**（DB 抖动裸逃逸）：`ModelGatewayPolicyPersistenceError` → 瞬态重投，`CorruptModelGatewayPolicy`（其子类，必须先捕获）→ 永久；补 DB 故障注入用例；
- **M2**（轮换永久失败锁死租户）：解耦后租户继续使用 observed 的旧版本，**不再是服务影响**；409 文案补上可操作的逃生舱（重新 PUT 策略入队新对账），docstring 如实声明「先落库再触达网关」的代价而不只讲好处；
- **M3**（派生密钥未做最小权限）：从共享 `x-backend-environment` 锚点移出，只注入 `worker` 与 `model-gateway-controller`；新增 `infra/compose/test_platform_secrets.py` 对 `docker compose config` 的**最终渲染结果**门禁（反向验证：放回共享锚点立即 2 项 RED）；
- **L1**：`test_skip_locked_lets_other_tenants_proceed_in_parallel` 原本只断言两副本都成功、拿到不同租户，不含时序断言，因此**剥离 `skip_locked` 仍全绿**；已改为断言两次持锁对账的时间区间真实重叠；
- **L2**：删除死代码 `resolve_model_gateway_key_secret` 与 `_DEV_ENVIRONMENTS`（零生产调用点，且与 `config.py` 的 fail-closed 策略不一致——测试对着没人调用的安全控制断言属虚假信心）；派生密钥的 fail-closed 唯一真相源为 `config.AppSettings`；
- **L3**：删除 `model_gateway_provisioning_commands.status` CHECK 中已废弃的 `'processing'`（枚举本就没有），迁移用 `batch_alter_table(copy_from=..., recreate="always")` 整表重建以摘掉匿名 CHECK，并给新约束命名；
- **L4**：订正 `credentials.py` docstring 中「数据库保存 SHA256 摘要」的早期方案残留——实际连摘要都不存；
- **覆盖缺口**：新增 `infra/litellm/test.sh tenant-key-inference` 门禁——对真实 LiteLLM 断言「派生租户 Key → `/chat/completions` → 200」，并断言未对账租户的派生 Key 被真实拒绝（**S1 当时命中的正是后者**）。此前的门禁只断言 admin 侧有记录，这正是 S1 能带着全绿测试出厂的原因；
- **`platform/models.py` 锚点**：遗留项③ 的提示写到真正的触发点 `DEFAULT_MODEL_ALIASES` 定义处，不再只存在于路线图；
- **`infra/platform/mvp-profile.sh`**：补齐 Controller 所需的 `LITELLM_MASTER_KEY` 与 `MODEL_GATEWAY_KEY_SECRET`（缺失时 app compose 直接渲染失败 → 对账永不发生），并把 `model-gateway-controller` 同时纳入**启动清单与健康门禁**（`start_profile` 的 `up` 是显式服务枚举，仅在 compose 里声明 `profiles: ["worker"]` 并不会被拉起——隔离验收栈实跑时健康门禁报出 `app service is missing: model-gateway-controller`，坐实了该缺口：没有它对账永不发生，而后果只会在 Run 真正跑起来后才以 401/瞬态重投暴露）；两份清单都有契约测试钉住。

- **`infra/litellm/worker_gateway_probe.py`**：阶段一把 `LiteLLMChatModelFactory` 的 `api_key` 从构造参数改成按调用传入（fail-closed 的结构性保证），但漏改了这个生产探针的调用点——单测覆盖不到它，隔离验收栈实跑时以 `TypeError: LiteLLMChatModelFactory.__init__() got an unexpected keyword argument 'api_key'` 暴露。已修复并重跑 `worker-chat` / `worker-readiness` / `stub-matrix` 三条既有门禁全绿。

- **S1 解除条件的真实证据（隔离验收栈实测，非推演）**：完整真实栈（PostgreSQL + Redis + MinIO + 真实 LiteLLM v1.86.2 + API + Dispatcher + Worker + **model-gateway-controller** + Sandbox + 前端）启动并全部健康后，从宿主机重放 Demo Seed，随后直接查真实数据库：策略 `status=active`（**Seed 写的是 pending，只可能由真实 Controller 推进**）、Key 行 `key_version=1 / provisioned_key_version=1`（**Seed 完全不写 Key 行，只可能由 Controller 在真实网关确认后创建并标记**）、对账命令 `completed / attempts=1 / 无错误`（Seed 入队的命令被真实消费）。同栈的 `Production worker ChatOpenAI -> LiteLLM -> stub passed` 证明 Worker 侧推理链路通；**Playwright 用户流程 `✓ 用户通过 MVP 完整栈完成数字员工任务并在刷新后看到持久化终态` 通过**，即以真实用户方式（浏览器点击 → 提交 → 任务终态持久化）跑通了 Demo 员工任务。这条链路在修复前**不可能成立**——Seed 从不入队命令，Controller 也不在启动清单里，其派生 Key 在 LiteLLM 侧根本不存在。

**本轮发现的既有问题（不在本任务范围，未修，交主代理判断）**：`infra/litellm/test_config.py::test_local_stub_override_is_test_only_and_not_published` 在 `f5fb483`（C16 开工前）即已失败——它断言 stub override 的 `image` 等于钉住的上游镜像，而 `compose.stub.yml` 实际构建本地 `agent-platform-litellm-stub:local`。已用 `git checkout f5fb483 -- infra/litellm/` 实测确认与本任务无关；本任务对 `infra/litellm/` 只改了 `test.sh` 与 `tenant_key_probe.py`。按「单个任务提交只能包含该任务及其必要基础改动」未夹带修复。

实现记录（阶段一，2026-07-17，本任务提交，分支 `task/c16-model-governance`）：

- **复用既有基线**：`LiteLLMAdminClient`（9074a67，843 行、826 项单测）此前只被自己的单测引用、无任何生产接线；本轮把它接入生产而非另写客户端，未修改其任何行为。新增 `infrastructure/llm/provisioner.py` 作为唯一适配层，把上游错误一次性映射为平台端口语义；
- **独立 Controller**：新增 `workers/model_gateway_controller.py` 进程（与 `sandbox_janitor` 同构，独立 compose 服务 `model-gateway-controller`），消费既有 outbox 对账真实 LiteLLM 公开管理 API 并推进策略状态；API 进程内不做任何对账。它是唯一持有 LiteLLM master key 的平台进程；
- **多副本安全**：认领用 `FOR UPDATE SKIP LOCKED` + 按租户串行（NOT EXISTS 排除本租户更早的 pending 命令），认领事务横跨网关调用，崩溃即释放锁、命令仍 pending 自然重入，无需 processing 租约。**真实 PG 并发门禁**：5 项用例（并发不重复执行/跨租户并行/同租户严格有序/CAS 丢弃被取代的 revision/崩溃释放重入）通过。反向验证（实测，两种退化分别验证）：**移除整个行锁** → 3 项 RED；**仅剥离 `skip_locked` 保留 `FOR UPDATE`**（退化为互相阻塞但仍都成功）→ 并行用例 RED。后者最初并测不出来——原用例只断言两个副本都返回 True、拿到不同租户，不含任何时序断言，因此不 pin 它名字宣称的「并行」行为；现已改为断言两次持锁对账的时间区间真实重叠。
- **凭据「派生而非存储」**：Key 明文与其 SHA256 摘要都由服务端密钥 + `tenant_id` + `key_version` 现场派生，`tenant_model_gateway_keys` 只存两个整数（`key_version`/`retired_key_version`），数据库中不存在任何由凭据派生的材料，API 也因此无需持有派生密钥。派生密钥在 staging/production fail-closed（≥32 字符、拒绝开发弱密钥），与 C14 `audit_hmac_key` 同模式；
- **错误分类与不确定语义**：瞬态（传输/超时/5xx/429）按 `next_attempt_at` 指数退避（2s→300s 上限）、次数有界（8 次）后受控转 `error`；永久（4xx/校验/配置）立即 `error`；`LiteLLMAdminOutcomeUnknown` 一律停在 `error` 且绝不自动重放（同 6.2 节 `tool_execution_uncertain` 哲学）。已结算命令按保留期（默认 7 天）有界清扫；
- **Worker 升级为租户 Key**：`PlatformModelResolver.resolve` 改为 async + 按 `tenant_id` 解析；`LiteLLMChatModelFactory` 不再持有共享 Key，凭据按租户传入。无策略/已撤销/未对账/Key 未签发/alias 越权一律 fail-closed，绝不回退共享 Key；解析只读平台数据库不调管理接口，LiteLLM 管理面故障不波及存量 active 租户。**生产装配由 `tests/unit/workers/test_main.py::test_production_worker_assembly_wires_tenant_attributable_gateway_credentials` 直接背书**（对齐 C07/C13 的生产装配缺口教训）；
- **审计**：`PUT /policy` 与新增 `POST /api/v1/model-gateway/key/rotate`（仅 Owner `models.manage`）全部经 `emit_audit_event` 接入 C14；metadata 只含 provider-neutral 策略字段与版本号，契约测试断言 Key 明文不在响应与审计中；
- **Demo Seed**：幂等补齐演示租户的 desired 策略并与之同事务入队真实 reconcile 命令，由真实 Controller 对账后 Demo 员工方可运行（`_upsert_record` 顺带从逐表特判主键改为统一从 mapper 取，移除既有 `RunEventRecord` 特判）。**注**：本条初版曾写「补齐 active 策略 + Key v1」——那正是下方「复审修复记录」S1 修掉的缺陷（Seed 伪造终态、从不入队命令，导致 Key 在 LiteLLM 侧根本不存在、每个 Demo Run 必然 401）。现行代码为 Seed 只写 `pending` 且**不写 Key 行**，以此处描述为准；
- **真实 LiteLLM 对账门禁**：新增 `infra/litellm/test.sh tenant-key-reconcile` + `tenant_key_probe.py`，对**真实 LiteLLM v1.86.2 容器**（随机项目名/端口、验后自动销毁）验证租户聚合、Key 签发可归因、幂等重放收敛、轮换后旧版本在网关侧被删除、撤销后立即阻断——首跑即通过，Stub 与真实实现无契约背离；
- 验证命令（复审修复后重跑；**采集条件影响计数，复跑请对齐**——`TEST_DATABASE_URL` 指向真实 PostgreSQL 时真实依赖用例才会执行，否则条件跳过）：
  - `uv run pytest tests/unit tests/contract -q` → 1480 passed（不需 PG）；
  - `TEST_DATABASE_URL=<真实 PG> uv run pytest tests/integration/model_gateway -q` → **25 passed**；不设该变量则全部跳过；
  - `TEST_DATABASE_URL=<真实 PG> uv run pytest tests/integration/database tests/integration/bootstrap -q` → **46 passed / 0 skipped**（含 autogenerate 漂移守卫）；不设该变量为 45 passed / 1 skipped，差的 1 项即需真实 PG 的那条；
  - `uv run ruff check .`、`uv run mypy`（0 错误）；
  - `python3 -m unittest infra.platform.test_contract`（45 OK）、`python3 infra/compose/test_platform_secrets.py`（3 OK）；
  - `bash infra/litellm/test.sh tenant-key-reconcile / tenant-key-inference / worker-chat / worker-readiness / stub-matrix`（全通过，各自起随机名/端口的真实 LiteLLM v1.86.2 栈并验后销毁）；
- 阶段一初版验证命令：`uv run pytest tests/unit tests/contract -q`（1474 passed）、`tests/integration/database tests/integration/model_gateway tests/integration/bootstrap`（含 autogenerate 漂移守卫）、`uv run ruff check .`、`uv run mypy`（0 错误）、真实 PG 并发门禁 5 passed、真实 LiteLLM 对账门禁 passed；
- **本阶段明确不含**（留给阶段二/三/四/五，不得据此宣称 C16 完成）：用量/Token/延迟/费用记录（阶段二）、预算与配额执行、限流与用量告警、fallback（阶段三）、评测（阶段四）、前端页面与 C14 审计收口、第 8 条验收（阶段五）；
- **已知缺口 / 遗留项**：① 派生密钥本身仍是环境变量注入的单一密钥，泄漏等于可签发任意租户 Key，纳入 KMS/Vault 托管与轮换属 C18（已按最小权限只发给 worker 与 controller，由 `infra/compose/test_platform_secrets.py` 对 `docker compose config` 的最终结果门禁）；② 轮换/撤销立即生效，不为在途 Run 保留旧凭据（其后续模型调用会失败而非静默降级），这是有意的安全取舍；③ `DEFAULT_MODEL_ALIASES` 当前只有 `general-purpose`，存量 Key 的 models 范围漂移不可达，故未实现 `update_key`——**新增 alias 时必须同步实现存量 Key 的范围对齐**，否则存量租户会命中 `provisioning_key_scope_conflict` 永久错误并全量 fail-closed；该提示已作为锚点注释写在真正的触发点 `platform/models.py` 的 `DEFAULT_MODEL_ALIASES` 定义处，不依赖本文档被读到；④ 轮换若永久失败，`retired_key_version` 不会清空，再次轮换持续 409 直到重新 PUT 策略产生新对账命令；服务不受影响（凭据解析用 observed 版本，租户继续使用网关侧真实可用的旧版本），409 文案已给出可操作的逃生舱。

完成定义：

- 平台管理供应商、模型别名、可用模型、fallback、超时和重试；
- LiteLLM Key 从应用级共享升级为可归因、可撤销的企业/工作负载凭据；
- 记录模型、Token、延迟、错误、费用和任务归属；
- 支持企业预算、用户/员工配额、限流和用量告警；
- 建立固定数据集、回归评测、人工反馈和版本对比；
- 客户端提供模型、用量、成本、预算和评测页面；
- 模型配置、凭据、预算和配额变更接入 C14 平台审计；
- 供应商故障、配额耗尽、fallback 和账单归因测试通过。

**（2026-07-17 由 C12 双复审转写而来的强制门禁，不得删除）**：C12 的调度入口已按完成定义第 4 条留好配额扩展点 `platform/scheduling/guards.py::evaluate_dispatch_guards`（返回新 `SkipReason` 即复用既有跳过/暂停/审计/历史链路，无需改调度器）。C16 **阶段三**（控制面；2026-07-17 阶段重排后，配额由原阶段二移至阶段三，见上方「阶段划分修订说明」）接入配额时必须满足：

1. **配额不足必须是「临时跳过」，不得是「永久自动暂停」**。C12 现有的 `_GUARD_PAUSE_REASONS` 映射会把守卫失败的任务自动暂停——那对「创建者权限被撤销」「员工已下线」这类**配置性**失效是正确的，但**照抄到配额上会把配额超限的定时任务永久停掉**，下个计费周期配额恢复后也不会自愈，属于把瞬态状态当永久缺陷处理。新增的配额 `SkipReason` 必须**不进入** `_GUARD_PAUSE_REASONS`。
2. **Cron 频率下限治理归属 C16 阶段三**（主代理 2026-07-17 拍板）。C12 不设频率硬下限，理由：C12 层没有任何计量能力（无 token/费用模型、无租户预算、无用量视图），在无计量的层设拍脑袋阈值是无数据的产品决策；且「每分钟轮询外部系统」是真实合法用例，硬下限会误伤。C12 已补极端频率用例（`* * * * *` 在默认 `ConcurrencyPolicy.SKIP` 下不堆积；`ALLOW` 下每触发一个 Run）。**如实声明的风险窗口**：C16 配额落地前，任何持有 `runs.execute` 的成员都可以建「每分钟 + ALLOW」的定时任务持续烧模型额度，无任何平台侧节流。当前本机 Demo 阶段接受该窗口；C16 阶段三必须闭合它。

### C17 Capability Registry、Entitlement 与交付 Profile

**状态：`✅ 已完成`**

**开始日期：2026-07-16**

**完成日期：2026-07-17**

2026-07-17 收口记录（本任务提交，无新增代码，仅台账与代码事实对齐）：B04 于 2026-07-17 合入 `✅ 已完成`后，本条目原三条待集成项经独立规格复审 + 主代理证据复核后定性如下，八条完成定义已满足或属「无对象」，收口为 `✅ 已完成`。

- **待集成项① video-studio 宿主接线与 Core+视频组合矩阵 —— 已闭合，且确认无夹具旁路**：`bootstrap/capabilities.py` 的 `_BACKEND_ROUTER_FACTORIES` 含 video-studio，`resolve_installed_backend_registrations` 对未知能力/无宿主集成能力装配期 fail-closed；契约夹具 `tests/contract/capabilities/capability_harness.py` 与 Playwright 夹具 `tests/fixtures/video_studio_e2e.py` **均走生产 `create_app`**，全仓 `extra_routers` 临时注入口已彻底删除（主代理 grep 复核零命中），E2E 夹具只覆盖 `app.state` 上的云 Provider（替换外部 COS，非替换门禁）。Core+视频组合矩阵已存在：装配层 `tests/unit/bootstrap/test_capabilities_bootstrap.py` 覆盖 core-only/social/video/both 四组合，HTTP 层 `tests/contract/capabilities/conftest.py` 三个 harness，越权契约 `test_video_studio_route_gating.py` 10 用例（401/未授权 403/无权限 403/撤销后即 403/未安装 404 + 授予 409/Core-only 404）。
- **待集成项② Worker 侧能力任务处理器 —— 判定为「无对象（vacuous）」，非 fail-open**：主代理独立复核 `grep -rn "worker_handlers" src/ tests/` 全部命中仅为 manifest 字段定义、命名空间校验、两个 manifest 声明（`social.jobs.v1` / `video.jobs.v1`）及其自身单测，**无任何 dispatcher 消费该字段**；`src/agent_platform/workers/` 下 `capabilit|entitlement` 命中全部为数字员工的 `PublishedRuntimeCapabilities` / `memory_capability_enabled`（与 Capability Package 同名不同物）。因此未授权租户无法调度能力 Worker，不是因为有门禁拦截，而是**该执行面在 Core 中根本不存在**，不存在可越权路径、无开口可 fail-open。`evaluate_capability_availability` 已作为 API/Worker 共用判定源导出于 `platform/entitlements/services.py`（位于 `platform/` 而非 `api/`，依赖方向正确、Worker 可导入），但 Worker 侧零调用、零测试——**该门禁要求已点名转写为 B08 的完成门禁**（见 `industry-capability-roadmap.md` B08 条目），不因本条目收口而消失。
  - **B04 引入的能力后台 Worker 不构成本条违规**：`bootstrap/capabilities.py` 的 `_BACKEND_WORKER_FACTORIES` 注册 `video-media-library-maintenance` 常驻清扫循环（由 API lifespan 启动），**仅经部署层门禁**（能力未安装即不注册、不启动，`test_capabilities_bootstrap.py` 背书）。这是有意取舍且正确：该循环回收的是跨租户存量对象、不由租户触发，撤销授权后仍必须继续清理已产生的对象，否则形成无界存储成本；对其套用 `tenant_entitled` 反而错误。
  - **主代理裁定不在本条目补「能力 worker 注册必须过 gate」的结构性守卫**：当前不存在任何 job handler 注册机制，补该守卫需先建出零消费方的 dispatcher 协议，属 CLAUDE.md 明令禁止的「为假设性未来需求过早抽象」。该守卫应在 B08 实现 `social.jobs.v1` 时与其一并建立，已写入 B08 完成门禁。
- **待集成项③ Sidecar 下载与云凭据签发 —— 云凭据已闭合；Sidecar 属「server 侧无对象」，不阻断本条目**：云凭据签发（B04 真实腾讯 CAM/STS）与其余 video 端点同在 router 级 gate 之下，无逐端点逃逸口（`capabilities/video_studio/api.py` 的 `POST /materials/upload-credentials`，未授权连 GET 都 403）；凭据不全时端点 503 fail-closed。Sidecar 侧经主代理复核确认：**平台后端不存在 Sidecar 分发端点**——`SocialOperationsPage.tsx` 的下载地址是用户手填输入框（`<Input aria-label="安全下载地址">`），manifest/signature 亦为粘贴录入，客户端直连任意 URL，安全控制是 Ed25519 验签（`src-tauri/src/sidecar_package.rs`），Rust 侧 `entitlement|capabilities/registry` 零命中。当前该入口仅受前端 registry 门禁保护，按 CLAUDE.md「前端隐藏菜单不能替代后端授权」这不算后端授权，但**没有后端资源可授权**。真实分发链建立时（B02 集成 / C20 发布）该端点必须挂同一 gate，**已点名转写为 B02 与 C20 的完成门禁**。
- **为何不继续挂 `🧪 待集成`（依赖方向论证）**：B02 与 B08 条目均明确把「Core C17 生产宿主 / Entitlement」列为**自身**解除条件。若 C17 反过来等 B02 的 Sidecar 分发链、等 B08 的 `social.jobs.v1`，即构成循环依赖死锁，并无限期堵死 C18（前置 C14+C17）与 C19（前置 C17）。第 5 节规定的依赖方向是单向的——「业务包依赖的 …Capability/Entitlement… 由 C17 提供；对应 Core 条目未完成时业务条目只能标记待集成」，**无反向条款**。C17 的职责是提供机制并在 Core 自有执行面上强制它，不是等业务包把执行面建出来。
- **完成定义第 6 条逐子句判定**：调用 API `✅ 满足`；签发云凭据 `✅ 满足`；调度 Worker `⊘ 无对象`（Core 无能力 job handler，门禁前移至 B08）；下载 Sidecar `⊘ 无对象`（平台无分发端点，门禁前移至 B02/C20）。
- **登记的已知局限（如实声明，非阻断）**：① I2 审计桥接不一致语义——能力服务业务副作用已持久化时审计 flush 失败返回显式 500，客户端重试同一 device_id 得 409，运维依据 500 与 `capability_audit_flush_failed` 日志人工补偿；**与 B04 同类问题（M-1 审计 flush 失败重试重复签发 STS）被列为必须闭合门禁存在标准不对称**，主代理裁定不阻断——B04 的重复副作用带真实外部云调用与成本，本条目仅为幂等性缺失、有显式失败信号、非 fail-open；设备注册幂等化保持 follow-up。② L1 registry 对未授权租户返回裸条目（capability_id + 安装布尔）暴露部署拓扑——前端需据此区分「未授权」与「未安装」语义，MVP 接受。③ L2 单条目畸形导致整表解析失败——fail-closed 优先于可用性，方向正确。④ L4 登录 / `/auth/me` 的能力权限附加为每工作区×每能力逐条查询（N+1，`dependencies/capabilities.py` 双重循环逐条 `repository.get`），当前 2 个能力包可接受；**这是随能力包数量线性劣化的登录热路径，触发阈值定为「安装能力包 ≥4 或 P95 登录延迟劣化」，达阈即改批量查询**，不再以无期限 follow-up 挂账。
- **验证命令（主代理在 main @ `f5fb483` 实跑复核）**：`cd backend && uv run pytest tests/unit/capabilities tests/unit/platform/entitlements tests/contract/capabilities -q`（423 passed）；`uv run pytest tests/contract/video_studio tests/integration/database/test_migrations.py -q`（29 passed, 1 skipped——真实 PG 条件门禁）；`cd frontend && pnpm exec vitest run src/app --reporter=dot`（6 文件 46 项通过）。迁移 `20260716_0026` 在当前 main 单链上（`down_revision=20260716_0025`，链至单头 `20260716_0034`）；`capabilities` 路由根在能力包路由装配之前注册，属 Core 保留根。组合矩阵 Playwright 未为本次收口重跑——Core+both 已由 `playwright.video-studio.config.ts` 的安装清单 + video E2E 3 项 + capability/social 5 项在 B04 第三轮双复审中实跑覆盖，主代理判定不必为状态对齐重复付出隔离栈成本。

开工说明：前置 C14 已合入主线；经用户批准与 C14 收尾（HMAC 加固）并行。实现分支 `task/c17-entitlements`。迁移编号协调：C14 HMAC 加固占用 `20260716_0025`，本条目使用 `20260716_0026`（暂 down_revision=0024，后合入者负责重链）。video-studio 相关三层校验接线在 B04 分支合入后补齐，本条目先覆盖 Core + social-operations 与 Core-only 组合矩阵。

实现记录（2026-07-16，本任务提交，分支 `task/c17-entitlements`）：

- `platform/entitlements/` 建立租户×能力授权领域（active/revoked + 到期读取时判定 + `evaluate_capability_availability` 单一可用性判定源），`infrastructure/database/repositories/entitlements.py` 提供 revision CAS + 唯一约束的幂等 grant/revoke 仓储，迁移 `20260716_0026_create_capability_entitlements`；
- `capabilities/registry.py` 落地生产 `CapabilityHost`（原 MockCapabilityHost 校验逻辑上移共享，Mock 改为薄子类），`bootstrap/capabilities.py` 组合根按 `AGENT_PLATFORM_INSTALLED_CAPABILITIES` 显式安装清单装配（未知能力/无后端宿主集成的能力启动即失败，fail-closed）；
- 新增 Core 路由根 `capabilities`：`GET /api/v1/capabilities/registry` 按「部署安装 ∩ 租户 Entitlement ∩ 用户 RBAC」三层裁剪返回；未授权条目不携带 frontend_entries/permissions。管理入口为 Owner（`workspace.manage`）范围内的 `PUT/DELETE /api/v1/capabilities/entitlements/{capability_id}` + `GET` 列表，全部经 `emit_audit_event` 留审计（设计取舍：MVP 无平台运营方角色，授权动作由企业 Owner 自管，生产运营方角色留待 C15/C18 收紧）；
- social-operations（B02/B08）路由首次挂入生产 App，统一经 `create_capability_gate` 每请求实时三层校验（未授权 403 fail-closed、Core-only Profile 下 404、Entitlement 查询失败 5xx 拒绝）；能力内审计事件经请求内缓冲、响应发出前桥接落库到 C14 统一审计（跨租户事件直接拒绝）；撤销后新调用（含任务入队/认领）立即 403，存量本地任务由设备端租约超时/紧急停止兜底；
- 登录/`/auth/me` 的 workspace permissions 按 Entitlement + 角色附加能力权限码（OWNER/ADMIN 获全部 manifest 权限，MEMBER 不授予）；前端 `capability-registry` 数据源切换为真实 API，Zod schema 收紧为「已授权条目才允许携带声明」的判别联合，保持失败关闭；
- Demo Seed 以稳定 ID 幂等授予演示租户 social-operations（source=demo-seed）；
- 组合矩阵：Core-only 与 Core+social 由契约测试覆盖（Core-only 下登录/员工/知识/Skill/Tool 全部可用、social 路由 404、registry 为空）；Core+视频与目标客户组合待 B04 合入后补；
- 验证命令：`uv run pytest tests/unit tests/contract tests/integration/database tests/integration/bootstrap -q`（971 passed）、`uv run ruff check .`、`uv run mypy`（0 错误）、`pnpm test`（43 文件 199 用例）、`pnpm lint && pnpm typecheck && pnpm build`、隔离栈 Playwright `capability-entitlements.spec.ts + social-operations.spec.ts`（5 passed，随机项目名/端口，验后自动销毁）；
- 待集成项：① B04 合入后补 video-studio 后端宿主接线与 Core+视频组合矩阵；② Worker 侧尚无能力任务处理器（social.jobs.v1 未在主线实现），`evaluate_capability_availability` 已作为 API/Worker 共用判定源导出，B04/B08 Worker 接入时必须复用；③ Sidecar 下载与云凭据签发的未授权拦截随对应能力落地时接入同一 gate。

复审修复记录（2026-07-16，双复审后集中修复，本任务提交）：

- C1 迁移多头：已在分支内合并 origin/main（含 C14 HMAC 加固 `20260716_0025`），`20260716_0026` 的 down_revision 重链至 `20260716_0025`，迁移测试 head 断言同步更新；
- I2 审计桥接语义：实测本版 FastAPI 的 yield 依赖 teardown 在响应发送之后执行，原 teardown 抛 500 会被吞（客户端 201、审计静默丢失）；已改为 endpoint 包装层在响应构造前 flush（`wrap_capability_router`），业务成功但审计写入失败时客户端收到显式 500（`capability_audit_flush_failed`）且审计不落半写。**已知不一致语义**：能力服务的业务副作用（如设备注册的内存/SQLite 状态）此时已持久化，客户端 500 后重试同一 device_id 会得到 409；运维补偿 = 依据 500 响应与 `capability_audit_flush_failed` 日志人工核对，设备注册幂等化（同租户同 device_id 重放返回既有记录）列为 follow-up；
- I1/L3 硬化：Entitlement 授予新增部署安装校验，对未安装能力（含 Core-only Profile 下所有能力）授予返回 409 `capability_not_installed`，fail-closed；
- 授权治理定位（主代理拍板）：**当前 Entitlement 由租户 Owner（`workspace.manage`）自助管理，不构成商业购买闸门；平台运营方/计费侧闸门待 C15/C18 收紧**；
- 完成定义第 7 条中「交付 Profile 变更」的审计：部署安装清单（`AGENT_PLATFORM_INSTALLED_CAPABILITIES`）属部署层环境配置，其变更发生在进程外，无租户内审计主体，归 C18/运维域（部署配置变更留痕）处理；租户可见的授权/撤销已全部接入 C14 审计；
- 记录项：L1 registry 对未授权租户返回的裸条目（capability_id + 安装布尔）暴露部署拓扑——MVP 接受，前端需要该信息区分「未授权」与「未安装」语义；L2 前端 registry 响应任一条目畸形即整表解析失败（fail-closed 优先，接受单能力故障放大为全部能力不可用）；L4 登录/`/auth/me` 的能力权限附加为每工作区×每能力逐条查询（N+1），当前安装能力数 ≤2 可接受，能力包增多时改批量查询（follow-up）。

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
- 审计哈希链 HMAC 密钥纳入密钥管理并支持轮换（C14 跟进项：`hash_algorithm`/`head_seal_algorithm` 已带版本标识，轮换需支持多密钥版本共存校验且不破坏存量链校验）；
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

**（2026-07-17 由 C12 双复审转写而来的强制门禁，不得删除）**：**本条目（或任何其他条目）一旦引入 Run 的删除或保留清理，必须同时处理定时任务执行记录的悬挂问题**。C12 的 `scheduled_task_executions.run_id` 对 `runs.id` 的外键是 `ondelete="SET NULL"`（迁移 `20260716_0035`）。Run 一旦被删，其执行记录的 `run_id` 会变成 NULL，而 `_settle_one`（`repositories/scheduling_dispatch.py`）对 `run_id is None` 直接 `return False`、C12 的执行超时也只覆盖 `waiting_for_input`，**两者都兜不住**：该执行会永久停在 `dispatched`，`list_active_for_task` 恒返回它，`ConcurrencyPolicy.SKIP`/`QUEUE` 下对应的定时任务**永久静默停摆**，且 `purge_terminal_before` 只清终态、永不回收它。这正是 C12 的 G1 缺口，届时会从 SET NULL 这条缝里原样复发。C12 当前不可达（全仓无删除 Run 的代码路径），故未预建处理分支——**引入删除的条目必须自行选择并验证方案**（可选：改 `ondelete=CASCADE` 一并删执行记录、删 Run 时就地结算执行、或把 `run_id is None` 纳入超时），并补真实门禁用例。

完成定义：

- 生成 macOS 和 Windows Tauri 安装包，完成签名、公证和来源校验；
- 建立受签名自动更新、灰度、回滚和版本兼容策略；
- 建立 API、Worker 和基础设施正式部署清单与容量基线；
- PostgreSQL、MinIO、RAGFlow、LiteLLM 配置具备备份和恢复演练；
- 建立高可用、健康检查、故障切换、灾备目标和操作手册；
- Core-only 全量自动化、安装升级、回滚和恢复演练全部通过；
- **（2026-07-17 由 C17 收口转写而来的强制门禁）平台侧一旦提供 Sidecar / 安装包 / 更新产物的分发端点，该端点必须挂 Core 统一的 `create_capability_gate` 三层校验，未安装/未授权租户不得下载可选能力的 Sidecar。** 门禁来源：C17 完成定义第 6 条「未安装/未授权能力无法…下载 Sidecar」在 C17 收口时属「无对象」（平台无分发端点，下载地址为用户手填、客户端直连），门禁前移由本条目与 B02 共同承接，C17 已不再承接，删除即造成该子句永久失守。

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

基线日期：2026-07-17（C13 合入后）。

| 验证项 | 当前结果 |
| --- | --- |
| 后端 Pytest | C15 合入后默认环境 1596 通过、42 跳过、0 失败；条件跳过均明确标注缺少真实 PostgreSQL、Redis、MinIO、破坏性本地 Docker 沙箱、真实腾讯云 COS 或真实 RAGFlow 凭据 |
| 后端 Unit + Contract | 863 项通过；新增覆盖动态输入输出契约、前端不可表达 JSON Schema 关键字拒绝、动态 properties 必须关闭 additionalProperties、历史已发布动态 Schema 运行入口 fail-closed、历史已发布文件字段 Schema 未启用 `file_upload` 时即使文件字段可选且本次未提交文件也 fail-closed、legacy 自由输入与零字段动态空输入兼容、浏览器 RegExp 不兼容 pattern 拒绝、文件控件约束收窄、数组文件语义拒绝、动态文件字段与本次附件绑定、幂等重放固定原员工版本 Schema、前置请求体限流与重复长度头、9 MiB/25 MiB 上传到物化、未绑定文件补偿/TTL 节流、Run 幂等与任务意图换键、SDK 硬超时/有界 tombstone 退休、Worker 首次物化异常/取消回收和 CORS 幂等头，并保留既有 Saga phase/lease/CAS/heartbeat、取消与提交失败回归 |
| C04 真实依赖专项 | `bash infra/platform/test-c04-artifacts.sh` 先执行 46 项 C04 单元/契约/迁移门禁并按条件跳过 1 项无显式凭据的真实 COS 测试，再通过 1 项真实 Docker Sandbox 25 MiB 边界测试，然后以随机端口启动 PostgreSQL、Redis、MinIO、LiteLLM Stub、API、Dispatcher、Worker、Sandbox Controller/Janitor 和 Web。正式无头 Playwright 3 项通过；附件场景在上传请求被延迟时同步双击并断言仅 1 次上传、1 个 Run，随后真实 Agent 在实际 Sandbox 读取附件、发布产物并完成预览、下载、刷新、定位和删除。真实 PostgreSQL Saga 并发 2 项通过；随机 profile 容器、网络、Volume 均为 0，未触碰运行中的 `agent-platform-dev` 12 个服务 |
| Ruff | 通过 |
| Mypy | 200 个源码文件通过（C09 合入后） |
| 前端 Vitest | C15 合入后 55 个测试文件、273 项测试通过 |
| 前端 Lint | 通过 |
| 前端 Typecheck | 通过 |
| 前端 Build | 通过 |
| Playwright Web 业务回归 | C10 合入后 26 项完整回归通过（单 worker，含审计观测、能力授权、知识生命周期、MCP 工具生命周期与记忆中心用例）；runtime 专用配置 9 项通过（真实 Worker/Sandbox/MCP stub/RAGFlow stub，含知识引用闭环、自动续跑、会话取消、MCP 调用/禁用拒绝与记忆多轮召回/纠正/删除/禁用）；多 worker 缩容退出挂起已定位并暂以单 worker 规避，根因待查 |
| C06 动态输入输出专项 | RED 阶段后端新增契约和 Worker 用例先覆盖默认空对象 Schema 误判、结构化输出违规未受控失败和真实运行时 JSON 字符串未解析；前端新增用例先覆盖员工详情缺 Schema 表单、任务详情缺结构化结果展示。复审补充覆盖文件型字段未启用 `file_upload`、历史已发布文件字段 Schema 未启用 `file_upload` 且本次未提交文件时运行入口 fail-open、动态 properties 必须关闭 `additionalProperties`、历史已发布动态 Schema 在运行入口 fail-closed、legacy 自由输入兼容、浏览器 RegExp 不兼容 pattern 后端拒绝且前端受控失败、文件控件额外约束、数组元素文件语义、零字段动态空输入、动态文件字段与本次 `attachment_ids` 绑定、真实员工编辑器 Schema 配置入口、动态表单无法渲染的嵌套输入 Schema、前端无法一致表达的 JSON Schema 关键字、发布 v2 后旧幂等键重放仍固定原 Run/原版本 `output_schema`、DeepAgent 数字标量结构化输出、字符串 Schema 下普通数字文本不误转、可选布尔字段不静默提交 false、必填布尔字段未触碰时按 false 提交、正则、日期、数值倍数、数组长度和唯一性。GREEN 阶段 `cd backend && uv run pytest tests/contract/runs/test_dynamic_io.py -q` 29 项通过；`cd backend && uv run pytest -q` 1012 项通过、39 跳过；`cd backend && uv run pytest tests/unit tests/contract -q` 863 项通过；`.venv/bin/ruff check .`、`.venv/bin/mypy` 181 个源码文件通过；`cd frontend && pnpm test -- --reporter=dot` 40 个文件、188 项通过；`pnpm lint`、`pnpm typecheck`、`pnpm build` 通过；`bash infra/platform/test-runtime-e2e.sh` 使用随机端口和独立 Compose 项目完成普通 Worker、结构化输入输出、取消慢模型 3 项真实运行时 E2E，结构化场景通过真实编辑器配置 Schema，不再通过 route 篡改员工定义，结束后临时容器、网络和卷清理完成 |
| C08 Skill 生命周期专项 | RED 阶段后端新增测试先失败于缺少 `skills.security`、`skills.builtin` 和固定版本物化类型，前端新增组件测试先失败于缺少“安全审核结果”面板；GREEN 阶段 `uv run --directory backend pytest tests/unit/skills tests/contract/skills tests/integration/database/test_migrations.py tests/unit/workers/test_runtime_composition.py -q` 47 项通过；合并 C05 后发现 `/api/v1/conversations` 未进入能力包 Core API 保留根契约，已补齐 `CORE_API_ROUTE_ROOTS` 并通过 manifest 契约 8 项；正式 Skill Playwright E2E 1 项通过，按 running 状态接管本轮启动的独立 Compose 依赖并自动 `down -v`，测试 project 容器、网络和卷复查为 0 |
| C14 审计与观测专项 | RED 阶段后端先补出审计元数据仓储边界脱敏、审计哈希链完整性、保留清理、请求 correlation_id 传递和观测告警域覆盖用例；GREEN 阶段 `cd backend && uv run pytest tests/contract/test_health.py tests/contract/audit/test_audit_events.py tests/unit/observability/test_telemetry.py tests/integration/database/test_migrations.py -q` 31 项通过；补强 Worker、队列、模型网关、RAGFlow 和 Sandbox 操作指标后，`cd backend && uv run pytest tests/unit/observability/test_operational_metrics.py -q` 2 项通过、`cd backend && uv run pytest tests/unit/workers/test_main.py tests/unit/knowledge/test_ragflow_client.py -q` 42 项通过、`cd backend && uv run pytest tests/unit/queue/test_redis_run_queue_claim.py tests/unit/queue/test_redis_run_queue_dlq.py tests/integration/queue/test_redis_run_queue.py tests/integration/queue/test_run_dead_letters.py -q` 24 项通过/6 项条件跳过、`cd backend && uv run pytest tests/unit/observability/test_telemetry.py tests/unit/workers/test_runtime_adapters.py tests/unit/workers/test_main.py tests/unit/runtimes/test_deep_agent_runtime.py -q` 57 项通过、`cd backend && uv run pytest tests/unit/llm/test_litellm_gateway.py tests/unit/sandbox/test_local_controller_provider.py tests/unit/sandbox/test_controller.py tests/unit/sandbox/test_manager.py tests/unit/workers/test_sandbox_janitor.py -q` 86 项通过；`cd backend && uv run ruff check . && uv run mypy` 184 个源码文件通过；`uv run --project backend python infra/observability/test_config.py` 通过 Collector 与告警规则配置校验；`cd frontend && pnpm exec tsc -b --noEmit && pnpm exec oxlint` 通过；前端 vitest 证据更正：此前记录的"4 个文件、28 项通过"在当时不成立——`src/features/operations/api/audit.test.ts` 存在断言笔误（mock `sequence: 2` 断言 3），其中 1 项自创建起失败；修正笔误后实测 `cd frontend && pnpm exec vitest run src/features/operations/api/audit.test.ts src/features/operations/api/queries.test.tsx src/features/operations/pages/AuditObservabilityPage.test.tsx src/app/App.test.tsx --reporter=dot` 4 个文件、28 项通过，`pnpm exec vitest run src/features/operations --reporter=dot` 5 个文件、16 项通过；正式 Playwright 审计运维入口使用隔离端口验证审计查询、页面展示和资源清理。第二轮复审修复（M1 每租户独立事务清扫、M2 完整性分块滚动校验、L1 telemetry 全局复位、L2 审计写入对指标异常免疫）后回归：`cd backend && uv run pytest tests/unit/observability tests/contract/audit tests/integration/database/test_migrations.py -q` 68 项通过；`uv run pytest tests/unit tests/contract -q` 896 项通过；`uv run ruff check . && uv run mypy` 185 个源码文件通过；临时 PG 容器 `TEST_DATABASE_URL=... uv run pytest tests/integration/audit -q` 7 项通过（容器已删除）。S7 HMAC 密钥签名加固（`task/c14-audit-hmac`）：RED 用例证明旧实现对无密钥全量重写伪造链返回 valid=True、保留边界篡改不被检出、密钥缺失不 fail-closed；GREEN 后 `cd backend && uv run pytest tests/unit/observability tests/contract/audit tests/integration/database/test_migrations.py -q` 78 项通过（既有篡改/尾删/跨块契约全部保持通过）；`uv run pytest tests/unit tests/contract -q` 917 项通过；`uv run ruff check . && uv run mypy` 187 个源码文件通过；临时 PG 容器 `TEST_DATABASE_URL=... uv run pytest tests/integration/audit -q` 7 项通过（含迁移 `20260716_0025` 真实 PG 升级，容器已删除）；`python3 -m unittest discover -s infra/platform -p 'test_contract.py'` 44 项通过。2026-07-17 隔离验收栈终验 `bash infra/platform/test-mvp-profile.sh` 完整通过（exit 0：镜像构建、迁移 0025 TOFU 回填、Demo Seed、Playwright 3 项、真实 Tauri wdio 1 项、生产 Worker 链路、真实 PG 并发、profile 治理断言全过，验后资源销毁）；验收脚本 `rg --quiet` 已全部替换为 `grep -q` 消除未声明工具依赖 |
| C17 能力授权专项（分支内，待合入） | RED 阶段先失败于缺少 `platform/entitlements` 领域、`capability_entitlements` 迁移/仓储、生产 `CapabilityHost`、组合根、`GET /api/v1/capabilities/registry` 三层裁剪、social 路由三层 gate、Demo Seed 授予与前端裁剪条目 schema；GREEN 阶段 `cd backend && uv run pytest tests/unit tests/contract tests/integration/database tests/integration/bootstrap -q` 971 项通过；`uv run ruff check .`、`uv run mypy`（194 文件）通过；`cd frontend && pnpm test` 43 文件 199 项通过；`pnpm lint`、`pnpm typecheck`、`pnpm build` 通过；隔离随机端口/项目名 Playwright `capability-entitlements.spec.ts`（未授权租户菜单不可见且直达被拒；Owner 授予后菜单可见、真实注册设备成功、撤销后入口消失且直连 API 403）与既有 `social-operations.spec.ts` 共 5 项通过；随后第二个隔离随机栈跑完整 Playwright Web 回归 22 项通过（原 20 项 + C17 新增 2 项），两轮验收栈容器/网络/卷均自动销毁 |
| Tauri Rust | 2 项凭据键校验与 3 项本地执行器集成测试通过；`cargo fmt --check`、`cargo clippy --all-targets --all-features -- -D warnings` 通过 |
| PlatformAdapter | Web/Tauri 双实现覆盖文件、外链、通知和安全凭据；2 个测试文件、6 项测试通过，业务源码无 Tauri 直连 |
| Tauri 桌面 E2E | macOS 本机 3 项真实应用启动、IPC、凭据失败关闭与无端口 Sidecar 生命周期通过；另有 1 项固定 Demo 账号的完整 MVP 核心纵切通过。测试 App 隐藏且不占 Dock，正式构建无 WebDriver 测试标记 |
| 百炼最小真实请求 | `bash infra/litellm/test.sh real-provider` 通过 `general-purpose` 稳定别名、隔离 LiteLLM 和北京地域兼容接口调用 `qwen-plus` 成功，返回 23 Token；临时容器、网络和卷清零 |
| 无付费模型默认回归 | LiteLLM 配置契约 17 项、Stub HTTP 协议 5 项通过；本地 Stub 协议矩阵通过并自动清理临时 Compose 项目；包含仅由显式测试场景触发的确定性 HTTP 500，以及真实附件相对路径的 `glob → read_file → write_file → create_artifact` 序列 |
| C03 MVP Profile 基础设施验收 | 隔离唯一随机端口真实启动 PostgreSQL、Redis、MinIO、LiteLLM Stub、API、Dispatcher、Worker、Sandbox Controller/Janitor 和 Web；生产 `LiteLLMChatModelFactory → LiteLLM → Stub` 调用、状态查询、重复启动、故障健康检查、保留卷停止、失败重启清理与恢复、同 Profile 并发拒绝、工作树镜像隔离及最终容器/网络/卷清理通过；dotenv 不执行、运行目录/权限/端口/网络配置校验通过；平台契约 42 项通过；行为回归额外覆盖无 `rg` 时预存卷保护、重复启动失败不拆既有容器、缺失环境状态时停止失败关闭、LiteLLM 网络检查异常失败关闭、外来网络拒绝删除、网络删除失败传播、Compose `up` 前分组端口占用拒绝，以及启动期间 `INT`/`TERM`/`ERR` 的退出码、差集清理和锁释放；正式 Playwright 业务纵切完成成功与受控模型失败两条真实链路，并验证工作台聚合、事件持久化、页面终态与刷新恢复；RAGFlow 未启动 |
| 完整本机栈 E2E | 本地 Stub 下 2 项正式 Playwright 场景通过：成功场景完成注册、登录、员工发布、任务执行并在工作台展示真实员工/任务状态；失败场景经生产 Dispatcher/Worker/LiteLLM 返回确定性 HTTP 500，持久化 `failed` Run、错误码和 `run.failed` 事件，并在工作台展示真实失败计数。macOS 真实 Tauri 另以固定 Demo 账号完成登录、员工发布、任务执行、终态和工作台聚合纵切；后端工作台契约/映射 8 项、前端工作台 9 项通过。百炼真实 `qwen-plus` 请求通过 LiteLLM 稳定别名完成并返回真实用量 |
| macOS/Windows Tauri 构建 | GitHub Actions `Tauri desktop validation` 运行 29334098300 双平台通过：正式桌面构建、Rust 测试与 2 项真实桌面冒烟均通过 |

**当前已知失败（2026-07-17，[T9] 合入后更新）**

**采集条件必须与数字同行——本节此前的失真全部源于省略它。**

| 采集条件 | 后端全量结果 |
| --- | --- |
| `TEST_DATABASE_URL=真实 PG`，**无** `TEST_REDIS_URL` | **1860 passed / 22 skipped / 0 failed / 0 errors** |
| `TEST_DATABASE_URL=真实 PG` **+** `TEST_REDIS_URL=真实 Redis` | **1873 passed / 9 skipped / 0 failed / 0 errors** |
| 真实 PG，仅 `tests/integration` | **326 passed / 22 skipped**；**同一未重置库连跑 4 轮恒定 326**（证明跨文件顺序/残留已稳定） |

以上由 [T9] 二轮规格复审在一次性 PG/Redis 容器（随机端口、只绑 127.0.0.1、验后销毁）**独立复跑复现**，非转述。

**T8 三条红线 + [T9] 的 5 failed / 13 errors 均已归零，证据如下（按本节规矩，不得只写 0）：**

- **两个来源，不是一个。** 台账原写「根因＝`test_postgres_scheduler_concurrency.py` teardown 清单漏 `tenant_model_gateway_policies`」**不完整**，经 T9 实现代理与 T9 规格复审**各自独立实测订正**：
  - **13 errors = teardown 漏表**：清单删 `UserRecord`/`TenantRecord` 却不含 C16 新增表 → `ForeignKeyViolationError: tenant_model_gateway_policies_updated_by_fkey`。**这 13 条是 teardown 错，用例本身已通过并已计入原 1850**——由账目对账反证：`1850 + 5(failed→passed) + 5(新增门禁用例) = 1860`，与实测吻合；若为 setup 错则应为 1873，与实测差 13。
  - **5 failed = before 态残留**：`tests/integration/runs/` **完全无清理**（实测跑完留下 `runs=5 users=5 tenants=5`），而 `runs/` 排在 `scheduling/` 之前，scheduling 的 `count(RunRecord) == 1` 是**无过滤全库计数** → 读到残留（`assert 19 == 1`）。**只补 teardown 对这 5 条无效**（规格复审构造「只补 teardown」变体实跑，仍剩 1 red），故实现必须是 before + after 双清理。
- 修复方式：`tests/fixtures/postgres_reset.py` 从 `Base.metadata` 自动推导表清单，单条 `TRUNCATE ... RESTART IDENTITY CASCADE`；零生产改动、未加 skip、未放宽断言。
- **门禁非假绿**（两个复审各自独立变异验证）：清单漏掉两张孤岛表 → 红且点名 `run_dead_letters`/`tool_audit_events`；拆掉 Demo Seed 护栏 → `DID NOT RAISE`；把护栏挪到 TRUNCATE 之后 → 根本不 raise（demo 用户已被删光，正面证明「先删再报错等于没护栏」被测到）；还原 → 5 passed。

> **一轮质量复审 FAIL 抓到的要害（记录备查）**：门禁 ① 声称「metadata 注册的所有表都空了」，实际**只锁到 CASCADE 可达子集**。`run_dead_letters` / `tool_audit_events` 的 `tenant_id`/`user_id` 是**裸 uuid 列、无 FK 约束**，CASCADE 永远够不到；而门禁只种了 CASCADE 可达的表 → **「退回手工清单」变异照样全绿**。即：T9 要根治的那类缺陷（清单漏表 → 静默残留）能从 T9 自己的门禁下原样溜过去。**实现一直是对的**（两张表在 metadata 里、被直接列进 TRUNCATE），瞎的是门禁。
>
> **修复副作用也被同轮抓住**：TRUNCATE 要 ACCESS EXCLUSIVE，旧的 DELETE 只取行锁。持锁连接在场时 reset **永久挂住无诊断**（实测 30s 超时兜底才死）。已加 `SET LOCAL lock_timeout = '5s'` + `55P03` → `DatabaseResetLockTimeout`，约 5s 快失败并点名「有用例泄漏了未关闭的 session/事务」。

**⚠️ skipped 是什么、哪些是盲区——如实登记：**

- **9 skipped（PG+Redis 齐备时的真实底线盲区）**：真实腾讯云 COS 5 项、真实 MinIO 2 项、破坏性本地 Docker 沙箱 1 项、真实 RAGFlow 1 项。**这 9 条是当前无法用本机一次性容器覆盖的外部依赖，属已知盲区。**
- **22 skipped = 上述 9 + 13 条 Redis 门禁**。其中 **4 条是 PG+Redis 双门禁**（`test_real_runs`、`test_dispatcher_process_integration`、`test_real_employee_definitions`、`test_real_auth_dependencies`）——**它们碰真实 PG，却因缺 Redis 被跳过**。因此**「无 PG 相关 skip」≠「PG 门禁已全跑」**；`22 − 9 = 13` 与 `1873 − 1860 = 13` 双向自洽。
- **教训（保留）：skip 原因是复合条件时，不能只看其中一个依赖就宣称该依赖的门禁已全部执行。** 该失真由主代理写于 T8 合并提交，经 T9 规格复审实测抓出。

**[T10] 结构性残余（已登记，本轮不做，非阻断）**

21 个使用 `TEST_DATABASE_URL` 的测试文件中**只有 4 个**调用共享清理（实测：`grep -rl` 22 命中减去 fixture 自身 = 21，调用方 = approvals / scheduling / model_gateway / test_postgres_reset），其余 **17 个仍会把数据留在共享库**。当前状态严格优于修复前且有证据（4 轮恒定 326）；这 17 个文件的行为**未被 [T9] 改变**（改前泄漏、改后照样泄漏），其断言按 uuid/租户收窄故不红。

**裁决（主代理）：不在 [T9] 内扩大范围。** autouse 清理下沉到 conftest 会改变 17 个文件的行为、令 40 张表的 TRUNCATE 乘以每个用例、并使 Demo Seed 护栏在每个用例触发，属需单独测性能的独立风险面。

**[T10] 的具体触发条件（不得开放式挂账）：这 17 个文件的「良性」完全依赖其断言保持 uuid/租户收窄，无任何机制强制。一旦其中任一文件新增全库计数/全库存在性断言，即会静默依赖执行顺序——届时 [T10] 必须立即启动。**

**已登记跟踪（LOW，不阻断，两个复审各自提出）**

- **护栏残留会让 PG 套件「变砖」且不可自愈**：`test_postgres_reset.py` 是全仓唯一往真实 PG 写 `DEMO_EMAIL` 的测试，`finally` 兜住异常但**兜不住 SIGKILL/超时**。残留后果实测：`77 passed, 21 errors`——夹具的 setup reset 也被自己的护栏挡下，**重跑救不了自己**，只能人工一条 SQL 删。且报错信息只说「指向了常驻开发栈」，在这个场景是**误诊**。概率低（推荐工作流本就是一次性容器）、恢复成本一条 SQL，但诊断会绕远路。修法：报错信息补第三种成因，或让门禁用例改走可回滚路径。
- **`test-mvp-profile.sh` 的约束只活在 docstring 里**：该脚本在同一个库上 `:212` 跑 demo_seed、`:439` 跑 PG 集成测试。那 3 个并发文件按**无过滤全库计数**断言、要求整库清理，**与已 seed 的栈根本不兼容**——只因今天 `:439` 只跑 `test_artifact_repository.py` 的 2 个用例（不在 4 个调用方里）才没炸。**该不兼容在 T9 之前就成立**（旧代码 `delete(UserRecord)` 一样清光 demo seed），T9 的护栏把「静默毁掉用户验收数据」变成「当场拒绝并指名道姓」，方向正确。将来有人往 `:439` 加用例**会炸——而这正是想要的**，它炸的是一个本来就不成立的组合。建议在 `:439` 就地加注释登记该约束。
**❌ 本节禁止再写「当前已知失败：无 / 0」**——除非每一条都有归零证据。第 6 节此前长期声明「无」而与 3 条红线共存，已被认定为台账失真。（T8 规格复审在主代理准备「清零」时当场拦下并强制写入；[T9] 合入时保留。归零必须像上面那样逐条附证据与采集条件，而不是一个数字。）

**已登记跟踪（LOW，T8 双复审发现）**：`runs.py:677-681` 与 `approvals/service.py:199-201` **两处** docstring 都写着「没有审批记录时返回 False/None，调用方走原有流程」，但调用方自 `b319bc2` 起就是 409 fail-closed，「原有流程」在代码中已死。规格复审用三重论证确认**无 fail-open 旁路**（调用链唯一收敛到 409；approve/reject 的 `approval_id` 恒非 None 故 raw 命令路径不可达；变异实测反证该 raise 是唯一出口），属注释腐烂，单开 docs-only 提交订正。

**红线 1**：`infra/litellm/test_config.py::LiteLlmComposeContractTest::test_local_stub_override_is_test_only_and_not_published`

`infra/litellm/test_config.py::LiteLlmComposeContractTest::test_local_stub_override_is_test_only_and_not_published` 在 main 上 FAILED（`1 failed, 16 passed`）。经 C16 实现代理与 C16 规格复审**各自独立** `git checkout f5fb483` 实测，确认**早于 C12/C16 两个条目开工即已存在**，与二者无关；两者按「单个任务提交只能包含该任务改动」未夹带修复，处理正确。

~~根因（主代理实测定位）：拿 stub 服务去比 LiteLLM 镜像常量属测试自身笔误，疑似自创建起从未通过。~~

**⚠️ 上述主代理初判经 T8 二分实测证伪，实为回归**：`openai-stub` **原本用的就是官方镜像**，断言当时是对的（`251c5d9`/`87bcd38`/`94d90ab` 时均 1 passed）。是 `ee2b624 fix(core): 完整闭环 C04 文件产物验收` 把它换成本机构建的 `agent-platform-litellm-stub:local` 并把脚本/配置烤进镜像，**没同步更新测试**——实测 `ee2b624^` → 1 passed，`ee2b624` → 1 failed。不是笔误，是**契约迁移后旧断言无人更新**。

**已修复（T8，零生产改动）**：`ee2b624` 迁移后 stale 的不止 image 一行——`volumes` 断言（脚本已烤进镜像、不再 bind mount）与 litellm 的 `/app/config.yaml` 挂载断言（已改为 `--config /app/config.stub.yaml`）同样过时。按「先确认预期语义再改断言、不得放宽隔离契约」，把契约追到迁移后的位置，并**补回旧断言曾隐含提供、迁移后无人再守的保证**：新增用例断言 `Dockerfile.stub` 的 `FROM` 必须等于 digest 锁定的 `EXPECTED_IMAGE`；把此前完全没钉的 `pull_policy: never`（防公共 registry 抢注同名 tag）也钉上。结果 17 → **18 passed**，4 个变异（stub 换官方镜像 / 删 pull_policy / FROM 漂到 latest / 给 stub 发布端口）**全部被抓**。

**本条不得以「非本轮引入」为由长期挂账**——第 6 节基线此前长期声明「当前已知失败：无」，与该红线共存，属台账失真；已按「文档与代码冲突时以可运行代码为事实」修正，并单开条目收口。

**红线 2（更严重，落在已标 ✅ 的区域）**：`tests/integration/checkpoints/test_postgres_checkpointer.py::test_postgres_runtime_closes_rebuilds_approves_and_reads_final_checkpoint`

由 C16 阶段一的对抗性代码质量复审发现，主代理**在真实 PG 临时容器上独立复现**（`1 failed, 4 passed`；无真实 PG 时 `1 passed, 4 skipped`——见上方方法论盲区）。C16 对 `checkpoints/` 与 `memory/` **零改动**（`git diff --stat main...HEAD` 为空，主代理已核），与 C12/C16 均无关。

~~**性质：这不是测试笔误，是生产代码路径上的真回归。**~~

**⚠️ 上述主代理初判经 T8 二分实测部分证伪。准确结论：是回归（`30e64ac` 引入），但生产代码是对的，红的是被落下的旧契约测试。**

二分实测（每轮全新 PG 容器 + 真实 `TEST_DATABASE_URL`）：`9c1eba7`（测试最后改动）→ **5 passed**；`30e64ac`（langgraph.py 最后改动）→ **1 failed, 4 passed**。`30e64ac` **没有碰这个测试文件**却把它弄红 → 回归点唯一确定。

**根因**：`30e64ac` 把 `_pending_interrupt` 从「**信任图载荷里的 approval_id**」改为「**平台按 `uuid5(NAMESPACE_URL, f"agent-platform:{run_id}:{occurrence_id}")` 派生**」，载荷里的 `approval_id` 只做格式校验后丢弃。**这是有意的安全加固**——不让图作者自选审批身份。失败的用例写于 `9c1eba7`，用自己塞进载荷的 uuid4 去 `approve()`，旧契约下对、新契约下永远对不上。

**关键**：`30e64ac` **其实更新了契约测试**（`tests/integration/runtimes/test_langgraph_runtime.py` 改动 192 行，已完整覆盖新契约：`platform_approval_id != APPROVAL_ID`、重建后 id 不变、错 id 抛 `RuntimeControlMismatch`）。它**只漏了 PG 门禁的这一个**——而后者恰好因为不设 `TEST_DATABASE_URL` 就被 skip 而藏了下来。**这是上方「方法论盲区」的直接实例，不是孤例。**

**已修复（T8，零生产改动，`_require_approval` 一行未动）**：把用例对齐到 `30e64ac` 确立、且已在别处覆盖的现行契约（从 `approval.required` 事件取平台下发的 id），并补上它独有的价值——**证明派生 id 能扛住真实 PostgreSQL 的序列化往返**。新增断言：`platform_approval_id != business_approval_id`（图作者不能自选审批身份）、**重建后 id 不变**、重建后 `approve(载荷号)` 与 `approve(伪造号)` 都必须抛 `RuntimeControlMismatch` 且运行不被推进、换 run 得到不同审批号（跨 run 不能互相顶替）、拒绝路径错号必拒。**净效果是这道安全校验被钉得更死。**

变异验证（T8 做 + 主代理独立复跑确认）：M1 回退到「信任图载荷 approval_id」→ 1 failed；M2 派生 id 跨重建不稳定（uuid5→uuid4）→ 1 failed；M3 弱化 `_require_approval` 不再比对审批号 → 1 failed；全部还原 → 5 passed。**M2 尤其重要**——它证明该用例现在能抓住「Worker 重启后审批全线卡死」这类此前无人守的故障。

**该路径属 C11（工作流/混合员工，LangGraph 编排内核）与 C13（审批中心）的交界，两者均已标 `✅ 已完成`。** 收口时必须先查明：是 `30e64ac` 的安全边界加固引入的回归，还是 C11/C13 合入时该用例本就未在真实 PG 下跑过（参照 C14 记录过的「前端 `audit.test.ts` 断言笔误、该用例自创建起未通过」先例）。**若确认为回归，C11 和/或 C13 的 `✅` 标记必须重新审视**——完成定义里的「人工审批、拒绝、继续和取消」若在真实 PG 的 checkpoint 恢复路径上不成立，那个 ✅ 就不成立。

**范围收窄（2026-07-17 主代理在常驻开发栈上实测定位）**：**普通的「审批 → 继续」活路径是通的**——在 `agent-platform-dev` 上以真实用户路径发起 Demo 员工任务，Run 走到 `waiting_for_approval`（事件序列 `run.started → run.progress → approval.required`），经 `POST /api/v1/approvals/{id}/approve`（C13 审批中心 → 驱动 C11 运行时）批准后 Run 正常推进到 `completed`。该实测与 T8 的结论一致：生产路径没坏。

**C11 的 `✅` 在这条路径上成立（T8 给出证据，主代理据此维持标记）**：T8 实测了真正要害的不变量——派生 id 跨重建是否稳定：

```
payload approval_id in graph : 9af42a47-a9b3-4e64-835e-a7e585da2097
START  issued approval_id    : d29945e8-8fdf-5e84-b0dc-84e738443cf9
RECOVER derived approval_id  : d29945e8-8fdf-5e84-b0dc-84e738443cf9
>>> STABLE ACROSS REBUILD    : True
approve(recovered id) -> completed
```

**稳定，且重建后审批直达 `completed`。** 这个不变量是生产关键：`run_worker.py:1757` 在恢复后拿 `pending_approval_id()` 去跟审计/持久化审批号对账——若不稳定，Worker 每次重启后审批就会全线卡死。它是稳定的 → C11 完成定义里的「LangGraph Checkpoint、持久化、进程重启恢复、Interrupt、人工审批」在真实 PG 下成立。

**红线 3（T8 新发现，落在 C13，主代理决策见下）**：`tests/integration/runs/test_postgres_terminal_concurrency.py::test_api_terminal_control_records_non_terminal_intent[reject]` —— **即使单独在全新 DB 上跑也失败**（`assert 409 == 202`），不是隔离问题。二分定位：`b319bc2^` → 4 passed；**`b319bc2 feat(core): C13 独立审批中心` → 1 failed**。用例用**随机 uuid4** 当 `approval_id` 发 reject 并期望 202，而 C13 在 `runs.py:612` 显式加了 fail-closed（注释写着「安全 fail-closed……绝不静默回退老通道直发 raw 命令」）→ 查无审批记录返回 409 `approval_record_missing`。**产品行为是对的，红的又是旧断言——与红线 1、红线 2 同一个病根。**

**已修复（T8，零生产改动，`runs.py` 的 fail-closed 一行未动）**：让用例先经 `Approval.create` + `add_idempotent` 建**真实 pending 审批记录**、用其 `invocation_id` 断言 202；并**新增 `test_run_control_reject_without_approval_record_is_fail_closed` 把 C13 的 fail-closed 正面钉住**——此前该安全校验唯一「碰到」它的测试，恰恰是个期望它不存在的旧断言。新用例断言的不只是 409，而是**真正的零副作用**：run 状态仍 `WAITING_FOR_APPROVAL`、无事件、**命令表条数前后不变**（挡住「返回 409 但已落 raw 命令」这种更隐蔽的 fail-open）。

变异验证（T8 做 + 两个复审各自独立重做）：删掉 fail-closed → 转红 `assert 202 == 409`（正是 `b319bc2` 之前的旁路行为）；换成「返回 409 但仍落命令」→ 转红 `assert 2 == 1`（质量复审实测 `commands_before = 1`，证明那是**真增量断言**、不是 `== 0` 的空门禁）。

**对 C13 `✅` 的决策（主代理）：维持 ✅，但如实记录其证据基础不完整。** 理由：产品行为正确且被验证（C13 的真实 PG 并发门禁 3/3 通过），完成定义里的「Tool 审批、工作流审批复用同一平台协议」「并发审批、越权、过期」均成立；红的是一条承载旧契约的断言。**但必须记下**：C13 合入时把一个真实 PG 门禁留成了红的，而其完成证据「真实 PG 并发 3/3」是**不含该文件的定向子集**——同一个方法论盲区。修复方向：让用例先建真实审批记录、用其 id 断言 202，并**补一条随机 id → 409 的用例把 C13 的 fail-closed 正面钉住**（此前该 fail-closed 无任何正面覆盖）。

**红线 4（前端默认 Playwright 套件；编号由主代理在合并时从「红线 3」改开——C12 阶段二分支基于旧基线 `cc25cd3`，当时只有红线 1/2，与 T8 并行期间各自记为「红线 3」，属并行分支不可避免的合并期撞号，非实现代理编号失误）**：`frontend/e2e/workspaces.spec.ts:49 › owner 创建员工时只能选择已接通的真实配置`

该用例第 61 行断言 `option '固定流程（尚未开放）'` 的 `aria-disabled` 为 `true`，但员工编辑器早已把工作模式开放为 `{ value: 'workflow', label: '固定流程' }`（无「尚未开放」后缀），选项也不再禁用——**用例与产品行为脱节，属旧改动未同步 E2E 的遗留**。经 C12 阶段二实现代理**在基线提交 `cc25cd3` 的独立 worktree 上实测复现**（同一行、同一报错），确认**早于 C12 阶段二开工即已存在**，与本条目无关；按「单个任务提交只能包含该任务改动」未夹带修复。

收口要求：先确认工作模式开放后该用例的预期语义（是删除这两条断言，还是改断言为「三种模式都可选」——`EmployeeEditorPage.test.tsx` 已有组件级用例覆盖后者），再改测试；**不得为了变绿而删掉整个用例**，它同时覆盖「owner 创建员工时只能选择已接通的真实配置」这一真实约束。注：C12 阶段二已同步更新该文件中因定时任务能力接通而失效的另两条断言（`支持定时任务（尚未接通）` → `支持定时任务` 且可用），但第 61 行的既有失效不在本条目范围内。

## 7. 完成记录

| 任务 | 状态 | 开始日期 | 完成日期 | 提交 | 验证证据 |
| --- | --- | --- | --- | --- | --- |
| C01 | 已完成 | 2026-07-14 | 2026-07-14 | 本任务提交 | `cd backend && uv run pytest -ra`；`uv run ruff check .`；`uv run mypy`；`cd ../frontend && pnpm test && pnpm lint && pnpm typecheck && pnpm build`；`bash infra/litellm/test.sh config`；`bash infra/litellm/test.sh stub-matrix` |
| C02 | 已完成 | 2026-07-14 | 2026-07-14 | 本任务提交 | pnpm 11 工作区配置与构建脚本白名单通过 `pnpm install --frozen-lockfile` 校验；`pnpm test && pnpm lint && pnpm typecheck && pnpm build`；`pnpm exec playwright test --trace=off`；`cargo test --locked`；`cargo clippy --all-targets --all-features -- -D warnings`；`pnpm test:tauri`；GitHub Actions 运行 29334098300 的 macOS/Windows 正式构建与真实桌面冒烟通过 |
| C03 | 已完成 | 2026-07-14 | 2026-07-15 | 本任务提交 | MVP Profile 纵切：`python3 infra/platform/test_contract.py`（42 项通过）；`bash infra/compose/test.sh config`；`bash infra/litellm/test.sh config`（17 项配置契约、3 项 Stub HTTP 协议通过）；`bash infra/litellm/test.sh stub-matrix`；`bash infra/platform/test.sh config`；`bash infra/platform/test-mvp-profile.sh`；`uv run --directory backend pytest tests/unit tests/contract -q`（580 项通过）；`uv run --directory backend pytest tests/unit/workers tests/integration/database/test_migrations.py -q`（65 项通过）；工作台后端契约/映射 8 项、前端工作台 API/查询/页面 9 项及前端全量 98 项通过；`uv run ruff check . ../infra/platform/test_contract.py`；`uv run mypy`。Profile 已具备私有 allowlist dotenv、路径/权限/端口/网络校验、同 Profile 锁、失败启动按容器/网络/卷稳定名称快照清理本轮差集、环境状态缺失与 LiteLLM 网络检查异常时失败关闭、外来网络保留并报错、网络删除失败传播、分组端口预检、启动中断按 `INT=130`、`TERM=143` 与原始 `ERR` 状态仅清理一次、当前工作树专属镜像与真实恢复验收。本地 Stub 的 Playwright 纵切已覆盖成功与受控失败两条真实链路；工作台以租户和既有 RBAC 语义聚合员工、任务、全部运行状态、失败数及系统健康，失败链路同时校验 PostgreSQL 中的 Run、错误码和 `run.failed` 事件。`TAURI_MVP_WEB_URL=http://127.0.0.1:18080 pnpm test:tauri` 在隐藏、无 Dock 的真实 macOS App 中以固定 Demo 账号完成登录、员工发布、任务执行、终态与工作台纵切；`pnpm test:tauri` 的 3 项原生冒烟通过；`bash infra/litellm/test.sh real-provider` 通过隔离 LiteLLM 的 `general-purpose` 别名调用阿里百炼 `qwen-plus`，返回 23 Token，临时 Docker 资源清零 |
| C04 | 已完成 | 2026-07-15 | 2026-07-16（质量收口） | 本任务提交 | 原纵切保持闭环，并完成最终质量加固：ASGI 层在 multipart/认证前对重复、伪造声明长度与流式 receive 统一限流；API、Controller 和 Sandbox 统一 25 MiB，9 MiB 与边界上传→Run→物化均通过；未绑定文件采用客户端补偿 + 服务端 TTL，原子保护已绑定文件且清扫受 300 秒节流；同步 UI mutex 与服务端幂等键共同防重，改变任务意图会换键；存储 Provider 与底层 SDK 具备硬超时/禁重试边界，持久 tombstone 在覆盖超时证明边界的窗口内重扫迟到 put、跨重启续扫并在最终删除失败时记录后退休；首次 Worker 物化异常或取消均删除新建 Sandbox/lease。本轮质量收口新增 0020 迁移，连同 C04 既有 0018/0019 建模迁移，升级/降级/存量数据通过。后端 919/39 skip、前端 Vitest 119、Ruff/Mypy/Typecheck/Build 全过；正式 C04 脚本含 46 通过/1 条件 skip、真实 25 MiB Docker Sandbox、随机完整栈 Playwright 3 项和 PostgreSQL Saga 并发 2 项，资源清零且未触碰 `agent-platform-dev`；真实 COS 保留显式外部门禁，质量收口当轮无凭据故 1 skip；2026-07-16 补：用户开通开发桶与子账号后，`TEST_COS_*` 真实 COS 门禁首次执行并通过——首跑即暴露 Provider 对真实 `StreamBody` 调用不存在的 `close()` 的缺陷（单测假实现曾放宽契约），已按 RED→GREEN 修复（假实现对齐真实 SDK 契约 + Provider 改为关闭 raw stream），单测 5、真实 COS 1、全量 896、ruff/mypy 全过 |
| C05 | ✅ 已完成 | 2026-07-16 | 2026-07-16（质量收口 + 主线收口 + 终审合入） | 本任务提交（merge 合入） | RED：`cd backend && uv run pytest tests/contract/conversations/test_conversations.py -q` 首次 404；`cd backend && uv run pytest tests/integration/queue/test_run_worker.py::test_worker_persists_message_output_into_conversation_timeline -q` 首次会话消息为空；`cd backend && uv run pytest tests/integration/queue/test_run_worker.py::test_permanent_preparation_failure_is_persisted_and_acknowledged -q` 首次准备失败未写回会话 error 消息；`cd backend && uv run pytest tests/integration/queue/test_run_worker.py::test_worker_bounds_long_message_output_without_blocking_run_completion -q` 首次会话投影保存 12099 字符、超过 PostgreSQL `conversation_messages.content` 12000 边界；`cd frontend && pnpm test -- src/features/conversations/api/conversations.test.ts src/features/conversations/pages/ConversationDetailPage.test.tsx` 首次缺模块；员工详情“开始会话”用例首次找不到按钮。GREEN：`cd backend && uv run pytest tests/contract/conversations/test_conversations.py tests/integration/database/test_migrations.py tests/integration/queue/test_run_worker.py::test_worker_persists_message_output_into_conversation_timeline tests/integration/queue/test_run_worker.py::test_worker_bounds_long_message_output_without_blocking_run_completion tests/integration/queue/test_run_worker.py::test_permanent_preparation_failure_is_persisted_and_acknowledged -q`（14 passed）；`cd backend && uv run ruff check . && uv run mypy`；`cd frontend && pnpm exec vitest run src/features/conversations/api/conversations.test.ts src/features/conversations/pages/ConversationDetailPage.test.tsx src/features/employees/pages/EmployeeDetailPage.test.tsx src/app/App.test.tsx src/features/runs/pages/RunDetailPage.test.tsx --reporter=dot`（47 passed）；`cd frontend && pnpm lint && pnpm typecheck && pnpm build`；`PLAYWRIGHT_*` 隔离端口下 `pnpm exec playwright test e2e/conversations.spec.ts --reporter=line`（1 passed，真实 PostgreSQL/Redis/MinIO/API/Web，结束后 Docker 容器、网络、卷和残留进程已清理）。主线收口 RED：`cd backend && uv run pytest tests/contract/conversations -q` 首次失败于缺 `followup` 意图命令与 `RunResponse.created_by`；`uv run pytest tests/integration/queue/test_run_worker.py -k followup` 7 条新用例中 5 条首次失败于无派生实现（另 2 条为反向用例）；`uv run pytest tests/unit/platform/conversations -q` 首次失败于缺 `conversation_followup_run_id`；前端 `pnpm exec vitest run src/features/conversations/pages/ConversationDetailPage.test.tsx` 新增取消/跳转用例首次 3 条失败；runtime E2E 自动续跑用例首次失败暴露时序不确定（followup 计数为 0），改用 `slow-complete` 夹具模型固定活跃窗口。主线收口 GREEN：`cd backend && uv run pytest tests/contract/conversations tests/integration/queue/test_run_worker.py -q`（72 passed，2 skipped）；`uv run pytest tests/unit tests/contract -q`（899 passed）；`uv run ruff check . && uv run mypy`；`cd frontend && pnpm exec vitest run src/features/conversations src/features/runs --reporter=dot`（28 passed）及全量 vitest（200 passed）；`pnpm lint && pnpm typecheck`；`PLAYWRIGHT_COMPOSE_PROJECT_NAME` + `PLAYWRIGHT_*_PORT` 随机隔离端口下 `pnpm exec playwright test e2e/conversations.spec.ts --reporter=line`（1 passed，含会话内取消入口与任务详情跳转，隔离栈自动销毁）；`PLAYWRIGHT_COMPOSE_PROJECT_NAME=随机 bash infra/platform/test-runtime-e2e.sh`（5 passed：原有 3 条 + 「活跃任务期间追加消息→当前任务终结后自动跑下一轮→时间线两轮输出」+「会话页直接取消执行中任务→已取消终态」，真实 PostgreSQL/Redis/MinIO/API/Dispatcher/Worker/Sandbox/Web，随机端口，资源自动清理） |
| C06 | 已完成 | 2026-07-16 | 2026-07-16 | 本任务提交 | RED：`cd backend && uv run pytest tests/integration/queue/test_run_worker.py::test_worker_treats_legacy_default_object_output_schema_as_unstructured tests/integration/queue/test_run_worker.py::test_worker_rejects_completed_output_that_violates_employee_output_schema tests/unit/runtimes/test_deep_agent_runtime.py::test_deep_agent_runtime_parses_json_text_for_structured_output_schema -q` 先暴露历史默认 Schema 误判、违规输出未受控失败和 JSON 字符串未解析；前端员工详情和任务详情新增动态 IO 用例先失败于缺少 Schema 表单与结构化展示；复审补充覆盖 file control/`file_upload` 不一致、历史已发布文件字段 Schema 未启用 `file_upload` 且本次未提交文件时运行入口 fail-open、dynamic properties 未关闭 `additionalProperties`、历史已发布动态 Schema 运行入口 fail-open、legacy 自由输入与零字段动态空输入兼容、浏览器 RegExp 不兼容 pattern 后端放行/前端崩溃、文件字段额外约束、数组文件语义、动态文件附件绑定、真实编辑器 Schema 配置、无法渲染的嵌套输入 Schema、前端无法一致表达的 JSON Schema 关键字、发布 v2 后旧幂等键重放固定原 Run/原版本 `output_schema`、数字标量结构化输出、字符串 Schema 下普通数字文本不误转、可选布尔字段、必填布尔字段和 JSON Schema 关键约束。GREEN：`cd backend && uv run pytest tests/contract/runs/test_dynamic_io.py -q`（29 passed）；`cd backend && uv run pytest -q`（1012 passed、39 skipped）；`cd backend && uv run pytest tests/unit tests/contract -q`（863 passed）；`cd backend && .venv/bin/ruff check . && .venv/bin/mypy`（181 个源码文件通过）；`cd frontend && pnpm test -- --reporter=dot`（40 个文件、188 项通过）；`cd frontend && pnpm lint && pnpm typecheck && pnpm build`；`bash infra/platform/test-runtime-e2e.sh`（独立随机端口真实 PostgreSQL/Redis/MinIO/API/Worker/Sandbox/Web，3 项通过，资源清理完成） |
| C08 | 已完成 | 2026-07-16 | 2026-07-16 | 本任务提交 | RED：`uv run --directory backend pytest tests/unit/skills/test_security_review.py tests/unit/skills/test_builtin_installer.py tests/unit/skills/test_materializer.py tests/contract/skills/test_skills.py -q` 先失败于缺少安全审核、内置安装和固定版本物化接口；`pnpm --dir frontend test src/features/skills/pages/SkillDetailPage.test.tsx` 先失败于缺少“安全审核结果”面板。GREEN：`uv run --directory backend pytest tests/unit/skills tests/contract/skills tests/integration/database/test_migrations.py tests/unit/workers/test_runtime_composition.py -q` 47 项通过；`uv run --directory backend pytest tests/unit/capabilities/test_manifest.py::test_reserved_core_api_route_roots_match_the_running_app_contract tests/unit/capabilities/test_manifest.py::test_manifest_rejects_core_api_route_root -q` 8 项通过；`uv run --directory backend pytest -q` 976 通过、39 跳过；`uv run --directory backend ruff check .` 通过；`uv run --directory backend mypy` 180 个源码文件通过；`pnpm --dir frontend test` 39 个文件、169 项通过；`pnpm --dir frontend lint`、`pnpm --dir frontend typecheck`、`pnpm --dir frontend build` 通过；独立端口和独立 Compose 项目的 `pnpm --dir frontend exec playwright test skills.spec.ts --trace=off` 1 项通过，Playwright 按 running 状态记录本轮 ownership 并自动 `down -v`，`agent-platform-playwright` 与 C08 隔离 project 容器、网络和卷复查均为 0 |
| C07 | ✅ 已完成 | 2026-07-16 | 2026-07-17 | 本任务提交（merge 合入） | 双复审通过后合入：迁移 0027 重链 0026；后端全量 1198、默认 Playwright 23/23（单 worker）、runtime E2E 6/6；真实 RAGFlow v0.25.6 独立栈验收通过（检索契约/元数据过滤/生命周期，栈用后销毁）；常驻栈真实用户冒烟通过；已声明限制（重排端到端待重排模型实例、事件键单检索）见第 4 节终审记录 |
| C14 | ✅ 已完成 | 2026-07-16 | 2026-07-17 | 本任务提交（两次 merge 合入 + 收口提交） | 三轮复审后主体合入 main；用户决定立即加固 S7，HMAC 密钥签名分支经安全复审（一轮整改 + 增量确认 PASS）合入：合并后后端全量 1067 通过、Playwright 20/20（隔离端口）、常驻栈重建后以真实用户路径冒烟通过（登录 → 审计页 → `auth.login_succeeded` 以 `hmac-sha256.v1` 落库、链头封印生效、迁移 0025 TOFU 回填完成）。2026-07-17 隔离验收栈终验 `bash infra/platform/test-mvp-profile.sh` 完整通过（exit 0，详见第 4 节 C14 终验记录与第 6 节基线）；剩余威胁面（持钥攻击者、整库回滚需外部锚定）已如实声明归 C18；L3-L5 follow-up 见第 4 节 C14 记录 |
| C10 | ✅ 已完成 | 2026-07-17 | 2026-07-17 | 本任务提交（merge 合入） | 完成定义逐条满足；双复审 PASS；验证证据与登记的观察项见第 4 节 C10 完成记录（后端全量 1324、前端 230、隔离栈 Playwright 26、runtime E2E 9，均含记忆用例） |
| C13 | ✅ 已完成 | 2026-07-17 | 2026-07-17 | 本任务提交（merge 合入） | 完成定义逐条满足；双复审（首轮质量复审 FAIL→集中修复 fail-closed/真实 PG 并发门禁/守卫→复跑 PASS）；修复 worker 审计 HMAC 生产缺口；验证见第 4 节 C13 完成记录（后端全量 1399、前端 242、真实 PG 并发 3/3、审批 runtime E2E 3 + 合并交叉 5） |
| C11 | ✅ 已完成 | 2026-07-17 | 2026-07-17 | 本任务提交（merge 合入） | 完成定义逐条满足；零侵入硬门禁通过；双复审两轮（首轮质量 FAIL 四项 + 二轮质量抓修复自引入占位注入→均修复复跑 PASS）；验证见第 4 节 C11 完成记录（后端全量 1536、前端 262、真实栈 workflow E2E 2/2 含人工审批经审批中心闭环） |
| C15 | ✅ 已完成 | 2026-07-17 | 2026-07-17 | 本任务提交（merge 合入） | 完成定义逐条满足；三轮双复审（M1 时序枚举来回两轮修至端点限流+诚实降级、L2 真实 PG 无半状态门禁）均 PASS；验证见第 4 节 C15 完成记录（后端全量 1596、前端 273、真实 PG 门禁、members/account Playwright 7/7） |
| C17 | ✅ 已完成 | 2026-07-16 | 2026-07-17 | 本任务提交（收口，无新增代码） | B04 合入后经独立规格复审 + 主代理证据复核收口：待集成项①已闭合（生产装配、无夹具旁路、四组合矩阵）、②③定性为「无对象」且门禁已点名前移至 B08/B02/C20，非 fail-open。完成定义第 6 条逐子句判定与已知局限（I2 标准不对称、L1/L2、L4 触发阈值）见第 4 节 C17 收口记录。验证：capabilities/entitlements 423 passed、video_studio+迁移 29 passed/1 skipped、前端 src/app 46 passed |
| C09、C12、C16、C18-C20 | 见第 4 节 | — | — | — | 按第 4 节逐项更新 |

C05 补充质量验证：代码复审发现会话失败投影除准备失败外，还需要显式覆盖续租失败和孤儿运行恢复失败；进一步复审发现会话投影与 Run 状态、事件、command、ownership/approval 收尾共处同一事务，若 `conversation_messages` 序号并发冲突或投影异常会拖垮核心运行收尾。已补充 RED 用例 `test_worker_completion_survives_conversation_projection_failure` 与 `test_recovered_snapshot_survives_conversation_projection_failure`，修复后投影改为核心事务提交后的独立安全事务，唯一约束冲突最多重试 3 次，最终失败只记录受控日志，不影响 Run 结果。已通过 `cd backend && uv run pytest tests/integration/queue/test_run_worker.py::test_permanent_preparation_failure_is_persisted_and_acknowledged tests/integration/queue/test_run_worker.py::test_renewal_failure_marks_running_run_failed_and_releases_environment tests/integration/queue/test_run_worker.py::test_started_tool_without_advanced_checkpoint_fails_uncertain_without_replay -q`（3 passed）、投影异常降级组（5 passed），并通过包含会话契约、迁移、正常输出、超长输出、三条失败投影和五条投影异常降级的综合后端目标回归（21 passed），确保三条直接失败路径都会写入 `conversation_messages` 的 error 消息，且投影失败不泄露底层异常细节、不阻断核心收尾。

后续每完成一项，将其拆成独立行记录，禁止只修改第 4 节状态而不留下提交标识和验证证据。
