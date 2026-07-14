from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

GATEWAY_ALIAS_PATTERN = r"^[a-z0-9][a-z0-9._-]{0,63}$"
GatewayAlias = Annotated[
    str,
    Field(max_length=64, pattern=GATEWAY_ALIAS_PATTERN),
]
DEFAULT_MODEL_ALIASES = frozenset({"general-purpose"})
_gateway_alias_adapter = TypeAdapter(GatewayAlias)


class GatewayModelReference(BaseModel):
    """跨 API、发布快照与 runtime 共用的 provider-neutral 模型引用。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["gateway_alias"]
    alias: GatewayAlias


def validate_gateway_alias(value: str) -> str:
    return _gateway_alias_adapter.validate_python(value)
