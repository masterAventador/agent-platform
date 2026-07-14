# Contracts

保存由后端导出的 OpenAPI、平台事件、能力协议 JSON Schema 和跨端契约测试样例。生成产物不能手工维护第二套业务模型。

当前契约：

- `events/platform-event.schema.json`：Core 平台事件；
- `capabilities/social-operations/local-executor-v1.schema.json`：Social Operations 本地执行器 v1；
- `capabilities/social-operations/local-executor-v1.md`：本地执行器幂等、取消、兼容和安全语义；
- `fixtures/capabilities/social-operations/local-executor-v1/`：5 个有效、12 个无效协议回放样例。

Schema 统一由 `scripts/export_event_contracts.py` 从后端 Pydantic 单一来源导出。外部语言可以消费快照生成类型，但不得把生成代码反向当作领域模型来源；还必须执行协议文档声明的 `x-semantic-validation-required` 语义规则和有效/无效 fixture 回放。
