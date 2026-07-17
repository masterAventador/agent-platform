# 会话接手说明（Handoff）

> **本文件只记「当前会话手头正在干的活」**，供会话意外丢失后接得上。
>
> - 整体进度、完成定义、验证基线、各条目的实现与复审记录 → [`core-capability-roadmap.md`](core-capability-roadmap.md)、[`industry-capability-roadmap.md`](industry-capability-roadmap.md)
> - 流程铁律（TDD、子代理、双复审、合并、部署、凭据边界）→ 项目根 `CLAUDE.md`
>
> 上面两处有的，这里不重复。**任务做完并合入后，把它从本文件删掉**——这里不留历史。

---

## 快照时间：2026-07-18

**main** = `f1f4198`（已推送）｜**迁移单头** `20260716_0038`，下一个可用号 `20260716_0039`
**常驻栈** `agent-platform-dev` 13 服务健康，Demo Seed 已重放｜账号 `demo@example.com` / `agent-platform-demo`｜API 18000、前端 18080、PG 15432、MinIO 19000

## 🟢 在途：无。所有工作树已清理，main 干净，工作区无未提交改动

本轮 4 个任务全部合入并推送：**T8**（收 main 三条红线）、**T9**（PG 夹具隔离缺陷，全量从 5 failed/13 errors → 0）、**T3**（C12 阶段二前端 + E2E）、**T4**（C16 阶段二用量/成本记录）。`git worktree list` 只剩主仓；临时容器残留 0。

## ⏸️ 当前状态：用户已暂停连续推进（2026-07-18）

用户此前授权「自主连续推进 C 系列」，但 **2026-07-18 明确说「先不用继续开了」**。**接手后不要自动开 T5**——等用户明确指令再启动。当前无任何执行单元在跑，这是正常暂停、不是卡住。

接手第一步，先确认没有遗留执行单元（脏工作树只证明改动存在、不证明执行者已退出）：

```bash
git worktree list && git status --short && ps aux | grep -E "pytest|playwright" | grep -v grep
docker ps -a --format "{{.Names}}\t{{.Status}}"
```

⚠️ 看到随机名容器（`*-mvp-test-<pid>-*`、`*-pg-*`、`c16-review-*` 等）**先查 `Status` 再动手**——曾差点把某代理刚起 3 秒的验收栈当残留清掉。**绝不碰** `agent-platform-dev`（13 服务常驻栈）和 `ragflow-local-threshold-*`（属另一项目）。

## 下一步计划（用户说开才开）

### [T5] C16 阶段三：预算/配额/限流/告警 + fallback + **C12 配额门禁接入** ← 下一个

**为什么它是关键节点**：**C12 标 ✅ 就卡在这里**（C12 完成定义第 4 条后半句「C16 引入配额后接入调度入口」是 C12 唯一剩余前置）。C16 阶段一/二已合入，阶段三阻塞已解除。这是 C16 五阶段里最复杂的一个（控制面 + 跨 C12 集成）。

**开工前必读 roadmap 的 C16 条目**，尤其这几条前置门禁（漏一条就是复审 FAIL）：

1. **配额类 `SkipReason` 不得进 `_GUARD_PAUSE_REASONS`**（C12 双复审转写的强制门禁）：配额超限是**瞬态**，自动暂停会让定时任务在下个计费周期配额恢复后也不自愈。配额跳过必须是「临时跳过」不是「永久暂停」。
2. **Cron 频率下限治理归 C16 阶段三**：C12 层无计量能力，频率下限要在有 token/费用/预算视图的这一层做。当前风险窗口如实声明——配额落地前任何 `runs.execute` 成员可建「每分钟 + ALLOW」持续烧额度，阶段三必须闭合。
3. **⚠️ nano-USD ↔ micro-USD 换算（T4 刚补，务必对齐）**：用量费用与定价表用 **nano-USD**，阶段一预算 `budget_microusd` 用 **micro-USD**，**1 micro = 1000 nano**。预算比较必须先对齐单位（`sum(nano) // 1000`），直接比会差 1000 倍、约 $0.001 用量就误判耗尽。已写进 `platform/model_gateway/pricing.py` docstring 和 roadmap 阶段二遗留项。
4. **fallback 也归阶段三**（与限流/配额同属「调用时刻 guardrail」，共用 desired-policy + Controller 机制）；LiteLLM router 配置（`config.yaml` 的 fallbacks/num_retries/timeout）要作为**受版本管理的配置产物**统一维护，不手工编辑。第 8 条「供应商故障/配额耗尽/fallback」并列测试也在本阶段。

**落点**：`platform/model_gateway/`、`workers/model_gateway_controller.py`、`platform/scheduling/guards.py::evaluate_dispatch_guards`（C12 已留好扩展点，返回新 `SkipReason` 即复用既有跳过/暂停/审计/历史链路）。

### 再往后

- **[T6] C16 阶段四**（固定数据集回归评测 + 人工反馈 + 版本对比）—— 依赖 T5。
- **[T10] PG 清理收敛为 conftest autouse**（非 C 条目，带触发条件挂账）：17/21 测试文件仍向共享库泄漏，之所以「良性」完全依赖其断言保持 uuid/租户收窄、**无机制强制**；一旦任一文件新增全库计数/存在性断言就会静默依赖执行顺序，届时必须立即启动。
- C16 阶段五（前端页面 + C14 审计收口 + 第 8 条验收）在阶段三/四之后。

## 节奏约束（沿用本轮实测）

- **Core 并行槽位上限 2 个 🚧 条目**；T5→T6 是串行链（T6 依赖 T5），当前无可并行的 C 条目。
- 写入代理**实测舒适上限 2 个，3 个开始漏审**；复审这类只读代理不占槽位可叠。
- **自主不等于降标准**：完成定义、双复审（规格 + 质量各一次独立派发）、真实边界验收、台账与代码同一提交同步，一条都不减。
- 每个任务合入 main 的那一次提交**必须同时带 roadmap 状态改动**，不是合完再补——本轮据此零文档失真窗口。
