# Social Operations 本地执行器协议 v1

此协议定义平台后端与未来 Tauri 受认证 Sidecar 之间的供应商无关消息格式。本切片只锁定协议，不实现 IPC、Sidecar、设备任务、浏览器自动化或 UI。

## 单一来源与边界

- Pydantic 单一来源：`backend/src/agent_platform/capabilities/social_operations/local_executor_protocol.py`；
- 可供 Rust、TypeScript 或其他客户端生成类型的快照：`local-executor-v1.schema.json`；
- 10 个有效与 25 个无效回放样例：`contracts/fixtures/capabilities/social-operations/local-executor-v1/`；
- `task_id`、`tenant_id`、`approval_id`、`audit_correlation_id` 和 `artifact_id` 都是 Core 资源的稳定引用，本协议不复制 Core Run、Approval、Audit 或 Artifact 领域模型；
- 传输认证、授权、Entitlement、设备注册、任务持久化与审计仍由后续 Core/业务任务实现，Schema 校验不能替代这些安全检查。

跨语言消费者必须按以下顺序校验，禁止只生成类型后直接执行任务：

1. 使用 Schema 声明的 JSON Schema Draft 2020-12 方言，并启用 `date-time` 等格式检查器，完成结构和格式校验；
2. 执行 Schema 中 `x-semantic-validation-required` 声明的语义校验；v1 当前必须验证 `deadline_at` 严格晚于 `sent_at`，拒绝在 `extensions` 键名中表达 Cookie、Token、API Key、Private Key、密码、本机路径、签名 URL 等敏感数据，拒绝 `safe_message` 中出现凭据头、赋值式敏感参数或私有本机路径标记，并对控制事件的字符串扩展值执行相同级别的凭据与路径标记检查；
3. 再执行传输认证、租户/设备/能力授权、Entitlement、审批和审计等运行时门禁。

当前共有 5 个语义层无效样例：`deadline-before-send.json`、`diagnostic-sensitive-field.json`、`diagnostic-unsafe-message.json`、`control-event-cookie-assignment.json` 和 `control-event-inline-data.json`。它们有意由 Draft 2020-12 + FormatChecker 结构层接受，再由 Pydantic 或跨语言等价语义层拒绝。其余 20 个无效样例必须在标准 Schema 结构/格式层直接拒绝。未来 Rust 实现除生成 wire 类型外，还必须按这份明确清单回放两类 fixture，并实现相同语义校验；新增或重分类 fixture 时必须同步本文档和契约测试。

## 消息

所有消息都必须显式携带精确的 `protocol_version=1.0`，不得依赖发送端或接收端补默认值；同时携带唯一 `message_id`、带时区的 `sent_at`、任务身份和治理关联：

- `task.request`：提交带幂等键、截止时间、任务类型、JSON 输入和 Core Artifact 输入引用的本地任务；
- `task.cancel`：使用独立幂等键取消同一 `task_id`，支持用户取消、授权撤销、紧急停止和截止时间到期；
- `task.response`：返回 `accepted`、`running`、`completed` 或 `cancelled` 状态、JSON 结果以及 Core Artifact 输出/证据引用；
- `task.error`：返回稳定错误类别、机器可读错误码、可安全展示的消息和显式重试语义。
- `step.progress`：报告稳定步骤 ID、步骤代码、严格进度状态、百分比和安全展示消息；
- `handoff.requested`：报告稳定接管 ID，并用受限原因枚举请求用户处理验证码、扫码失效、风控、元素漂移、权限缺失或未知异常；协议没有任何绕过验证的状态或动作；
- `diagnostic.event`：报告稳定诊断 ID、严重级别、机器可读代码和脱敏后的安全展示消息，原始页面、本机路径、凭据与内联截图不进入 wire 消息。

`correlation_id` 串联一次业务调用，`target_device_id` 防止任务被投递给错误设备，`capability_id` 固定为 `social-operations`。每条消息还必须携带 `audit_correlation_id`；敏感任务已经进入 Core 审批时通过可选 `approval_id` 关联，不在本协议中表达或伪造审批状态。

步骤、接管和诊断事件继续属于同一条本地执行器 wire 通道，因此直接扩展现有 `message_type` 判别联合，而不维护第二套事件 Schema。它们复用相同的 `protocol_version=1.0`、任务/租户/关联/设备/能力身份、治理引用和扩展策略。v1 消费者必须按 `message_type` 做穷举处理；不认识的 variant 必须明确拒绝，不能猜测执行。

## 控制事件顺序与状态

- `event_sequence` 从 1 开始，对同一任务、设备和执行器严格连续递增，且必须是 JSON 整数，禁止把布尔或字符串强转为序号；`progress_percent` 同样使用严格整数；`message_id` 在回放内唯一；同一回放中的身份、`executor_id`、完整治理关联（`audit_correlation_id` 与 `approval_id`）和 `step_code` 不得漂移，`sent_at` 不得倒退；治理关联需要变化时必须进入新的受控执行尝试，不能在同一事件流中静默切换审计或审批链；
- 新 `step_id` 的首个状态必须为 `started` 且进度为 0；`completed` 的进度必须为 100；两条边界同时由 Pydantic 语义校验和标准 JSON Schema `if/then` 约束，跨语言消费者不能只依赖其中一层；进度不得倒退，`completed`、`failed`、`cancelled` 终态后不得再转换；
- `handoff.requested` 只能关联已经进入 `waiting_for_human` 的步骤；`handoff_id` 在任务内唯一，原因只描述需要人工处理的安全失败，不表达验证码、扫码、风控或设备绑定绕过；
- `diagnostic_id` 在任务内唯一；诊断关联某一步时必须引用已出现的 `step_id`；
- `MockSocialOperationsReplayHost` 只在 B01 隔离测试中回放上述组合契约，不注册设备、不领取任务、不建立 IPC、不启动 Sidecar，也不执行 RPA 或任何生产副作用；畸形 payload 只能返回稳定的 `invalid control event payload`，不得把 Pydantic 输入值、凭据、异常 cause 或 context 泄漏给调用方。

## 幂等、截止时间与取消

- 发送方对同一个业务尝试重复发送时必须复用同一 `idempotency_key`；接收方按租户、任务、消息类型和幂等键去重，并回放已保存结果，不重复产生外部副作用；
- 新的业务尝试必须使用新的幂等键，但保持同一 `task_id` 与 `correlation_id`，便于审计重试链；
- `deadline_at` 必须晚于 `sent_at`。执行器在接受前和产生外部副作用前都必须重新检查截止时间；超时后不得启动新副作用；
- 取消是协作式控制消息，不等于已经终止。只有收到 `cancelled` 响应或 `cancelled` 错误后，平台才把本地执行视为停止；无法确认外部副作用结果时必须安全失败并转人工，不得自动重放。

## Artifact 与错误

- 请求只能引用 `usage=input` 的 Core Artifact；响应只能引用 `usage=output` 或 `usage=evidence` 的 Core Artifact；
- 协议不携带对象存储键、签名 URL、文件路径或长期凭据；执行器只能通过后续受授权接口解析短期访问能力；
- `safe_message` 必须由生产者先行脱敏，才能用于用户界面和审计摘要；原始异常、Cookie、Token、页面内容和本机路径不得放入协议。协议校验只是纵深防御，不负责从敏感输入中提取或生成安全摘要，也不能替代生产者的结构化脱敏；赋值式常见凭据（包括 `api_key`、`private_key`、`access_token`、`refresh_token`、`session_cookie`、`api_token`、`cookie`、`token` 和 `password`）、Bearer 凭据、Data URI、Base64 内联载荷、`file://` URI、`/Users/`、`/home/`、`/root/`、`/tmp/`、`/var/folders/` 以及 Windows 盘符绝对路径必须由语义层拒绝；控制事件的字符串扩展值如果可解析为 JSON object 或 array，必须作为标量内编码嵌套数据直接拒绝，与其内部键名是否已知无关；普通网页 URL、API 路由、花括号叙述、JSON 字符串/数字/布尔字面量、“Cookie 策略”“Base64 编码”，以及 `disabled`、`unavailable`、`redacted`、`missing`、`not set`、`string`、`boolean` 等明确状态或类型描述不因此被拒绝；
- 控制事件只允许 `usage=evidence` 的 Core Artifact 引用，不允许内联文件、截图、URL、对象键或本机路径；
- `retryable=false` 时 `retry_after_ms` 只能为 `null` 或缺省；非空重试延迟只允许出现在 `retryable=true` 的错误中，且不能据此绕过平台频控、审批或风控门禁。

## 兼容与未知字段

- v1 消费方只接受精确的 `protocol_version=1.0`；不支持的版本必须明确拒绝，禁止按相近结构猜测执行；
- 所有对象都采用未知字段拒绝策略，防止新字段被旧执行器静默忽略后产生错误副作用；
- 前向兼容数据只能放入显式 `extensions`，键必须以能力包所有的 `social.` 开头并使用稳定命名段；`core.*`、其他能力包命名空间和未命名扩展一律拒绝；扩展键不得命名 Cookie、Token、API Key、Private Key、密码、凭据、本机路径、对象键或签名 URL；步骤、接管和诊断控制事件的扩展值只允许平坦 JSON 标量（安全字符串、严格整数、有限浮点数、严格布尔或 `null`），`NaN`、正负无穷和溢出为无穷的 JSON 数字必须拒绝，禁止对象、数组、内联截图和嵌套 payload，字符串还必须通过凭据、私有本机路径和控制字符安全校验；该限制不改变 `task.request`、`task.response` 的现有 JSON 业务载荷及扩展兼容性；扩展不能表示或覆盖 Core 授权、Entitlement、审批、安全和审计结论，未知 Social Operations 扩展可以保留并转发，但不得改变核心字段的授权、幂等、截止时间、取消、步骤或人工接管语义；
- 兼容的 v1 演进只能新增可选扩展或放宽无安全影响的枚举。新增必填字段、改变字段含义或删除字段必须发布新的协议大版本，并保留明确迁移期。
