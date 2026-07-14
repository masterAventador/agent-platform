# Social Operations 本地执行器协议 v1

此协议定义平台后端与未来 Tauri 受认证 Sidecar 之间的供应商无关消息格式。本切片只锁定协议，不实现 IPC、Sidecar、设备任务、浏览器自动化或 UI。

## 单一来源与边界

- Pydantic 单一来源：`backend/src/agent_platform/capabilities/social_operations/local_executor_protocol.py`；
- 可供 Rust、TypeScript 或其他客户端生成类型的快照：`local-executor-v1.schema.json`；
- 5 个有效与 12 个无效回放样例：`contracts/fixtures/capabilities/social-operations/local-executor-v1/`；
- `task_id`、`tenant_id`、`approval_id`、`audit_correlation_id` 和 `artifact_id` 都是 Core 资源的稳定引用，本协议不复制 Core Run、Approval、Audit 或 Artifact 领域模型；
- 传输认证、授权、Entitlement、设备注册、任务持久化与审计仍由后续 Core/业务任务实现，Schema 校验不能替代这些安全检查。

跨语言消费者必须按以下顺序校验，禁止只生成类型后直接执行任务：

1. 使用 Schema 声明的 JSON Schema Draft 2020-12 方言，并启用 `date-time` 等格式检查器，完成结构和格式校验；
2. 执行 Schema 中 `x-semantic-validation-required` 声明的跨字段语义校验；v1 当前必须验证 `deadline_at` 严格晚于 `sent_at`；
3. 再执行传输认证、租户/设备/能力授权、Entitlement、审批和审计等运行时门禁。

标准 JSON Schema 无法比较两个日期字段，因此 `deadline-before-send.json` 是有意保留的“结构层接受、语义层拒绝”样例；其他 `invalid/` 样例必须被 Draft 2020-12 + FormatChecker 结构层拒绝。未来 Rust 实现除生成 wire 类型外，还必须回放两类 fixture，并实现相同语义校验。

## 消息

所有消息都必须显式携带精确的 `protocol_version=1.0`，不得依赖发送端或接收端补默认值；同时携带唯一 `message_id`、带时区的 `sent_at`、任务身份和治理关联：

- `task.request`：提交带幂等键、截止时间、任务类型、JSON 输入和 Core Artifact 输入引用的本地任务；
- `task.cancel`：使用独立幂等键取消同一 `task_id`，支持用户取消、授权撤销、紧急停止和截止时间到期；
- `task.response`：返回 `accepted`、`running`、`completed` 或 `cancelled` 状态、JSON 结果以及 Core Artifact 输出/证据引用；
- `task.error`：返回稳定错误类别、机器可读错误码、可安全展示的消息和显式重试语义。

`correlation_id` 串联一次业务调用，`target_device_id` 防止任务被投递给错误设备，`capability_id` 固定为 `social-operations`。每条消息还必须携带 `audit_correlation_id`；敏感任务已经进入 Core 审批时通过可选 `approval_id` 关联，不在本协议中表达或伪造审批状态。

## 幂等、截止时间与取消

- 发送方对同一个业务尝试重复发送时必须复用同一 `idempotency_key`；接收方按租户、任务、消息类型和幂等键去重，并回放已保存结果，不重复产生外部副作用；
- 新的业务尝试必须使用新的幂等键，但保持同一 `task_id` 与 `correlation_id`，便于审计重试链；
- `deadline_at` 必须晚于 `sent_at`。执行器在接受前和产生外部副作用前都必须重新检查截止时间；超时后不得启动新副作用；
- 取消是协作式控制消息，不等于已经终止。只有收到 `cancelled` 响应或 `cancelled` 错误后，平台才把本地执行视为停止；无法确认外部副作用结果时必须安全失败并转人工，不得自动重放。

## Artifact 与错误

- 请求只能引用 `usage=input` 的 Core Artifact；响应只能引用 `usage=output` 或 `usage=evidence` 的 Core Artifact；
- 协议不携带对象存储键、签名 URL、文件路径或长期凭据；执行器只能通过后续受授权接口解析短期访问能力；
- `safe_message` 必须经过脱敏，可用于用户界面和审计摘要；原始异常、Cookie、Token、页面内容和本机路径不得放入协议；
- `retryable=false` 时 `retry_after_ms` 只能为 `null` 或缺省；非空重试延迟只允许出现在 `retryable=true` 的错误中，且不能据此绕过平台频控、审批或风控门禁。

## 兼容与未知字段

- v1 消费方只接受精确的 `protocol_version=1.0`；不支持的版本必须明确拒绝，禁止按相近结构猜测执行；
- 所有对象都采用未知字段拒绝策略，防止新字段被旧执行器静默忽略后产生错误副作用；
- 前向兼容数据只能放入显式 `extensions`，键必须以能力包所有的 `social.` 开头并使用稳定命名段；`core.*`、其他能力包命名空间和未命名扩展一律拒绝；扩展不能表示或覆盖 Core 授权、Entitlement、审批、安全和审计结论，未知 Social Operations 扩展可以保留并转发，但不得改变核心字段的授权、幂等、截止时间或取消语义；
- 兼容的 v1 演进只能新增可选扩展或放宽无安全影响的枚举。新增必填字段、改变字段含义或删除字段必须发布新的协议大版本，并保留明确迁移期。
