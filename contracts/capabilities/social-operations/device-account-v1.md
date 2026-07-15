# Social Operations 设备与平台账号契约 v1

本契约描述 B02 在能力包隔离层建立的设备、任务领取与平台账号状态边界。生产宿主注册、Core PostgreSQL 迁移、Entitlement 和 Core Audit 适配器仍由 Core 路线图负责；隔离 SQLite 适配器不是第二套平台事实源。

## 身份与授权

- 所有资源以 `tenant_id` 隔离；设备和健康上报同时绑定 `owner_user_id`，跨租户与非所有者读取统一返回不可见；
- 读取、执行与管理分别要求 `social.read`、`social.execute`、`social.manage`；
- 设备只允许 `macos`、`windows`，平台账号只允许 `douyin`、`xiaohongshu`、`kuaishou`、`wechat_channels`、`wechat`；
- API 由能力包提供独立 `APIRouter` 工厂，C17 完成能力启用和 Entitlement 门禁前不得注册到生产宿主。

## 设备与任务

- 初次注册心跳序号固定为 `0`；后续心跳序号严格递增，相同序号且版本完全相同可幂等重放，陈旧或冲突重放必须拒绝；
- 同一设备重复注册只能更新展示名和版本，必须保留注册时间、最后心跳、心跳序号及计算后的有效状态，不能借重注册把离线或紧停设备恢复在线；
- 在线状态由最后心跳和受控超时计算；远程紧急停止优先级最高，重新注册或心跳都不能清除；
- 本地任务只投递给同租户、同所有者目标设备；离线或紧停设备不能领取，领取带有限租约，租约在 `lease_expires_at <= now` 时即可恢复，`claim_attempt` 必须递增；
- 注册、心跳、投递、领取、紧停、账号绑定、健康、恢复和注销共享同一个进程内 `RLock` 线性化边界；claim/stop、enqueue/stop 和 account-execute/stop 的最终状态必须以紧停为安全终态；
- 紧急停止原因只接受非敏感枚举，取消该设备尚未完成的排队或已领取任务、熔断关联账号并禁止继续投递；恢复紧急停止需要后续显式管理契约，不能由设备自行绕过；
- 隔离 SQLite 适配器使用 `BEGIN IMMEDIATE` 和单调 revision 保存能力快照，逐级以 `O_DIRECTORY | O_NOFOLLOW` 打开祖先并以 `O_NOFOLLOW` 打开数据库，文件权限为 `0600`；持久化失败必须回滚同一临界区内的内存状态。跨进程生产原子领取必须由 Core PostgreSQL 适配器实现，不能宣称 SQLite 快照已覆盖该门禁。

## 平台账号与人工接管

- 账号稳定绑定租户、所有者、设备和平台，不保存 Cookie、Token、验证码或页面原文；
- 状态包括 `awaiting_scan`、`healthy`、`human_handoff`、`logged_out`；`awaiting_scan`、人工接管和注销均保持熔断打开，只有 `healthy` 且熔断关闭时允许后续业务执行；
- 验证码、平台风控或登录过期统一进入 `human_handoff` 并打开熔断，`authenticated` 健康信号不能绕过人工接管；
- 仅 `social.manage` 操作者可显式恢复，恢复后回到 `awaiting_scan`、保持熔断并递增 `session_revision`；注销同样递增 revision、打开熔断；
- App 浏览器 Profile 使用固定平台枚举与规范 UUID 组成私有路径；Cookie 仅以账户 ID 作为附加认证数据加密落盘，复制到其他账户必须解密失败，注销删除密文；Cookie 密钥持久保存在 App 私有目录，写入使用唯一 staging 与原子替换，所有路径祖先拒绝符号链接；
- Tauri `SocialOperationsRuntime` 把签名安装、Profile/Cookie、登录状态和同一个 `LocalExecutorManager` 串成单一命令边界：仅健康账号可启动已安装 Sidecar；注销先打开熔断并停止执行，再删除 Cookie 与账号 Profile；紧停先打开人工接管熔断再终止执行。

## 审计与敏感信息

- 能力服务只通过 `AuditPort` 记录带稳定 `event_id` 的结构化动作、租户、操作者、资源、时间和非敏感枚举；有状态存储时先把审计事件写入持久 outbox，再交付并按 `event_id` 幂等清除，生产适配器仍等待 C14；
- 审计、状态快照、错误和日志不得包含 Cookie、Token、密码、页面内容、私有路径或原始异常；
- 验证码和风控没有自动绕过动作，任何未知或无法确认的外部状态都必须安全失败并请求人工处理。

## 当前集成门禁

B02 隔离实现和自动化测试通过后只能标记 `🧪 待集成`。升级为 `✅ 已完成` 仍需：

1. Core PostgreSQL 迁移、C14 Audit 与 C17 能力宿主/Entitlement 接入；
2. 签名发布链提供受信公钥和真实 Sidecar 包；
3. macOS 与 Windows 真实 App/Sidecar 生命周期 E2E；
4. 受控测试账号完成扫码、Cookie 恢复、登录失效、验证码/风控人工接管和注销 E2E。
