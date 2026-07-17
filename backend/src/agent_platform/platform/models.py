from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

GATEWAY_ALIAS_PATTERN = r"^[a-z0-9][a-z0-9._-]{0,63}$"
GatewayAlias = Annotated[
    str,
    Field(max_length=64, pattern=GATEWAY_ALIAS_PATTERN),
]
# 新增 alias 前必须先处理 C16 模型网关的存量 Key 范围对齐：
# 租户虚拟 Key 的 models 范围在签发时按当时的 allowed_aliases 固化。当前只有一个 alias，
# 因此 allowed_aliases 恒等于本集合、范围漂移不可达，Controller 也就没有实现存量 Key 的
# 范围更新（LiteLLMAdminClient 无 update_key）。一旦本集合新增 alias，存量租户的 Key 会与
# 新的 desired 范围不符，对账将命中 provisioning_key_scope_conflict 永久错误并全量
# fail-closed。届时必须同步实现存量 Key 的范围对齐（见 docs/core-capability-roadmap.md C16）。
DEFAULT_MODEL_ALIASES = frozenset({"general-purpose"})
_gateway_alias_adapter = TypeAdapter(GatewayAlias)


class GatewayModelReference(BaseModel):
    """跨 API、发布快照与 runtime 共用的 provider-neutral 模型引用。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["gateway_alias"]
    alias: GatewayAlias


def validate_gateway_alias(value: str) -> str:
    return _gateway_alias_adapter.validate_python(value)
