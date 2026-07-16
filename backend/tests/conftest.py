"""全局测试夹具：为审计哈希链装配固定测试密钥。

生产语义是 fail-closed（未配置密钥禁止写审计）；测试进程在每个用例前装配
公开的开发/测试密钥，用例结束后恢复，避免跨用例串染。需要验证密钥缺失
fail-closed 的用例可在用例内显式 ``configure_audit_hashing(None)``。
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from agent_platform.platform.audit.hashing import (
    INSECURE_DEV_AUDIT_HMAC_KEY,
    configure_audit_hashing,
)


@pytest.fixture(autouse=True)
def _configured_audit_hashing() -> Iterator[None]:
    configure_audit_hashing(INSECURE_DEV_AUDIT_HMAC_KEY)
    yield
    configure_audit_hashing(INSECURE_DEV_AUDIT_HMAC_KEY)
