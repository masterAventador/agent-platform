# 会话接手说明（Handoff）

> **本文件只记「当前会话手头正在干的活」**，供会话意外丢失后接得上。
>
> - 整体进度、完成定义、验证基线、各条目的实现与复审记录 → [`core-capability-roadmap.md`](core-capability-roadmap.md)、[`industry-capability-roadmap.md`](industry-capability-roadmap.md)
> - 流程铁律（TDD、子代理、双复审、合并、部署、凭据边界）→ 项目根 `CLAUDE.md`
>
> 上面两处有的，这里不重复。**任务做完并合入后，把它从本文件删掉**——这里不留历史。

---

## 快照时间：2026-07-17

**main** = `926ea6b`（已推送）｜**迁移单头** `20260716_0037`，下一个可用号 `20260716_0038`
**常驻栈** `agent-platform-dev` 13 服务健康，Demo Seed 已重放｜账号 `demo@example.com` / `agent-platform-demo`｜API 18000、前端 18080、PG 15432、MinIO 19000

## 🔴 在途：两个后台代理正在跑

**接手第一步——先确认它们死活，别凭工作树有改动就假定还在跑**（CLAUDE.md「长任务防假忙与中断恢复」）：

```bash
git worktree list && for w in wt/*/; do echo "--- $w"; git -C "$w" status --short; done
ps aux | grep -E "pytest|playwright" | grep -v grep
docker ps -a --format "{{.Names}}\t{{.Status}}"
```

各代理**零文件重叠**，都被要求**不 commit/不 push**（所以工作区有改动是正常的）。若确认代理已死且有改动，**先读完整差异再决定接管或重派**，别直接覆盖。

⚠️ 看到随机名容器（如 `*-mvp-test-<pid>-*`、`t8-pg*`）**先查 `Status` 再动手**——曾差点把某代理刚起 3 秒的验收栈当残留清掉。

### [T8] `task/t8-main-redlines` @ `322ebc1` —— 收 main 上的三条既有红线｜**实现已完、双复审在跑**

三条红线全部收完，**零生产改动**（`backend/src/` 空 diff）——根因同一个：真实 PG / 真实构建门禁被跳过后，旧契约测试没随生产加固更新。完整证据与根因 → **roadmap 第 6 节**，不在这里复述。

**两个复审代理在跑**（各自独立工作树，防串扰）：
- 规格复审 → `wt/t8`（general-purpose）
- 质量复审 → `wt/t8-review`（`pr-review-toolkit:code-reviewer`，detached）

给两者的共同要求：审 `git diff cc25cd3..322ebc1` **全分支**；不得采信开发代理汇报；**自己重做变异验证**（删掉 `runs.py` 的 fail-closed → 新门禁必须转红）。核心风险点是**"改测试去迎合生产"把真缺陷洗白**，以及 `.get(k, [])` 类写法造成的空断言。

**跑完我要做**：两者皆 PASS → 合并 → roadmap 第 6 节「当前已知失败」按复审建议改写（**注意采集口径盲区**：历史"全量 X passed"都是没设 `TEST_DATABASE_URL` 采的，红线就藏在 skipped 里）→ 删掉 `wt/t8-review` 工作树。任一 FAIL → 退回实现，**汇总同类根因后集中修**，不许逐条打补丁。

### [T3] `wt/c12-fe` / `task/c12-frontend` —— C12 阶段二：定时任务前端 + Playwright E2E

后端 8 个端点阶段一已合入 main、无需动后端。范围是完成定义第 5 条 + 第 6 条的用户视角那一半（时区回显自洽含跨 DST、重启后列表与历史连续无重复、同一触发点只有一条执行记录、无 `runs.execute` 看不到入口且他人任务 404）。

**跑完我要做**：双复审 → 合并。注意 **C12 标 ✅ 还差 C16 阶段三的配额接入**（完成定义第 4 条后半句），阶段二合了也不能标完成。

## 接下来的排队

> **用户已授权自主连续推进 C 系列**（2026-07-17）：按 roadmap 依赖图往下走，不用每条来问；能并行的并行，有直接依赖的串行。**B 系列暂不启动。**
>
> 自主不等于降标准——完成定义、双复审、真实边界验收、台账同步一条都不减。真受阻或需要产品取舍时才找用户。

1. **[T9] PG 集成套件隔离缺陷** —— 夹具 teardown `DELETE FROM users` 非 FK 安全，每文件单跑全绿、连跑 FK 连锁失败。**不修它，带真实 PG 的全量就永远淹在噪音里**——而那正是三条红线的藏身处。建议优先于 C16 阶段二。
2. **[T4] C16 阶段二**（用量/成本，纯观测面）—— **等 T8 合并后再开**：它落点在 `infrastructure/llm/`、`runtime_composition.py`，与 T8 改的 `runtimes/langgraph.py` 紧邻。

再往后的顺序见 roadmap。**Core 并行槽位上限 2 个 🚧 条目**（C12、C16 已占满）；红线修复/复审这类不占槽位，但**实测同时跑 2 个写入代理是舒适上限，3 个开始漏审**。
