# 会话接手说明（Handoff）

> 本文件是跨会话接手的**临时交接快照**，不是权威台账。功能状态、完成定义、验证基线一律以
> `docs/core-capability-roadmap.md`（Core）和 `docs/industry-capability-roadmap.md`（行业能力包）为准。
> 每次里程碑推进后更新本文件的"检查点"与"下一步"两节即可；与路线图冲突时以路线图为准。

## 当前检查点（2026-07-17，C12/C16 阶段一已合入）

- **Git**：`main` = `b0589ae`，C12 与 C16 的**阶段一均已 `--no-ff` 合入**。两个分支/worktree 仍在（`wt/c12`、`wt/c16`），供各自后续阶段继续用。
- **迁移**：main 单头 `20260716_0037`。链：…0034 account → **0035 定时任务(C12)** → **0036 网关 Key(C16)** → **0037 provisioned(C16)**。C12 先合入保留 0035；C16 的 0036 由主代理合并时从 0034 **重链至 0035**，`test_migrations.py` 的降级目标同步改为 0035。
- **常驻开发栈** `agent-platform-dev`：**13 服务**健康（新增 `model-gateway-controller`），Demo Seed 已重放。固定测试账号 `demo@example.com` / `agent-platform-demo`。发布端口：API 18000、前端 18080、Postgres 15432、MinIO 19000。
- **无遗留**：无隔离验收栈、孤儿测试进程、悬空镜像。

### 合并后真实链路冒烟（2026-07-17，主代理在常驻栈实测）

- **C16 的 S1 解除条件实证**（DB 直查）：`policy status=active` / `key v1 provisioned=1` / `command completed attempts=1`——**这三样 Demo Seed 都不写**（Seed 只写 `pending` 策略 + 入队 reconcile 命令、不写 Key 行），只可能由真实 Controller 对账 LiteLLM 后产生。修复前该链路不可能成立。
- **C12**：`GET /api/v1/scheduled-tasks` 返回 2 条，Cron 任务 `enabled=true` / `next_run_at=2026-07-20T01:00:00Z`（与 `0 9 * * 1-5` + Asia/Shanghai 自洽，非早期那个硬编码的 2027 假值）；单次预约以暂停态表达。
- **Demo 员工真实任务闭环**：发起 → `run.started → run.progress → approval.required` → 停在 `waiting_for_approval`（**无网关失败事件 = 租户派生 Key 在 LiteLLM 侧真实可用**）→ 经审批中心批准 → `completed`。
- **C16 策略 API 无 Key 明文泄漏**（响应断言通过）。

### Docker 磁盘清理（2026-07-17，用户授权）

删除 hugai app 全部 27 个历史 tag（约 10GB，registry 可重拉）+ 孤儿实验镜像（华为云源 ragflow v0.26.1 与 text-embeddings-inference，约 10GB）+ 构建缓存 3.7GB。镜像 52.43GB → 38.84GB。**保留**：hug-ai 的 `infiniflow/ragflow:v0.26.1`（`ragflow-local-threshold-*` 5 容器在用，记忆明令不得删）与 agent-platform 钉的 `ragflow:v0.25.6`（`infra/ragflow/VERSION`）。已配 `~/.docker/daemon.json` 的 `builder.gc`（构建缓存超 10GB 自动回收、7 天未用降到 2GB），**需重启 Docker Desktop 生效**。**镜像层面 Docker 无原生 TTL**，且无差别定时清理会反复删掉 agent-platform 钉的 12.6GB（本项目验收栈用完即销毁，该镜像永远处于「无容器引用」态），故未装镜像定时清理。

### C16 关键发现（2026-07-17 主代理核查，写入 roadmap C16 条目）

C16 **不是零起点**。提交 `9074a67`（2026-07-14，早于 C16 开工）已落第一纵切并在主线：`tenant_model_gateway_policies` + `model_gateway_provisioning_commands`（outbox）两表（迁移 `20260714_0017`）、`platform/model_gateway/` 领域与服务（revision CAS）、`GET/PUT /api/v1/model-gateway/policy`（`models.usage.read` 读 / `models.manage` 写）、`infrastructure/llm/admin.py` 的 **843 行 `LiteLLMAdminClient`**（租户聚合、受阻 Key 签发、Key 阻断/删除、spend 分页，826 项单测）。**但该 Admin 客户端只有测试引用、零生产接线**——没有 Controller、没有租户 Key 下发、没有用量/成本/配额/评测/前端。C16 阶段一的核心就是把它接进生产，**必须复用不得重写**。

### C12 起点

真零起点。`api/routes/employees.py` 的 `scheduled_tasks: Literal[False] = False` 与 `platform/employees/entities.py` 的对应校验强制关闭该能力，C12 要让它真实可用。

## Core 进度：14/20 ✅（C12、C16 🚧 进行中，阶段一均已合入）

- **已完成**：C01-C08、C10、C11、C13、C14、C15、**C17**。
- **🚧 进行中**（2026-07-17 开工，两个并行槽位已占满；**阶段一均已合入 main 并通过双复审，条目本身不标 ✅**）：
  - **C12** 定时任务 —— 分支 `task/c12-scheduled-tasks`。**①后端调度主链 ✅ 已合入**（四轮返工 + 双复审 PASS）｜②前端页面 + Playwright E2E（待开工）。另：C16 阶段三的配额接入也是 C12 的完成前置。
  - **C16** 模型治理/配额 —— 分支 `task/c16-model-governance`。**①Controller 对账 + 租户可归因/可撤销凭据 ✅ 已合入**（两轮返工 + 双复审 PASS）｜②用量/成本记录（纯观测面）｜③预算/配额/限流/告警 + fallback + C12 配额门禁接入（控制面）｜④评测｜⑤前端 + C14 审计 + 第 8 条验收。

### ⚠️ main 上有 2 条既有红线 + 1 个方法论盲区（见 [T8]，roadmap 第 6 节有完整记录）

1. `infra/litellm/test_config.py::test_local_stub_override_is_test_only_and_not_published` —— 测试笔误（拿 openai-stub 比 LiteLLM 镜像常量），疑似自创建起未通过。
2. **`tests/integration/checkpoints/test_postgres_checkpointer.py::test_postgres_runtime_closes_rebuilds_approves_and_reads_final_checkpoint`** —— **生产代码回归**（`RuntimeControlMismatch` @ `langgraph.py:481`），落在 C11/C13 交界、**两者都标着 ✅**。主代理已在常驻栈实测把范围收窄：**普通「审批→继续」活路径是通的**（实测 Demo 员工经审批中心批准后 Run 正常 `completed`），问题只在该用例特有的 **closes → rebuilds → approves**（运行时重建后再审批的 checkpoint 恢复路径）。
3. **方法论盲区（最重要）**：台账历来的「后端全量 X passed / Y skipped」**全部在未设 `TEST_DATABASE_URL` 下采集**，真实 PG 门禁用例统统落进 skip 桶——红线 2 就藏在那几十个 skipped 里。今后写「全量」必须注明采集条件，条目终验至少跑一次带真实 PG 的全量。
- **🧪 待集成**（已在 main，仅剩集成尾巴）：
  - **C09** Tool/MCP 生命周期 —— stdio 传输 E2E 缺口；生产凭据待 C18。
- **⬜ 未开始**：**C18** 生产凭据与沙箱（**依赖已解开**：前置 C14+C17 均已完成）、**C19** 协议契约自动化（**依赖已解开**：前置 C17 已完成）、**C20** 发布与容灾收口（须等 C01-C19 全绿）。

### C17 已于 2026-07-17 收口 ✅（原 🧪 待集成 已名不副实）

B04 合入后经独立规格复审 + 主代理证据复核：三条待集成项中①已真闭合（生产 `create_app` 装配、`extra_routers` 全仓已删、四组合矩阵齐全）；②「调度 Worker」与③「下载 Sidecar」定性为 **Core 中无对象（vacuous）** ——`worker_handlers` 零 dispatcher 消费方、平台无 Sidecar 分发端点（下载地址是前端用户手填输入框），**不存在可越权路径、非 fail-open**。继续挂待集成会与 B02/B08（二者都把 C17 列为自身解除条件）构成循环依赖死锁并无限期堵死 C18/C19；roadmap 第 5 节的依赖方向是单向的，无反向条款。

**关键：两条前向门禁已点名转写，防止台账断链**——B08 条目已写入「实现 `social.jobs.v1` 必须复用 `evaluate_capability_availability` 三层校验 + 建结构性守卫」；B02 与 C20 条目已写入「Sidecar/安装包分发端点必须挂 `create_capability_gate`」。三处均标注「门禁来源说明（不得删除）」。主代理裁定**不在 C17 内预建零消费方的 dispatcher 守卫**（属 CLAUDE.md 禁止的过早抽象）。

## 行业能力包（B 系列）

- 已完成：B01、B04（video-studio 素材库首个完整交付，含真实腾讯 STS/COS、crc64 抗伪造、分段/断点续传真实门禁）。
- 🧪 待集成：B02、B08。
- **状态：暂停**。用户已明确决定——**优先把 C 系列全部做完，暂停开新 B 条目**。

## 下一步（推进顺序）

1. **C12 + C16 并行推进中**（见上）。每阶段完成即做全分支差异 + 失败矩阵审查（阶段检查点不提前标完成），全部阶段闭合后走独立双复审 → 主代理合并 → 台账标 ✅。**Core 并行槽位上限为 2，已占满**（规则所限，非冲突所限；理由是给主代理保留冲突协调与审查带宽）。
2. **C18（生产凭据/沙箱）** —— 依赖已解开（C14+C17 均完成），是 C12/C16 之后的首选。生产安全基线；C18 建立后按台账轮换废止 `infra/compose/.env.platform` 里的开发凭据。C14 遗留的审计 HMAC 密钥轮换与外部锚定也归 C18。
3. **C19（协议契约自动化）** —— 依赖已解开（C17 完成）。
4. **C09 集成尾巴收口**（stdio 传输 E2E；生产凭据部分依赖 C18）。
5. **C20 最终发布与容灾收口**（须等 C01-C19 全绿）。

## 必守工作流（本项目铁律）

1. **子代理 TDD 实现**：每个条目派 `general-purpose` 子代理在 `wt/<条目>/` worktree 里实现，严格 TDD（先写测试→实跑看到 RED→最小实现→GREEN）。子代理**只写代码、跑测试、汇报，不 commit/不 push**。子代理看不到本项目 CLAUDE.md，TDD 流程、要跑的测试、迁移号占用必须写进 prompt。
2. **子代理一律后台派遣**（`run_in_background: true`），被动等 task-notification，别轮询。
3. **独立双复审**（实现完 + 每轮修复后，基于**分叉点到 HEAD 全差异**）：
   - 规格复审用 `general-purpose`（逐条核完成定义、依赖门禁、真实链路、台账、验收证据）。
   - 对抗性代码质量复审用 `pr-review-toolkit:code-reviewer`（安全 fail-open、跨边界不一致、竞态、TOCTOU、资源泄漏、无界成本、测试虚假通过）。
   - 两个复审独立、不互相替代；主代理负责证据核验和**最终放行**，不照单转述子代理的 PASS（前几轮多次出现"子代理说非阻断、主代理判定必修"的情况，如 C15 的 Owner 授予不变式、时序枚举）。
4. **复审 FAIL → 退回实现**：同类根因汇总、补一组 RED 测试后集中修复，再基于**新 HEAD 重新双复审**；两个都 PASS 前不合并。
5. **完成门**：有 UI 的条目必须真实隔离栈跑 Playwright（`frontend/e2e/`，随机小写 `PLAYWRIGHT_COMPOSE_PROJECT_NAME` + 随机端口，验后销毁）；纯后端用真实依赖集成/API 验收。
6. **主代理合并落地**：合最新 main → **迁移重链**（下一个可用号从 `20260716_0035` 起，"先合入者保留编号、后合入者重链到当时单头"）→ **语义解冲突**（高频冲突点：`frontend/src/app/App.tsx` 导航/路由、`backend/.../core_contract.py` 路由根、`backend/.../bootstrap/demo_seed.py`、`backend/tests/integration/database/test_migrations.py`、`backend/.../infrastructure/database/models.py`；禁止机械并集，逐块语义解）→ 复跑全量门禁 → `git merge --no-ff` 合入 main + push → **常驻栈重建 + 重放 Seed + 真实链路冒烟** → 台账标 ✅（完成日期/提交/验证命令 + 第 6 节基线）。合入后删 worktree/分支、清悬空镜像。

## 易踩的坑（前几轮踩过）

- **autogenerate 漂移守卫很严**：新表的唯一索引在 ORM 里要用**显式命名** `Index("uq_<table>_<col>", ..., unique=True)` 对齐迁移，**别用列级 `index=True`/`unique=True`**（会自动生成 `ix_...` 名字与迁移的 `uq_...` 不符而漂移）。C13 审批表、C15 token_digest 都因此被守卫抓出、对齐后才过。
- **worktree 合并后 `node_modules` 可能缺新依赖**：合 main 带进新 npm 依赖（如 B04 的 cos-js-sdk-v5）时，worktree 里要先 `pnpm install --frozen-lockfile` 再跑 typecheck，否则报 implicit any。
- **子代理起后台任务后可能空转不自动续起**：子代理 spawn 一个 `run_in_background` bash 然后结束回合，那个 bash 跑完它不一定自动被唤起；若巡检发现它的隔离栈已销毁/无进程/worktree 长时间无写入，用 SendMessage（保留其上下文）唤醒它核对结果、出汇报。
- **runtime E2E 本地 webServer 清理**：`test-runtime-e2e.sh` 已加按端口兜底杀本地 uvicorn/vite（cleanup + 启动预清），但子代理中途被 SIGKILL 仍可能留孤儿；主代理巡检时顺手清 `wt/*` 路径的孤儿进程（只杀 wt/ 路径的，别碰 dev 栈）。
- **video-studio 在 dev 栈要 COS 凭据才完整可用**：启动前 `set -a && source infra/compose/.env.platform && set +a` 再 `MVP_PROFILE_NAME=agent-platform-dev bash infra/platform/mvp-profile.sh start`，否则素材端点 503 fail-closed（设计如此）。

## 凭据与部署边界

- 腾讯云开发子账号 `agent-platform-server` 的 SecretId/SecretKey + 开发桶 `agent-platform-1424480216` 在 `infra/compose/.env.platform`，用户已批准随私有仓库版本化（仅开发/演示，C18 生产凭据体系建立时轮换废止）。百炼 API Key 同样随仓库版本化。**这两个例外之外，不扩展到任何其他凭据。**
- 当前阶段一切在本机跑，**未经用户新指令不得部署到远程/云/公网**。
