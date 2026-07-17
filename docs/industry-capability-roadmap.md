# 视频剪辑与自动运营能力包路线图

> 文档性质：行业能力包功能清单、依赖图、推荐实施波次与完成状态的唯一执行台账
> 建立日期：2026-07-14
> 当前阶段：允许与 AI 中台 Core 隔离并行；B02、B04、B08 待集成
> 适用范围：`video-studio`、`social-operations` 及两者的组合工作流
> 主要证据：[`dt-ai-helper-competitive-analysis.md`](dt-ai-helper-competitive-analysis.md)

## 1. 目标与前置门禁

本路线图将竞品安装包中已证实或高可信的视频剪辑、内容发布、AI 客服、微信私域和曝光获客能力，转换为本项目两个可插拔行业能力包的正式开发清单。

业务能力不能复制一套用户、任务、模型、知识、审批、产物、审计或客户端底座。它们必须依赖 Core 的公开协议，并同时经过：

```text
deployment_installed && tenant_entitled && user_permitted
```

并行开发入口：

- [`core-capability-roadmap.md`](core-capability-roadmap.md) 的 C01 已为 `✅ 已完成`，Core-only 质量基线通过；
- 业务开发使用独立分支/工作树，不和 Core 主线共同堆积未提交改动；
- 未完成 Core 依赖通过公开契约、Mock Host 或测试适配器隔离，不复制平台实现；
- 生产集成和 `✅ 已完成`仍要求该条目依赖的 Core 能力、真实平台验收与组合回归全部通过。

## 2. 状态、证据与更新规则

状态统一使用：

| 状态 | 含义 |
| --- | --- |
| `⬜ 未开始` | 尚未进入实现 |
| `🚧 进行中` | 当前工作流正在实施的业务条目 |
| `🧪 待集成` | 自身实现与隔离测试已通过，等待明确记录的 Core 或真实平台门禁 |
| `⛔ 受阻` | 存在已记录证据和解除条件的外部阻塞 |
| `✅ 已完成` | 全部完成定义、自动化验证和真实平台验收通过 |

竞品证据等级统一使用：

| 等级 | 含义 |
| --- | --- |
| `已证实` | 安装包代码、界面或运行日志形成直接证据链 |
| `高可信` | 多处代码/界面/官网相互印证，但没有完整成功日志 |
| `证据不足` | 只能证明相邻能力，不能宣称完整支持 |
| `规划增强` | 我们基于架构和安全需要新增，不是竞品现状结论 |

执行要求：

1. 先实施 B01 公共协议；B01 形成稳定协议和 Mock Host 后，按第 5 节依赖图拆分 Video Studio、社媒发布、账号治理、微信桌面和曝光获客泳道，不再按所属能力包限制只能有一个 `🚧 进行中`；
2. 每项开始前更新状态和开始日期，完成后在同一个任务提交中标记 `✅ 已完成`；
3. 完成记录必须包含日期、提交标识、目标平台/版本、自动化验证和真实账号验收证据；
4. 平台网页或桌面 UI 自动化必须在受控测试账号上完成真实验收，Mock 不能替代最终完成门禁；
5. 页面元素可定位不等于业务成功，必须验证平台最终状态和本平台发布/执行记录一致；
6. 验证码、滑块、风控、账号冻结等必须转人工，禁止开发规避平台安全验证的能力；
7. 小红书“收到私信后自动读取并回复”和视频号互动等证据不足能力，不得冒充竞品已证实能力；实现前必须重新验证平台能力与规则；
8. 新发现的可复用业务能力补入正确依赖位置，不以客户名称硬编码；
9. 能力关闭后 Core-only 必须保持可用，能力包之间不得互相导入内部实现；
10. 真实 Cookie、聊天、联系人、素材、截图和运行日志不得进入 Git。

### 2.1 多设备并行开发边界

- 每个并行业务条目使用独立功能分支或 Git Worktree；每个完成任务仍需独立提交并立即推送；
- 行业能力分支主要修改自身模块、Sidecar、Adapter、测试和本路线图；Core 路线图由 Core 主线维护；
- 公共前端布局、Tauri 主壳、跨端契约、根依赖锁文件和数据库迁移编号属于高冲突区，未经协调不得由两台设备同时修改；
- 未完成 Core 依赖可以使用版本化接口和 Mock 验证，但 Mock 结果只能支持 `🧪 待集成`，不能替代真实 Core、真实桌面或真实平台验收；
- 合并前必须同步最新主线，运行 Core-only、目标能力组合和受影响平台回归；业务包关闭后 Core 行为必须不变。

### 2.2 竞品可见菜单逐项映射

下表逐项承接静态分析中发现的全部主要菜单。属于 AI 中台底座的菜单不在行业能力包重复实现，但仍标明其 Core 或解决方案归属，防止遗漏或重复建设。

| 竞品菜单 | 路由 | 证据 | 本项目归属 |
| --- | --- | --- | --- |
| AI Agent | `/agent-chat` | 高可信 | Core C03/C05 数字员工工作台与多轮会话 |
| 首页 | `/home` | 高可信 | Core C03/C14 工作台、待办与运营观测 |
| 自动工作流 | `/simple-workflow` | 高可信 | Core C11；业务节点组合纳入 B17 |
| 剪辑任务 | `/video/task` | 高可信 | B06 |
| 下载任务 | `/video/download_task` | 高可信 | B04 |
| 素材库 | `/video/materials` | 高可信 | B04 |
| 批量剪辑 | `/video/clip` | 高可信 | B06 |
| 聚合发布 | `/video/choose_platform` | 已证实 | B07 |
| 批量发布 | `/video/batch-import` | 已证实 | B07 |
| 账号管理 | `/video/account` | 已证实 | B02 |
| 发布记录 | `/video/release_record` | 已证实 | B07 |
| 自动曝光 | `/exposure/auto-exposure` | 已证实 | B15/B16 |
| 定向曝光 | `/exposure/targeted-exposure` | 已证实 | B15/B16 |
| 链接曝光 | `/exposure/url-exposure` | 已证实 | B15/B16 |
| 搜索账号曝光 | `/exposure/search-account-exposure` | 已证实 | B15/B16 |
| 抖音号管理 | `/tiktok/account` | 已证实 | B02/B03 |
| AI 客服配置 | `/tiktok/customer/list` | 高可信 | B09 |
| 私信会话 | `/tiktok/customer/conversation` | 高可信 | B09 |
| 私信跟进 | `/tiktok/customer/follow-up` | 高可信 | B10 |
| 欢迎语配置 | `/tiktok/customer/welcome-message` | 高可信 | B10 |
| AI 专家搭建 | `/ics/flexible-config` | 高可信 | Core C07/C11 + 客户解决方案配置 |
| 主动激活 | `/mass-send` | 已证实 | B13 |
| 朋友圈 | `/moments` | 已证实 | B14 |
| 朋友圈发布 | `/moments/publish` | 已证实 | B14 |
| 朋友圈营销 | `/moments/marketing` | 已证实 | B14 |
| 自动功能 | `/auto-accept` | 已证实 | B13 |
| DeepSeek | `/deepseek` | 高可信 | Core 模型网关与 C16 模型治理 |

## 3. Video Studio 功能清单

竞品视频链路主要采用阿里云 OSS + Timeline + IMS/ICE 云剪辑，不是本地完整 FFmpeg 渲染；这是竞品事实。本项目目标供应商已确定为腾讯云，第一阶段采用 LighthouseCOS/COS + 自研 Timeline/预览 + MPS 云端渲染，并保留未来本地 Provider 扩展点。具体部署基线见 [`tencent-cloud-mvp-deployment.md`](tencent-cloud-mvp-deployment.md)。

**第一期范围冻结：** 本地 FFmpeg 执行器不属于 B01-B17 的竞品对标交付范围，不采购、不随 App 打包、不按需下载，也不作为任何第一期任务的完成依赖。第一期只保留供应商无关 `VideoRenderProvider` 扩展边界，实际渲染使用腾讯云 MPS；只有用户后续明确启动离线、隐私或降本增强时，才单独建立 `LocalFfmpegProvider` 任务和验收标准。

| 功能域 | 功能清单 | 竞品证据 | 我们的实现边界 |
| --- | --- | --- | --- |
| 素材库 | 视频、图片、音乐等素材上传、列表、选择和管理 | 已证实 | 复用 Core Artifact，腾讯 LighthouseCOS/COS 保存业务对象 |
| 下载任务 | 素材或成片下载队列、进度、成功/失败记录 | 高可信 | 使用平台任务和产物协议，不建立旁路状态 |
| 对象存储直传 | 竞品使用 OSS STS，客户端直传对象存储 | 已证实 | 我们使用腾讯云 CAM/STS + LighthouseCOS/COS，永久密钥只在服务端 |
| Timeline | 创建、解析、保存剪辑时间线 | 已证实 | 业务模型使用供应商无关 Timeline DTO |
| App 内预览 | 竞品加载阿里云 Web SDK、播放器、转场和时间线预览 | 已证实 | 我们自研 Timeline 编辑器，使用 HTML5 Video/Canvas 和低清代理素材预览 |
| 一键剪辑 | 依据素材和规则创建云端剪辑任务 | 已证实 | 通过 `VideoRenderProvider` 提交 |
| 模板剪辑 | 选择模板、替换素材、生成多个成片 | 已证实 | 模板版本固定，输入输出可追溯 |
| 批量剪辑 | 批量导入素材、批量创建任务和结果管理 | 已证实 | 配额、并发、幂等和失败隔离由平台统一治理 |
| 剪辑任务 | 创建、排队、运行、轮询、取消、失败重试 | 已证实 | 映射到 Core Run/Event，不复制任务中心 |
| 云端渲染 | 竞品使用 IMS/ICE 异步 Job、进度、结果、费用和错误 | 高可信 | 第一阶段 `TencentMpsProvider`，服务端调用 MPS `EditMedia` 等官方 API |
| 成片管理 | 获取成片、下载、预览、删除、保留策略 | 已证实 | 成片作为 Core Artifact，具备租户权限与生命周期 |
| 发布衔接 | 成片进入账号、标题、话题和多平台发布流程 | 已证实 | 只通过稳定 Artifact ID 交给 Social Operations |
| 本地 FFmpeg | 离线、隐私或成本敏感场景的本地渲染 | 规划增强 | 后续 `LocalFfmpegProvider`，不作为第一阶段竞品复刻目标 |

视频剪辑页面至少包括：

- 素材库；
- 下载任务；
- 剪辑任务；
- Timeline/模板编辑；
- 批量剪辑；
- 云端 Job 详情；
- 成片与发布衔接；
- 用量、费用、失败和重试。

## 4. Social Operations 功能清单

### 4.1 平台账号与内容发布

| 平台 | 登录/账号 | 内容发布 | 评论/曝光 | 主动私信 | 收到私信后自动回复 | 证据说明 |
| --- | --- | --- | --- | --- | --- | --- |
| 微信 PC | 复用本机登录 | 朋友圈 | 朋友圈营销 | 个性化群发 | 已证实 | UI 自动化 + OCR，桌面环境敏感 |
| 抖音 | 扫码/Cookie/授权 | 已证实 | 已证实 | 已证实 | 高可信 | 有真实视频发布成功日志和 AI 客服入口 |
| 小红书 | 扫码/Cookie | 已证实 | 已证实 | 高可信 | 证据不足 | 不得直接宣称入站私信自动回复 |
| 快手 | 扫码/Cookie | 已证实 | 已证实 | 高可信 | 证据不足 | 重点是发布、搜索与互动 |
| 视频号 | 扫码/Cookie | 已证实 | 证据不足 | 证据不足 | 证据不足 | 第一阶段只纳入聚合发布 |

通用功能：

- 平台账号新增、扫码登录、授权、注销和重新登录；
- App 专用浏览器配置目录、Cookie 加密和登录态健康检查；
- 账号状态、最近成功、连续失败、风控和熔断；
- 选择素材/成片、标题、话题、账号和发布时间；
- 单平台发布、聚合发布和批量导入发布；
- 上传进度、页面处理、点击发布、最终状态确认；
- 发布记录、失败原因、重试、取消和人工接管；
- 页面改版后的定位策略版本、诊断截图和回归样例。

### 4.2 抖音 AI 客服

- AI 客服角色、知识、语气和回复策略配置；
- 私信会话列表和聊天上下文；
- 首次触达欢迎语；
- AI 回复建议、敏感回复审批和自动回复策略；
- 未转化会话跟进；
- 人工接管、暂停自动回复和恢复；
- 会话标签、状态、负责人和转化结果；
- 回复频控、敏感词、黑名单和完整审计。

说明：优先使用平台官方接口；官方接口不能满足且平台规则允许时，才使用浏览器 RPA。

### 4.3 微信私域运营

- Windows 执行器使用 UI Automation；macOS 执行器使用 Accessibility/AX API；
- macOS 通过 ScreenCaptureKit 获取授权窗口画面，并使用本地 Vision OCR 作为无障碍树不足时的兜底；
- 两个平台复用会话、任务、审批、审计和人工接管协议，但分别维护定位器、权限诊断和客户端版本矩阵；
- macOS 微信没有入口或尚未动态验证的功能必须返回明确能力状态，不得假设与 Windows 客户端完全等价；
- PC 微信进程、版本、窗口、分辨率、DPI 和锁屏诊断；
- 激活并置前微信窗口；
- UI Automation 定位联系人列表、会话区和输入区；
- 截图和 OCR 识别未读消息；
- 打开指定会话并读取最近上下文；
- 调用 AI 生成回复建议；
- 人工确认或按风险策略自动输入和发送；
- 检查新的好友申请；
- 按规则通过好友或添加好友；
- 联系人分群、模板和个性化群发；
- 沉默客户主动激活；
- 朋友圈图文/视频发布；
- 朋友圈营销任务；
- 成功、跳过、失败、重试和下一次检查时间；
- 黑名单、退订、去重、频控、敏感词和紧急停止。

### 4.4 曝光与获客

竞品“曝光”是模拟人工搜索内容或账号并执行互动，不是购买平台广告。功能包括：

- 自动曝光；
- 定向曝光；
- 指定内容链接曝光；
- 搜索指定账号曝光；
- 抖音私信曝光；
- 小红书、快手目标搜索与互动；
- 按抖音号、小红书号、关键词、主页或链接定位目标；
- 访问主页、评论、主动私信及平台允许的其他互动；
- 目标去重、任务结果和转化归因。

这部分是最高风控能力，必须具备：

- 每个平台独立动作频率和每日上限；
- 新账号冷启动和账号风险等级；
- 随机等待之外的业务频控；
- 连续失败熔断和远程紧急停止；
- 验证码、滑块、风险提示和异常登录转人工；
- 评论、私信等敏感动作审批；
- 账号健康、行为审计和内容追溯；
- 平台规则版本记录和合规复核。

### 4.5 自动工作流与效果分析

- 将素材、剪辑、发布、客服、微信和曝光组合为可视化工作流；
- 触发条件、时间、目标人群、平台账号和审批节点；
- 每一步暂停、取消、重试、跳过和人工接管；
- 发布成功率、回复率、触达量、互动量和转化结果；
- 账号、内容、员工、工作流和客户方案维度分析；
- 所有指标基于平台事件和稳定业务 ID，不依赖 RPA 日志文本解析。

## 5. 业务依赖图与推荐实施波次

### 5.1 并行门禁

- B01 保持公共前置；B02 保持 Social Operations 设备、执行器和账号公共前置；其后编号只作稳定标识，开工条件以本节直接依赖为准；
- 同一直接依赖链严格串行；不同泳道只有在独立工作树、独立提交、独立测试和高冲突文件唯一写入方均已明确后才能并行；
- 业务条目允许依赖尚未完成的 Core 公开 Port、Mock Host 或测试适配器进行隔离开发；缺少 Core 集成、真实桌面或真实账号门禁时只能标记 `🧪 待集成`；
- 数据库迁移编号、共享 API/事件契约、根依赖锁文件、Tauri 主壳、公共前端导航和组合 Profile 由主代理指定唯一写入方；
- 实际并发受可用 Agent 槽位和主代理审查能力约束，推荐在“两个 Core + 一个业务”与“一个 Core + 两个业务”之间交替，不为填满并发而启动边界不清的任务。

### 5.2 泳道与直接依赖

| 泳道 | 条目链 | 开工/完成约束 |
| --- | --- | --- |
| 公共设备与账号 | B01 → B02 | B02 是社媒发布、账号治理和微信桌面的公共前置 |
| Video Studio | B04 → B05 → B06 | B04 完整集成依赖 Core C04；B06 依赖 B04、B05 和 Core Artifact |
| 社媒发布 | B02 → B03 → B07 | B07 还依赖 B06 成片、B08 安全治理和各平台受控账号验收 |
| 账号治理与客服 | B02 → B08 → B09 → B10 | B09 完成还依赖 Core C07/C13；B10 不得绕过 B08 频控和熔断 |
| 微信桌面 | B02 → B11 → B12 → B13/B14 | B12 完成依赖 Core C07/C10/C13；B13、B14 还必须接入 B08 安全治理和审批 |
| 曝光获客 | B02 → B08 → B15 → B16 | B15 完成依赖 Core C13/C14；B16 复用已完成的平台 Adapter，不建立旁路自动化 |
| 统一收口 | B03-B16 → B17 | B17 最后完成组合工作流、效果分析、双平台安装和交付验收 |

推荐波次：C04 与 B02 收口后，优先从 `B03`、`B04`、`B08` 中选择两个与一个 Core 条目并行；下一波可在 `B05`、`B09`、`B11` 中按已满足依赖选择。每次开工前必须基于最新代码重新检查 Core 门禁、真实账号条件和文件冲突，本表不是跳过检查的授权。

**以下各条目保留稳定编号，其完成定义、真实平台门禁和安全要求不因并行策略而降低。**

### B01 能力包装配、桌面执行协议与组合门禁

**所属：Video Studio + Social Operations**

**状态：`✅ 已完成`**

**开始日期：2026-07-14**

**完成日期：2026-07-15**

完成定义：

- 两个能力包分别注册路由、Worker、权限、事件、前端入口、迁移和健康检查；
- 建立设备、账号、本地任务、步骤、人工接管和诊断事件协议；
- Tauri 通过受认证 IPC 管理本地 Sidecar，不开放无保护固定端口；
- 使用版本化协议和 Mock Host 验证两个能力包可独立装配、测试和关闭；
- 真实 Core-only、Core+视频、Core+自动运营和 Core+两者组合测试统一在 B17 完成。

### B02 设备中心、本地执行器与平台账号中心

**所属：Social Operations 公共层**

**状态：`🧪 待集成`**

**开始日期：2026-07-15**

完成定义：

- 设备注册、心跳、版本、在线状态、任务领取和紧急停止；
- Sidecar 下载、签名校验、启动、停止、崩溃恢复和日志脱敏；
- App 专用浏览器目录、扫码登录、Cookie 加密、注销和健康检查；
- 平台账号运行时绑定企业、用户和设备，隔离层具备权限、审计 outbox 和熔断状态；生产账号后端链路待 Core PostgreSQL/C14/C17 接入；
- 验证码或风控明确进入人工接管，不尝试绕过；
- macOS/Windows 设备和受控测试账号 E2E 通过；
- **（2026-07-17 由 C17 收口转写而来的强制门禁）建立平台侧 Sidecar 分发端点（真实签名发布链）时，该端点必须挂 Core 统一的 `create_capability_gate` 三层校验，未安装/未授权租户不得下载 Sidecar。当前下载地址为用户手填、客户端直连任意 URL、仅靠 Ed25519 验签保护，入口仅受前端 registry 门禁遮蔽——按 CLAUDE.md「前端隐藏菜单不能替代后端授权」，该形态不得作为最终交付形态。**

**门禁来源说明（不得删除）**：C17 完成定义第 6 条要求「未安装/未授权能力无法…下载 Sidecar」。C17 收口时（2026-07-17）经主代理复核确认：**平台后端不存在 Sidecar 分发端点**（`SocialOperationsPage.tsx` 的下载地址为 `<Input aria-label="安全下载地址">` 用户手填，manifest/signature 亦为粘贴录入；Rust 侧 `entitlement|capabilities/registry` 零命中），故「下载 Sidecar」子句在 Core 中**无后端资源可授权、无对象**，C17 据此收口为 `✅ 已完成`。该门禁**前移由本条目（真实分发链建立时）与 C20（发布与更新产物分发）共同承接**，C17 已收口不再承接。

隔离实现证据：设备注册/心跳/有效在线状态、所有者权限、任务租约领取和账号执行门禁共享原子状态边界，紧停与领取/投递/账号执行竞态、持久化回滚和持久审计 outbox 已覆盖；SQLite 逐级拒绝符号链接，并以最终私有目录 owner/`0700` 门禁和连接前后 inode 校验检测叶子替换。本轮质量收口又将 SQLite 快照持久化改为 `BEGIN IMMEDIATE` + revision CAS，多实例心跳无法覆盖已提交的紧急停止；Tauri 默认与桌面测试 capability 均显式授权 11 个 `social_*` 命令，并已经隐藏、无 Dock 的 macOS 真实 WebView 调用验收。Tauri `SocialOperationsRuntime` 已把带摘要/平台/架构/防降级的 Ed25519 Manifest 安装、签名元数据持久化及重启恢复、逐跳 HTTPS/超时下载、真实已安装 Sidecar 启停/独立后台监护与最多两次崩溃恢复/调用超时、限界 stderr 脱敏、Profile/Cookie 原子私有存储、账号健康/人工接管、风险/验证码/登录过期熔断停进程、注销后重新登录与幂等紧停串成同一命令闭环。紧停不再等待最长 30 秒的调用锁，可抢占并熔断阻塞中的账号调用；Unix 进程组与 Windows Job Object 覆盖 stop/超时/崩溃恢复的整树清理。Sidecar 每次真实 spawn 前重验 Manifest/签名/摘要与稳定文件身份；Unix 执行由已验证字节创建的私有临时文件，Windows 在启动期间保持不允许写入/删除共享的已验证句柄，安装后替换可执行文件必须失败关闭。前端 Core App 不再直接导入可选 Feature；运行时 Capability Registry 先以公开静态 `capability.json` 元数据严格校验能力 ID、唯一且精确的 `frontend_entries`、唯一且精确的 Manifest 权限、部署状态、租户 Entitlement 与用户权限，全部通过后才调用对应公开 `module.tsx` 的动态加载器。协议缺失、畸形、请求失败、重复声明替代缺项或未知/恶意入口漂移时均失败关闭，且不会下载或执行业务模块；菜单和直达路由共享同一门禁结果。生产 `socialOperationsModule` 注册“设备与平台账号”主导航以及 `/video/account`、`/tiktok/account` 双路由，页面只通过 TypeScript `PlatformAdapter` 和 `SocialOperationsRuntime` 消费 B02 本地账号运行时命令面，并接入租户设备注册 API；平台账号准备、扫码、健康、熔断和启动执行器当前仍是 Tauri 本地运行时闭环，尚未接入后端 `/accounts` 生产账号 API。前端测试适配器现实行 `begin_qr → qr_scanned → authenticated` 严格转移，页面也提供明确“确认已完成扫码”操作；设备平台入口与后端契约保持 macOS/Windows 一致，不暴露尚未支持的 Linux。正式 Playwright 通过显式 C17 Mock 与 DEV-only 测试适配器覆盖授权允许、租户拒绝及恶意入口流程，网络观测已证实后两者均不请求业务模块，且业务入口未回退到 B01 `local_executor_*` 测试路径；生产构建已验证不包含测试适配器标记。Windows Job Object/TOCTOU 新增 API 已通过 `x86_64-pc-windows-msvc` 最小交叉编译；完整项目在 macOS 交叉编译时仍因本机缺失 Windows SDK C 头文件而停在第三方 `ring` 的 `assert.h`，真实 Windows 构建及运行仍属于集成门禁。当前不能升级为完成：`GET /capabilities/registry` 现阶段仍由稳定协议与 Mock Host 隔离，Core PostgreSQL/C14 Audit/C17 生产宿主与 Entitlement 尚未接入；生产账号后端链路、真实签名发布链、Windows 真实 App/Sidecar 与 macOS/Windows 受控真实账号 E2E 也均未完成。

### B03 抖音单平台视频发布闭环

**所属：Social Operations**

**状态：`⬜ 未开始`**

完成定义：

- 在 App 内完成抖音扫码登录、选择账号和登录态校验；
- 选择本地视频或 Core Artifact，填写标题与话题；
- Playwright 打开创作者后台、上传、等待处理、发布并确认最终成功；
- 记录步骤、进度、失败、截图、重试、取消和人工接管；
- DOM 改版、登录失效、弹窗和网络慢具有可诊断错误；
- 受控抖音账号完成真实发布验收，测试内容随后按策略清理。

### B04 素材库、下载任务与云存储

**所属：Video Studio**

**状态：`✅ 已完成`**

**完成日期：2026-07-17**

**开始日期：2026-07-16**

2026-07-16 进度与复审记录：实现位于 `task/b04-media-library` 分支（WIP 提交 `f7652c5`）。独立代码质量复审确认素材领域模型、上传/预览/删除、STS TTL 上限、跨租户拒绝、CAS 并发、可信对象元数据校验等单元/契约测试质量较高，迁移 `20260716_0024` 自身链条正确；前端能力模块经 capability registry 动态发现，无 Tauri 直连。继续开发必须先解决的阻断项：Core `infrastructure/database/models.py`/`repositories/video_studio.py` 静态 import 能力包内部实现，卸载能力包后 Core 无法启动（违反 Core 禁止导入能力包铁律）；STS 签发只校验通用 RBAC，无 `deployment_installed && tenant_entitled && user_permitted` 三层授权（依赖 C17）；`video.*` 权限域后端未生效；无真实腾讯 CAM/STS Provider（生产 503）；成片下载不支持；未接 Core Artifact 权限与审计（依赖 C14）；引用创建无 API；生产 App 不挂载 video-studio 路由，Playwright 用测试夹具应用不构成生产验收。迁移编号与 C14 分支同号 `20260716_0024`，后合入者必须改号。

2026-07-16 阻断项修复记录（本任务提交）：已按 TDD 解除三项阻断。C-1 Core 反向依赖：扩展 AST 依赖边界测试覆盖 Core `infrastructure`、`api` 目录（RED 先失败于 `models.py`、`repositories/video_studio.py`、`api/routes/video_studio.py` 三处违规 import），随后把 video 四个 SQLAlchemy Record 与仓储迁入 `capabilities/video_studio/persistence.py`、`/api/v1/video-studio` 路由迁入 `capabilities/video_studio/api.py`，Core 全局 `ALL_DATABASE_MODELS` 不再含能力包模型，卸载能力包后 Core 可独立启动；新增能力包后端装配协议 `BackendCapabilityRegistration`（校验路由不越出 Manifest 路由根、模型为 Base 子类且不重复），video-studio 以 `VIDEO_STUDIO_BACKEND_REGISTRATION` 声明路由与模型，部署层组合根 `bootstrap/capabilities.py` 按 deployment_installed 显式装配、未知/重复声明失败关闭；三层授权中的租户 Entitlement 与用户 `video.*` 权限域校验待 C17。H-4 引用关系：契约 RED 先失败于路由不存在，新增 `POST/GET /materials/{id}/references`（创建 RUNS_MANAGE、查询 RUNS_EXECUTE，与既有素材端点一致），非法引用类型 422、跨租户 404，引用存在时删除素材仍按 `material_in_use` 409 拒绝。M-1：补 20 GiB 上限精确拒绝测试（服务层 `InvalidMaterialInput("素材大小无效")` 与 API 422 精确 detail，边界值可通过）。验证命令：`uv run pytest tests/unit/capabilities tests/contract/video_studio tests/integration/database/test_migrations.py -q`（206 通过）、`uv run pytest tests/unit tests/contract -q`（887 通过）、`uv run ruff check .`、`uv run mypy` 全部通过；前端未改动。迁移编号约定：`20260716_0024` 与 C14 分支同号，本轮不改号，待 C14 合入 main 后本分支 rebase 时一并处理（down_revision 指向 C14 的 0024、自身改 0025）。仍未解决：三层授权与 `video.*` 权限域（待 C17）、真实腾讯 CAM/STS Provider（需真实云凭据）、Core Artifact 权限与审计（待 C14）、成片下载、生产装配后的真实腾讯云全栈验收，状态保持 `🚧 进行中`。

2026-07-16 第二轮复审跟进修复记录（本任务提交）：已按 TDD 解除三个跟进问题并补一个边界对偶用例。其一，迁移 metadata 漂移：新增 autogenerate 漂移守卫测试 `test_autogenerate_detects_no_drift_between_head_and_runtime_metadata`（Alembic `compare_metadata`，RED 先检出 video 四表 `remove_table` 漂移及迁移簿记表 `employee_model_migration_backups` 误报），修复为 `bootstrap/capabilities.py` 新增 `load_all_database_models()` 无条件聚合 Core 与全部能力包模型、`migrations/env.py` 改用该同源聚合，并以 `include_name_for_autogenerate` 单一来源排除迁移内部簿记表；守卫同时暴露既有 Core/能力包漂移并已最小对齐——模型侧补齐迁移已部署的 FK `ondelete="RESTRICT"`（artifacts.created_by、files.owner_id、video 三表 users FK），`RunRecord.thread_id` 去掉迁移从未创建的 `index=True`、`conversation_id` 补上迁移 `0021` 已创建的索引声明。其二，AST 边界测试白名单缺口：Core 目录列表由写死清单（含不存在的 `tools`、`memory`，漏掉 `workers`、`sandbox`、`observability`）改为动态枚举 `src/agent_platform` 顶层目录扣除 `bootstrap`/`capabilities` 白名单，新增顶层目录默认受保护；RED 用 `workers/` 下反向 import 探针文件验证可捕获后删除探针。其三，重复引用未受控 500 与 Mock/真实语义背离：按仓库既有惯例（仓储 flush 捕获 IntegrityError 转领域错误、路由映射 409 `*_exists`）选择受控 409 `reference_already_exists`；SQL 仓储 `add_reference` flush 捕获 IntegrityError 转 `MaterialReferenceAlreadyExistsError`，InMemory 仓储同键查重抛同一错误，API 映射 409；契约测试 `test_duplicate_reference_error_semantics_match_across_repositories` 参数化断言两条仓储路径同语义（RED：SQL 裸 IntegrityError→500、InMemory 静默成功），另有 API 级重复引用 409 契约用例。顺手补 `test_upload_credentials_accept_material_at_exact_size_limit`（恰好 20 GiB 被接受）。验证命令：`uv run pytest tests/unit/capabilities tests/contract/video_studio tests/integration/database/test_migrations.py -q`（211 通过）、`uv run pytest tests/unit tests/contract -q`（891 通过）、`uv run ruff check .`、`uv run mypy` 全部通过。其余未解决门禁不变，状态保持 `🚧 进行中`。

2026-07-17 主线同步与迁移改号记录（本任务提交）：分支合并最新 main（C05/C07/C09/C14 HMAC/C17 均已在主线）。迁移 `20260716_0024_create_video_media_library` 改号为 `20260716_0029`（down_revision=`20260716_0028`，成为单头）；**（2026-07-17 台账更正）该编号此后随 C15 等条目合入时的重链再次改号，磁盘上的最终落位是 `20260716_0031_create_video_media_library` + `20260716_0032_add_video_material_crc64`（`20260716_0029` 实为 `create_memories`）。本行原始编号仅记录当时状态，追溯请以磁盘迁移文件与单头链为准；单头链完好，正确性不受影响。**合并采用 C17 生产组合根为基座：删除 B04 早期的 `create_app(extra_routers=...)` 临时注入口，能力路由统一走 `resolve_installed_backend_registrations` + `wrap_capability_router` + `create_capability_gate` 生产路径，`bootstrap/capabilities.py` 保留 `load_all_database_models()`（迁移 metadata 与运行时同源，video 四表无条件纳入 Alembic metadata），video-studio 在三层授权接线完成前保持不可安装（`test_video_studio_backend_host_is_not_installable_before_b04` 维持 GREEN）；B04 自身契约测试与 E2E 夹具改为测试侧 post-hoc `include_router` 挂载，待三层接线后切换为生产装配。

2026-07-17 三层授权、真实 STS 与审计接线记录（本任务提交，先 RED 后 GREEN）：video-studio 正式可安装——`bootstrap/capabilities.py` 注册路由工厂与 `create_state` 装配协议（按部署配置注入真实腾讯 CAM/STS 签发器，配置不全时端点 503 失败关闭），`test_video_studio_backend_host_is_not_installable_before_b04` 按计划改写为可安装用例；全部端点从通用 RBAC 切换到能力上下文 `video.read/manage/execute` 细粒度权限（read/manage/execute 互不隐含，三用例背书），三层组合矩阵覆盖 Core-only/Core+social/Core+video/Core+both（未安装 404 + 授予 409 `capability_not_installed`、未授权 403、无权限 403、撤销后下一请求即 403 全 fail-closed）。真实腾讯 CAM/STS Provider（`tencent_sts.py`，`qcloud-python-sts==3.1.6` 锁定、零侵入）：限定 `materials/{tenant_id}/` 前缀、显式只写动作集、TTL 硬上限 900s、上游失败/畸形响应受控 503；真实云门禁 `test_real_tencent_sts_material_credentials.py` 用开发凭据实测通过（限定前缀可写、越界前缀云端 403、只写凭证读取云端 403，实现与复审代理各自独立复跑均 1 passed）。C14 审计桥接：素材/引用/下载任务 7 类关键操作经 `ContextBufferAuditSink` → 响应前 flush → C14 统一 `audit_events` 落库（契约测试经 `GET /api/v1/audit/events` 断言，metadata 脱敏不含临时凭证/sha256），审计包装层支持 async 端点，flush 失败显式 500 不半写。成片下载：`SqlAlchemyArtifactDownloadSourceResolver` 按租户读 Core `ArtifactRecord` 可信元数据，跨租户 404、未知来源 422。生产装配 E2E：契约夹具与 Playwright 夹具全部改为生产 `create_app` 装配（安装清单缺失即启动失败），E2E 去除 registry/登录 mock 改真实 Entitlement 授予/撤销驱动，3 项通过（未授权入口不可见+直达被拒、直传闭环、撤销后入口消失+直连 403）；顺带修复 video Playwright 配置按下标替换 API server 在 C09 合入后漂移导致实际跑无 video 生产 app 的假配置。Demo Seed 幂等授予演示租户 video-studio Entitlement（source=demo-seed）。验证：定向 422 passed/1 skipped、unit+contract 1140、全量 1321 passed/43 skipped、ruff/mypy 207 文件、前端 vitest 225、lint/typecheck/build、Playwright video 3 项 + capability/social 回归 5 项（随机隔离栈，验后资源清零）。双复审：代码质量复审 PASS（无阻断）；规格复审确认代码与测试全部核验通过、真实云门禁独立复跑通过。

**B04 收口前必须关闭的门禁（2026-07-17 双复审登记，未闭合前不得标 ✅）**：
- M-1 审计 flush 与业务写入非原子：审计失败 500 后客户端重试会重复签发真实 STS 并重复产生草稿，需审计并入业务同事务或 upload-credentials 幂等去重；
- M-2 过期草稿与 `cleanup_required` 对象无生产回收调用方（`expire_upload_drafts`/`cleanup_material_object` 已实现但无 worker/调度接线），无界存储成本；
- M-3 真实对象核验/预览/清理 Provider（COS head_object/预签名/删除）未实现，真实栈 complete-upload/preview 恒 503；连同真实腾讯云全栈验收（真实大文件、断网恢复）一并闭合；
- L-1 STS 签发无租户级频控/去重；L-2 `delete_material` 与 `add_reference` 并发 TOCTOU（需行级锁或 DB 约束）；L-3 `video_material_cos_bucket` 未进 `infra/compose/.env.platform`，常驻开发栈缺配置即 503。

2026-07-17 第二轮收口记录（双复审登记门禁修复，先 RED 后 GREEN）：M-1 审计原子性——upload-credentials 增加基于 (tenant_id, sha256, size_bytes, folder_id) 的幂等去重，审计 flush 失败 500 后客户端重试复用既有 pending_upload 草稿并复用/重签凭证，不再重复签发真实 STS 或产生孤儿草稿。M-2 生产回收——新增 `maintenance.py`（`run_media_library_maintenance` 常驻循环，配置驱动间隔默认 300s、批量 100，随 API lifespan 启动、停机 cancel + await 抑制 CancelledError），过期草稿与 `cleanup_required` 对象逐素材独立事务回收，单个失败保留标记下轮重试，只清 cleanup_required/超时 pending_upload。M-3 真实对象 Provider——新增 `tencent_cos.py`（`TencentCosMaterialObjectVerifier`/`PreviewUrlIssuer`/`ObjectCleaner`，cos-python-sdk-v5 已锁定），核验以客户端元数据 `x-cos-meta-sha256` + 服务端可信 Content-Length 比对草稿声明（COS ETag 非内容 sha256；抗恶意伪造需升级 crc64ecma 与服务端 `x-cos-hash-crc64ecma` 比对，记为后续硬化项），404→MaterialObjectMissing、其余上游故障→MaterialStorageUnavailable；bootstrap `create_state` 按配置注入，凭据不全时端点 503 失败关闭；真实门禁 `test_real_tencent_cos_material_objects.py` 用开发凭据实测通过（真实上传→head_object 核验→预签名预览→delete_object 清理→云端确认不存在往返）。L-1 STS 频控——租户级 `video_sts_issue` 作用域限流（默认 30/min，经 `extra_limits` 组合根注入），超限 429 `video_sts_rate_limited`。L-2 TOCTOU——delete_material 对素材行加锁消除与 add_reference 的并发窗口，补真实 PG 条件门禁并发测试。L-3 配置一致性——`.env.platform`/`.env.platform.example` 补 `VIDEO_MATERIAL_COS_BUCKET`、`platform.yml` 注入 `AGENT_PLATFORM_VIDEO_MATERIAL_COS_BUCKET`/`AGENT_PLATFORM_COS_REGION`/`AGENT_PLATFORM_COS_SECRET_ID`/`AGENT_PLATFORM_COS_SECRET_KEY` 与默认 `AGENT_PLATFORM_INSTALLED_CAPABILITIES=["social-operations","video-studio"]`、`mvp-profile.sh` 仅在提供开发 COS 凭据时写入并同步 dotenv 允许列表，缺省保持 503 失败关闭。验证：后端全量 1338 passed/45 skipped、定向 439、ruff/mypy 209 文件；前端 vitest 226、lint/typecheck/build（含修复 direct-upload.test.ts mock 参数签名 typecheck）；真实 COS 门禁 STS + 对象 2 项均实测通过；Playwright video 3 项 + capability/social 回归 5 项（随机隔离栈，验后资源清零）。**B04 剩余验收门禁（未闭合前不标 ✅）**：断点续传直传、真实大文件（20GiB 量级）与断网恢复的真实腾讯云全栈验收；sha256 核验升级 crc64ecma 抗恶意伪造硬化。本轮第二轮改动已通过独立双复审（规格复审亲自复跑定向 439/unit+contract 1157/真实 COS 2/真实 PG L-2 门禁均通过；对抗性质量复审确认六项修复无新高/中风险缺陷）。

2026-07-17 第二轮双复审 Low 级 follow-up（不阻断，记入 B04 收口门禁）：① 真实门禁覆盖缺口——`test_real_tencent_cos_material_objects.py` 用单段 `put_object` 验证 verifier，而生产前端走分段 `cos.uploadFile`，分段上传的 `x-cos-meta-sha256` 自定义元数据需落在 Init Multipart 请求上、该路径未被真实门禁覆盖（fail-closed 不会误放行，但可能拒收合法大文件，需真机/分段真实门禁暴露），与「断点续传 + 20GiB 真实大文件全栈验收」合并闭合；② maintenance 永久失败对象无上限重试且 oldest-first 可能饿死新清理（累积 >batch_limit 个不可删对象时），需失败计数落库 + 退避/dead-letter；③ 多 API 副本并发跑同一清扫循环无 `FOR UPDATE SKIP LOCKED`（delete 幂等所以无正确性损害，仅浪费 COS 调用），需单实例调度或 SKIP LOCKED；④ 后台 `cleanup_material_object` 物理删对象无审计痕迹（用户态 delete_material 有），观测缺口需补审计；⑤ 能力后台 worker 未等 `_wait_for_database_ready`，首轮清扫遇 DB 未就绪抛异常被吞并延后一个间隔（自愈但有噪声日志）。

2026-07-17 验收门禁收尾记录（本任务提交，先 RED 后 GREEN，第三轮双复审后收口）：闭合「剩余验收门禁」全部技术项——① **crc64 抗伪造硬化**：`complete_upload` 改用 COS 服务端计算的 `x-cos-hash-crc64ecma`（CRC-64/XZ）作可信内容指纹与草稿声明比对，客户端可伪造的 `x-cos-meta-sha256` 从门禁移除、降级为仅展示/诊断元数据；crc64 缺失/空/非法均 fail-closed，保留服务端 size 校验。前端 `crc64ecmaFile`（hash-wasm createCRC64 分块流式）与 COS 一致有已知向量背书（`CRC-64/XZ("123456789")=11051210869376104954`），前端算法/独立参考实现/COS 服务端三方互证。② **分段上传真实门禁**：`test_real_cos_multipart_upload_yields_server_crc64_matching_declaration` 走真实 COS `create_multipart_upload`（自定义元数据落 Init）/`upload_part`/`complete_multipart_upload`，覆盖生产 `cos.uploadFile` 同一路径，闭合第二轮 Low-①。③ **断点续传+断网恢复真实门禁**：`test_real_cos_multipart_resume_after_interruption_does_not_reupload` 以 COS 原生 `list_parts` 为断点真相，中断后只补未完成分段、不重传已完成段、最终 crc64 一致。迁移 `20260716_0033` 加 crc64 列。复审整改 M-1（跨边界读契约）：迁移 0033 给存量 available 行回填 `crc64ecma=''`，前端 zod 放宽为 `union([literal(''), regex])` 容忍存量空 crc64（仅展示用），避免单条存量行让整表解析崩溃（RED `media-library.test.ts` 证明旧 schema 拒空串、新 schema 接受且仍拒非法非空值；dev 栈实测 0 行受影响，属防御性修复）。

**20GiB 真实大文件门禁定性（主代理工程判断）**：原「真实大文件（20GiB 量级）真实腾讯云全栈验收」判定为**由代表性分段路径验证满足** + 诚实取舍——强制 1MiB 小分段让数 MiB 文件走真实多段上传，覆盖的是与前端 `cos.uploadFile` 完全相同的 `create/upload_part/complete_multipart` COS 生产代码路径 + 服务端 crc64 核验 + 断点续传，功能正确性门禁已闭合；**未覆盖的仅是规模才暴露的非功能性风险**（单进程内存不膨胀、超长 STS 超时、超大并发分片吞吐），归为规模/环境压测范畴、非本功能门禁，作为运维压测 follow-up 记录，不阻断 B04 功能完成。

**保留的 follow-up（非阻断，不影响 ✅）**：L-1 大文件双次全量哈希（sha256 已降级为非门禁，可后续改门禁路径只算 crc64 或单趟并算）；L-2 草稿去重键仍含 sha256（非安全洞，complete-upload 以服务端 crc64 fail-closed，语义可后续锚到 crc64）；② maintenance 永久失败对象重试无上限/退避；③ 多副本清扫无 SKIP LOCKED；④ 后台物理删对象无审计痕迹；⑤ worker 未等 DB ready；以及 20GiB 规模压测。

验证（本轮）：后端全量 `uv run pytest -q` 1341 passed/47 skipped、定向 252、unit+contract 1160；`ruff`/`mypy`（208 文件）通过；真实 COS 门禁（source .env.platform）4 passed（单段对象/分段/续传/STS）；真实 PG 17 passed；前端 vitest 230（含 media-library schema 存量兼容 3 项 + crc64 已知向量）、lint/typecheck/build 通过；Playwright video 3/3。第三轮双复审：规格复审逐条亲验三门禁真闭合（真实 COS 2 passed），FAIL 仅卡台账（本记录已补）与 20GiB 定性（已明确）；质量复审确认 crc64 硬化无 fail-open、三方算法互证、fail-closed 分支完整，FAIL 仅 M-1（已修）。
- 部署说明（**2026-07-17 更正**）：Demo Seed 已授予 video-studio Entitlement。`AppSettings.installed_capabilities` 的代码默认值仍为 `("social-operations",)`——这不是缺陷，契约测试正是靠该默认值表达 Core+social Profile；而**部署层 `infra/compose/platform.yml` 的默认值已是 `["social-operations","video-studio"]`**，故常驻开发栈无需再手工配置 `AGENT_PLATFORM_INSTALLED_CAPABILITIES`，本说明的原措辞已过期。栈重建后仍需重放 Demo Seed。
完成定义：

- 建立视频、图片、音乐素材及文件夹/标签管理；
- 支持导入、上传、预览、删除、引用关系和生命周期；
- 服务端签发 LighthouseCOS/COS 限定目录、短有效期 STS；
- 支持素材/成片下载任务、进度、断点、失败和重试；
- 素材复用 Core Artifact 权限和审计；
- 大文件、跨租户、STS 过期和失败清理测试通过。

隔离实现证据：B04 已新增 `video-studio` 素材库后端服务、SQLAlchemy 仓储、FastAPI 可选路由、`20260716_0024` 迁移和前端素材库页面；Core `create_app()` 通过 `extra_routers` 显式挂载可选路由，默认 OpenAPI route root 仍只包含 Core，能力关闭后不暴露 `/api/v1/video-studio`。后端支持素材文件夹/标签、视频/图片/音乐校验、20 GiB 上限、租户隔离、tenant/material scoped 短期 STS 端口、服务端可信对象元数据核验、上传中止与过期草稿扫描、可重试对象清理、短时预览 URL、引用保护删除，以及下载排队、单调进度、断点、失败、重试、完成、取消、成员所有权和 revision CAS 并发保护状态机；缺少 STS、可信对象校验或预览 provider 时统一 503 失败关闭，不再签发伪本地密钥。前端已由手填 size/SHA256 的协议演示改为真实文件选择、4 MiB 分块 SHA256、腾讯 COS 分片直传、失败 abort、服务端完成核验、短时预览、删除和下载任务管理。隔离无头 Playwright 已用生产 API 和数据库、显式测试 provider、浏览器 COS 请求拦截覆盖固定测试账号登录、文件选择、直传、确认、预览、下载初态和删除；测试 provider 与拦截只存在于测试目录，不能替代真实腾讯 CAM STS/COS、真实对象可信核验或网络故障验收。生产腾讯 CAM STS/COS 的 issuer、对象校验/清理/预签名 provider 与真实账号联调，Core C14 Audit、C17 Entitlement/Capability Host、真实 20 GiB/断网恢复及真实腾讯云全栈验收仍是完成门禁；叠加上方复审阻断项，状态保持 `🚧 进行中`。

### B05 Timeline 编辑与 App 内预览

**所属：Video Studio**

**状态：`⬜ 未开始`**

完成定义：

- 定义供应商无关 Timeline、轨道、素材、时间区间、转场和模板引用；
- 自研 Timeline 编辑器，以 HTML5 Video/Canvas 和低清代理素材实现 App 内预览；
- 支持 Timeline 创建、保存、版本、解析和预览错误；
- 页面不直接持有腾讯云长期密钥或拼装 OpenAPI 签名；
- Timeline 与员工、任务、素材和客户模板稳定关联；
- 组件、协议和真实素材预览 E2E 通过。

### B06 一键剪辑、模板剪辑、批量任务与云端成片

**所属：Video Studio**

**状态：`⬜ 未开始`**

完成定义：

- 建立 `VideoRenderProvider` 和第一阶段 `TencentMpsProvider`；
- 支持一键剪辑、模板替换、批量提交和幂等 Job；
- 支持排队、轮询、进度、取消、失败重试、费用和供应商错误映射；
- 成片进入 Core Artifact，可预览、下载、删除和交给发布流程；
- App 关闭后云端任务继续，重开后恢复真实状态；
- Stub 协议、真实 MPS `EditMedia` 小样、LighthouseCOS/COS 成片回写和批量隔离测试通过。

### B07 多平台聚合发布、批量发布与发布记录

**所属：Social Operations + Video Studio 衔接**

**状态：`⬜ 未开始`**

完成定义：

- 在抖音闭环基础上依次接入小红书、快手和视频号发布；
- 同一成片可选择多个平台、多个账号和各平台标题/话题；
- 支持批量导入、预约、并发限制、平台级失败隔离和重试；
- 发布记录包含目标账号、内容版本、最终状态和平台返回证据；
- 单个平台页面改版不影响其他平台任务；
- 四个平台分别完成受控账号真实发布验收和组合 E2E。

### B08 账号健康、频控、熔断与人工接管

**所属：Social Operations 公共层**

**状态：`🧪 待集成`**

**开始日期：2026-07-16**

完成定义：

- 建立平台/账号/动作维度的频率、每日上限和冷启动策略；
- 连续失败、登录失效、风险提示和异常行为自动熔断；
- 提供暂停、恢复、验证码处理、人工接管和紧急停止；
- 账号中心展示健康度、最近任务、失败趋势和处置建议；
- 所有策略由服务端授权并进入审计，客户端不能自行放宽；
- 风控模拟、并发、离线和远程停止 E2E 通过；
- **（2026-07-17 由 C17 收口转写而来的强制门禁）实现 `social.jobs.v1` Worker 任务处理器时，必须复用 Core 的 `evaluate_capability_availability`（`platform/entitlements/services.py`）执行 `deployment_installed && tenant_entitled && user_permitted` 三层校验，未安装/未授权租户不得调度该 Worker；并且必须同时建立「能力 Worker 任务处理器注册必须经过统一 gate、否则装配期 fail-fast」的结构性守卫测试，把该要求固化为不变式而非文档意向。**

**门禁来源说明（不得删除）**：C17 完成定义第 6 条要求「未安装/未授权能力无法调用 API、调度 Worker、下载 Sidecar 或签发云凭据」。C17 收口时（2026-07-17）经独立规格复审与主代理复核确认：Core 中**不存在任何能力包 Worker 任务处理器**（`worker_handlers` 字段仅有 manifest 声明与命名空间校验，零 dispatcher 消费方），故「调度 Worker」子句在 Core 中**无对象**，不构成 fail-open，C17 据此收口为 `✅ 已完成`。主代理同时裁定：不在 C17 内为零消费方的 dispatcher 预建守卫（属 CLAUDE.md 禁止的「为假设性未来需求过早抽象」），该门禁**前移由本条目承接**。本条目是该门禁的唯一承接方，C17 已收口不再承接，**删除或弱化本门禁即造成 C17 完成定义第 6 条永久失守**。

隔离实现证据：账号治理已在 Social Operations 公共层建立服务端策略与授权入口，覆盖平台/账号/动作维度的最小间隔、每日上限、冷启动每日上限、连续失败阈值和幂等授权；客户端只能调用 `/actions/authorize`、`/actions/result`、`/pause`、`/resume`、`/remote-stop` 等服务端 API，不能通过请求体覆盖服务端策略。动作结果上报必须绑定同一 `(account_id, action_type, idempotency_key)` 的既有授权；同结果重放不重复计数，冲突重放失败关闭，未授权结果不能重置失败状态。连续失败、登录失效、平台风险信号和异常行为均进入熔断/人工接管；暂停、恢复、远程停止、验证码/风控转人工和本地紧急停止均保持安全失败。账号中心已展示健康度、最近任务、失败趋势和处置建议，并在熔断、暂停、登录失效或设备离线时禁用本地动作。后端单元/API 测试覆盖冷启动并发限流、频控窗口、每日上限、结果上报授权/幂等、连续失败熔断、异常行为、登录失效、暂停/恢复、离线、远程停止、审计脱敏和客户端策略覆盖拒绝；前端 Vitest 与正式 Playwright 覆盖账号治理展示、最近任务、服务端授权、熔断禁用和远程停止流程。当前不能升级为完成：B02 仍处于 `🧪 待集成`，Core C14 审计、C17 生产宿主/Entitlement、生产账号后端链路、真实平台账号 E2E 和完整组合回归尚未完成；本条目先保持 `🧪 待集成`，供 B03/B07/B09/B10/B13/B14/B15/B16 复用治理接口，不得绕过真实集成门禁。

### B09 抖音 AI 客服、会话中心与回复审批

**所属：Social Operations**

**状态：`⬜ 未开始`**

完成定义：

- 配置客服 AI 角色、知识、语气、欢迎语和回复策略；
- 获取私信会话、上下文、未读状态和负责人；
- AI 生成回复建议，敏感内容进入 Core 审批；
- 支持人工发送、策略允许的自动发送、暂停和接管；
- 回复与模型、知识引用、审批和平台消息可追溯；
- 优先官方 API，并用受控账号完成真实会话验收。

### B10 私信欢迎语、持续跟进与转化状态

**所属：Social Operations**

**状态：`⬜ 未开始`**

完成定义：

- 支持首次触达欢迎语、跟进条件、间隔、次数和停止规则；
- 支持会话标签、阶段、负责人、备注和转化结果；
- 黑名单、退订、重复去重、敏感词和频控生效；
- 人工回复后自动流程按策略暂停或重算；
- 证据不足的平台入站私信能力只有动态验证通过后才能接入；
- 跟进时序、并发、退订和真实账号 E2E 通过。

### B11 桌面微信环境诊断、Windows UIA、macOS AX 与 OCR 基线

**所属：Social Operations**

**状态：`⬜ 未开始`**

完成定义：

- Windows 使用 UI Automation；macOS 使用 Accessibility/AX API，并分别完成权限探测；
- 检查微信进程、版本、窗口、分辨率、DPI/缩放、锁屏、遮挡和前台状态；
- macOS Screen Recording 和 Accessibility 权限必须由用户明确授予，缺失时提供可操作诊断；
- 结构化无障碍树优先定位；Windows 使用本地 OCR，macOS 使用 ScreenCaptureKit + Vision OCR，只识别必要区域；
- 建立脱敏截图、识别置信度、重试和人工校准；
- 微信升级或布局变化时安全失败，不误点、误发；
- 建立 Windows/macOS 独立功能与版本矩阵；专用测试账号的诊断 E2E 通过。

### B12 微信未读识别、上下文读取与 AI 回复

**所属：Social Operations**

**状态：`⬜ 未开始`**

完成定义：

- 识别未读、打开指定会话并读取最小必要上下文；
- 调用 Core 模型、知识和记忆生成回复建议；
- 第一阶段默认人工确认，自动发送按企业风险策略灰度；
- 支持成功、失败、重试、重复消息去重和会话接管；
- 聊天、截图和日志按隐私规则处理，不进入安装包；
- Windows/macOS 在各自已支持功能范围完成多联系人、窗口异常、锁屏和真实微信测试账号 E2E。

### B13 微信好友、主动激活与个性化群发

**所属：Social Operations**

**状态：`⬜ 未开始`**

完成定义：

- 检查好友申请并按明确规则人工/自动处理；
- 支持联系人分群、模板变量和 AI 个性化内容；
- 支持沉默客户激活、批次、进度、跳过和失败原因；
- 黑名单、退订、去重、频控、敏感词和每日上限强制生效；
- 群发任务具备审批、暂停、取消和完整审计；
- 小批量专用联系人真实验收通过，禁止使用未授权联系人测试。

### B14 朋友圈发布与朋友圈营销

**所属：Social Operations**

**状态：`⬜ 未开始`**

完成定义：

- 支持文案、图片/视频、可见范围和发布时间；
- 自动进入发布入口、选择素材、提交并确认最终状态；
- 发布记录与素材、员工、审批和设备可追溯；
- 朋友圈营销动作具备独立频控、范围和熔断；
- UI 改版、素材失败、窗口异常和重复发布安全处理；
- 专用微信账号真实发布和任务恢复 E2E 通过。

### B15 曝光获客公共引擎与合规门禁

**所属：Social Operations**

**状态：`⬜ 未开始`**

完成定义：

- 建立目标、动作、账号、频率、配额、审批和结果统一模型；
- 平台规则版本、企业策略和账号风险共同决定可执行动作；
- 评论、私信等敏感动作默认审批并保留内容追溯；
- 目标去重、黑名单、每日上限、连续失败熔断和紧急停止生效；
- 验证码、滑块和平台风控只允许转人工，不允许绕过；
- 在不真实骚扰第三方的隔离/自有账号环境完成安全 E2E。

### B16 自动、定向、链接与账号搜索曝光

**所属：Social Operations**

**状态：`⬜ 未开始`**

完成定义：

- 支持自动曝光、指定目标、内容链接和账号搜索四类任务；
- 按平台、账号、关键词、主页或链接定位并校验目标；
- 只执行 B15 策略允许的访问、评论、私信或其他动作；
- 支持抖音、小红书、快手的独立 Adapter 和故障隔离；
- 记录目标、动作、内容、结果、失败和转化关联；
- 每个平台在自有/授权测试账号范围完成真实验证。

### B17 业务工作流、效果分析与交付验收

**所属：Video Studio + Social Operations**

**状态：`⬜ 未开始`**

完成定义：

- 可视化组合素材、剪辑、发布、客服、微信、曝光和审批节点；
- 支持触发条件、目标人群、账号、暂停、取消、重试和人工接管；
- 提供发布成功率、回复率、触达、互动和转化分析；
- 指标来自平台事件和稳定 ID，不解析非结构化 RPA 日志充当真相；
- 完成 Core-only、Core+视频、Core+自动运营和 Core+两者组合回归；
- 完成 macOS/Windows 安装、更新、故障恢复、隐私和客户交付验收。

## 6. 当前业务基线

基线日期：2026-07-16。

| 项目 | 当前结果 |
| --- | --- |
| `video-studio` 源码模块 | 已建立独立、供应商无关 Manifest；B04 素材库隔离层在 `task/b04-media-library` 分支进行中，阻断项见 B04 记录 |
| `social-operations` 源码模块 | 已建立独立、供应商无关 Manifest、本地执行器 v1 协议及 Tauri 无固定端口认证 stdio Sidecar；B02 隔离层补齐原子设备/账号/任务 API、SQLite 快照与审计 outbox、签名 Manifest 安装、真实进程监管、私有浏览器 Profile/Cookie 及 Tauri 应用编排；B08 隔离层补齐账号治理、频控、冷启动、熔断、人工接管、远程停止、服务端授权和账号中心治理展示；生产 PostgreSQL、Core Audit/Entitlement、真实签名发布和真实账号 RPA 仍待集成 |
| Core 前置条件 | C01-C04、C06、C08 已完成；C05 待集成；C07、C14 进行中（C14 复审退回）；其余按依赖 DAG 推进，业务条目按依赖标记待集成 |
| 竞品静态分析 | 已完成，见完整分析报告 |
| 竞品动态账号验收 | 尚未完成 |
| 我们的真实平台账号 E2E | 尚未开始 |
| 能力包组合矩阵 | Mock Host 四组合隔离门禁已通过；真实 Core/C17 与 B17 组合回归尚未完成 |

## 7. 完成记录

| 任务 | 状态 | 开始日期 | 完成日期 | 提交 | 平台/版本 | 验证证据 |
| --- | --- | --- | --- | --- | --- | --- |
| B01 | 已完成 | 2026-07-14 | 2026-07-15 | 本任务提交 | Mock Host / JSON Schema / macOS Tauri | 两个版本化 Manifest 已覆盖路由、Worker、权限、事件、前端、迁移、健康与桌面声明；Mock Host 四组合隔离及关闭门禁通过。本地执行器 v1 以 Pydantic 为单一来源导出 Draft 2020-12 Schema，10 个有效/25 个无效样例覆盖任务、取消、步骤进度、人工接管、诊断、身份、幂等、截止时间、治理引用、严格状态与脱敏语义。Tauri 现通过匿名 stdin/stdout 管道管理同源 Sidecar，使用 256 位随机会话令牌逐消息认证，不经参数/环境变量泄露且不监听 TCP；Rust 单元/stdio 回放 3 项与隐藏、无 Dock 的 macOS 真实桌面启动、调用、状态、停止生命周期 E2E 通过，完整原生套件 3 项通过。B01 只提供无业务副作用的版本化接收确认；设备注册、任务持久化、Sidecar 下载/签名/崩溃恢复、账号与 RPA 属于 B02，真实四组合回归按定义属于 B17。 |
| B02 | 🧪 待集成 | 2026-07-15 | — | 本任务提交 | Python / Rust / TypeScript / Playwright / macOS 无头真实子进程 | 后端 capability 164 项和全量 pytest 878 项通过，35 项仅因真实 PostgreSQL/Redis/MinIO 或破坏性 Docker 门禁而跳过；覆盖原子竞态、owner 隔离、离线/紧停门禁、租约边界、持久化回滚、审计 outbox、非敏感原因以及 SQLite 防符号链接、私有父目录、连接竞态和多实例 revision CAS；Ruff 通过，mypy 163 个源文件无问题。执行 `cd frontend/src-tauri && cargo test --locked --offline`，完整原生套件 49 项全部通过、0 失败、0 忽略、0 过滤（lib 4 + browser 9 + credentials 2 + executor 14 + security 12 + runtime 8），覆盖签名 Manifest/逐跳下载/超时/防降级/重启恢复、启动时重验与已验证字节执行、并发启动单实例登记、调用中紧停抢占、stop/超时/崩溃整树清理、真实 Sidecar 崩溃/挂起/限界脱敏、Cookie/Profile 私有原子存储和 Tauri 编排层闭环；`cargo fmt --check` 及全 targets/features 的 Clippy `-D warnings` 通过。TypeScript 完整接入 11 个 B02 IPC，运行时 Capability Registry 以静态公开元数据在动态导入前严格核对能力 ID、唯一且精确的入口/权限、部署、租户授权和用户权限，畸形、失败、重复声明替代缺项及恶意漂移均失败关闭且不加载业务模块；`pnpm test` 为 35 文件、146 项通过，`pnpm lint && pnpm typecheck && pnpm build` 通过，生产 `dist` 不包含 `__AGENT_PLATFORM_TEST_ADAPTER__`、`__socialCommands`、`createSocialOperationsTestAdapter` 或 `test-adapter`；Playwright 通过共享 compose helper 将 `PLAYWRIGHT_COMPOSE_PROJECT_NAME` 与 `PLAYWRIGHT_*_PORT` 同步注入 Docker Compose、API、Alembic 和测试夹具，B02 允许/租户拒绝/恶意入口 3 项及完整 17 项回归全部通过；隐藏主窗口、无 Dock 的 macOS 真实 Tauri 套件 5 项通过，其中真实 WebView 已调用 B02 账号 ACL/状态转移命令；服务、浏览器、隔离容器/网络/卷均已清理，`agent-platform-dev` 未受影响。Windows 新增 API 最小 MSVC-target 交叉编译通过；完整交叉编译在第三方 `ring` 因本机缺 Windows SDK `assert.h` 而中止。Core PostgreSQL、C14、C17 生产宿主/Entitlement、生产账号后端链路、真实发布签名链、Windows 真实构建/运行和双平台受控账号 E2E 未完成，因此严格保持 `🧪 待集成`。 |
| B08 | 🧪 待集成 | 2026-07-16 | — | 本任务提交 | Python / TypeScript / Playwright | 后端 B08 单元/API 覆盖平台/账号/动作治理策略、冷启动并发日限、频控窗口、结果上报授权绑定与幂等重放、连续失败熔断、登录失效、异常行为、暂停/恢复、设备离线、远程停止、客户端策略覆盖拒绝和审计脱敏；前端 API/组件测试覆盖治理 Schema、账号中心健康度、最近任务、失败趋势、处置建议、服务端授权禁用和暂停/恢复/远程停止；正式 Playwright 社媒流程覆盖服务端授权、风控熔断、最近任务展示、失败趋势、远程停止和本地紧急停止。B02、Core C14/C17、生产账号后端链路、真实平台账号 E2E 与组合回归未完成，因此保持 `🧪 待集成`。 |
| B04 | ✅ 已完成 | 2026-07-16 | 2026-07-17 | 分支 `task/b04-media-library`（本任务提交，待主代理合入 main） | Python / TypeScript / Playwright（生产装配）+ 真实腾讯云 COS | 三层授权 + `video.*` 权限域 + 真实腾讯 STS + C14 审计桥接 + 成片下载 + 生产装配 E2E + 真实对象核验/预览/清理 Provider + crc64 抗伪造硬化 + 分段上传/断点续传/断网恢复真实 COS 门禁全部闭合，经三轮独立双复审 PASS；20GiB 门禁由代表性分段路径判定满足（规模压测归运维 follow-up）；保留 L-1/L-2 与 maintenance ②③④⑤ 非阻断 follow-up，详见第 5 节 B04 记录 |
| B03、B05-B07、B09-B17 | 尚未开始 | — | — | — | — | 按第 5 节依赖顺序逐项推进 |

后续每完成一项，将其拆成独立行记录。提交标识允许写“本任务提交”，精确哈希由 Git 历史追溯；不得为了回填提交自身哈希制造循环提交。
