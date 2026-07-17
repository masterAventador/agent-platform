"""租户网关虚拟 Key 的确定性派生。

Key 明文既不落数据库、也不进接口/日志/审计：Controller 与 Worker 都用本模块从
服务端密钥（``AGENT_PLATFORM_MODEL_GATEWAY_KEY_SECRET``）+ tenant_id + key_version
重新派生同一个值。数据库**只保存版本号**，连 SHA256 摘要都不存——摘要同样可现场派生，
不存它就等于数据库里不存在任何由凭据派生的材料，API 也因此完全不需要持有派生密钥。

轮换等价于递增 ``key_version``；撤销由 LiteLLM 侧 block/delete 完成。派生密钥本身的
加密存储、KMS 托管与轮换属 C18 生产凭据体系；它的 fail-closed 校验归 ``config.AppSettings``
统一负责，本模块不重复实现一套环境判定。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from uuid import UUID

from pydantic import SecretStr

# 开发/测试专用的公开弱密钥，仅用于本机开箱可用；staging/production 显式拒绝。
INSECURE_DEV_MODEL_GATEWAY_KEY_SECRET = "agent-platform-insecure-dev-model-gateway-key-secret"

_DERIVATION_PURPOSE = "model-gateway-tenant-key.v1"
_KEY_ALIAS_PREFIX = "agent-platform-tenant"


class ModelGatewayKeySecretNotConfiguredError(RuntimeError):
    """网关 Key 派生密钥缺失时 fail-closed 的受控错误（消息不含密钥材料）。"""

    def __init__(self) -> None:
        super().__init__(
            "model gateway key secret is not configured; refusing to derive tenant keys"
        )


def derive_tenant_gateway_key(
    *,
    secret: SecretStr,
    tenant_id: UUID,
    key_version: int,
) -> SecretStr:
    """派生租户虚拟 Key 明文；同一 (secret, tenant, version) 恒等。"""

    secret_value = _secret_value(secret)
    version = _key_version(key_version)
    if not isinstance(tenant_id, UUID):
        raise ValueError("tenant_id must be a UUID")
    material = hmac.new(
        secret_value.encode("utf-8"),
        f"{_DERIVATION_PURPOSE}:{tenant_id}:{version}".encode(),
        hashlib.sha256,
    ).digest()
    # LiteLLM 要求 sk- 前缀；base64url 无填充的 32 字节摘要恒为 43 个 [A-Za-z0-9_-]。
    return SecretStr("sk-" + base64.urlsafe_b64encode(material).decode().rstrip("="))


def tenant_gateway_key_digest(key: SecretStr) -> str:
    """LiteLLM 用 SHA256(key) 作为 token 标识；平台只持久化该摘要。"""

    return hashlib.sha256(_secret_value(key).encode("utf-8")).hexdigest()


def tenant_gateway_key_alias(*, tenant_id: UUID, key_version: int) -> str:
    """可归因到租户与版本的稳定 alias；不含任何密钥材料。"""

    if not isinstance(tenant_id, UUID):
        raise ValueError("tenant_id must be a UUID")
    return f"{_KEY_ALIAS_PREFIX}-{tenant_id}-v{_key_version(key_version)}"


def _secret_value(secret: SecretStr) -> str:
    if not isinstance(secret, SecretStr):
        raise ValueError("secret must be a SecretStr")
    value = secret.get_secret_value()
    if not value:
        raise ModelGatewayKeySecretNotConfiguredError()
    return value


def _key_version(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("key_version must be a positive integer")
    return value
