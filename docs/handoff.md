# 会话接手说明（Handoff）

> **本文件只记「当前会话手头正在干的活」**，供会话意外丢失后接得上。
>
> - 整体进度、完成定义、验证基线、各条目的实现与复审记录 → [`core-capability-roadmap.md`](core-capability-roadmap.md)、[`industry-capability-roadmap.md`](industry-capability-roadmap.md)
> - 流程铁律（TDD、子代理、双复审、合并、部署、凭据边界）→ 项目根 `CLAUDE.md`
>
> 上面两处有的，这里不重复。**任务做完并合入后，把它从本文件删掉**——这里不留历史。

---

## 快照时间：2026-07-17

**main** = `37ac8b5`（已推送，T8 已合入）｜**迁移单头** `20260716_0037`，下一个可用号 `20260716_0038`
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

### [T3] `wt/c12-fe` / `task/c12-frontend` —— C12 阶段二：定时任务前端 + Playwright E2E

后端 8 个端点阶段一已合入 main、无需动后端。范围是完成定义第 5 条 + 第 6 条的用户视角那一半（时区回显自洽含跨 DST、重启后列表与历史连续无重复、同一触发点只有一条执行记录、无 `runs.execute` 看不到入口且他人任务 404）。

**跑完我要做**：双复审 → 合并。注意 **C12 标 ✅ 还差 C16 阶段三的配额接入**（完成定义第 4 条后半句），阶段二合了也不能标完成。

### [T9] `wt/t9` / `task/t9-pg-fixture-isolation` —— PG 集成套件夹具隔离缺陷

根因已定位到行：`test_postgres_scheduler_concurrency.py:100-113` 是**手工维护的删表清单**，漏了 C16 新增的 `tenant_model_gateway_policies` → 删 users 被 FK 挡住 → 残留泄漏到后续文件。`model_gateway` 还有第二份同样的清单。

**要点**：不接受「把表加进清单」的补丁修法——手工清单结构性不可维护。要求做**从 SQLAlchemy metadata 自动推导**的通用清理（注意别误伤 `alembic_version`），让所有 PG 集成测试复用。验收 = 真实 PG 下 `tests/integration` 从 `5 failed / 13 errors` → `0 / 0`，且残留失败必须逐条查明、不许继续归到「隔离问题」。

**跑完我要做**：双复审 → 合并 → **回头订正 roadmap 第 6 节**（那 5 failed / 13 errors 的记录随之更新；仍不许写「0」除非每条都有归零证据）。

## 接下来的排队

> **用户已授权自主连续推进 C 系列**（2026-07-17）：按 roadmap 依赖图往下走，不用每条来问；能并行的并行，有直接依赖的串行。**B 系列暂不启动。**
>
> 自主不等于降标准——完成定义、双复审、真实边界验收、台账同步一条都不减。真受阻或需要产品取舍时才找用户。

1. **[T4] C16 阶段二**（用量/成本，纯观测面）—— T8 已合入，阻塞解除。**等 T9 或 T3 腾出槽位再开**：它会动 `tests/integration/model_gateway/`，与 T9 正在改的夹具重叠。
2. **[T5] C16 阶段三**（预算/配额/限流/告警 + fallback + **C12 配额门禁接入**）—— C12 标 ✅ 卡在这里。

再往后的顺序见 roadmap。**Core 并行槽位上限 2 个 🚧 条目**（C12、C16 已占满）；红线修复/复审这类不占槽位，但**实测同时跑 2 个写入代理是舒适上限，3 个开始漏审**。
