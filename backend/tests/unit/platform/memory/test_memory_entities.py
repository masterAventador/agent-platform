"""Memory 领域实体单元测试。

覆盖失败矩阵：命名空间键合法性、敏感信息脱敏/受控拒绝、超大内容上限、
过期判定、禁用/启用状态机、提取指令解析与幂等 dedupe key。
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from agent_platform.platform.memory.entities import (
    MAX_MEMORY_CONTENT_CHARS,
    MEMORY_EXTRACTION_MAX_PER_RUN,
    InvalidMemoryNamespace,
    Memory,
    MemoryContentRejected,
    MemoryScope,
    MemorySource,
    MemoryStatus,
    extract_remember_directives,
    limit_memory_content,
    memory_dedupe_key,
    resolve_scope_ref,
    sanitize_memory_content,
)


class TestNamespace:
    def test_tenant_scope_uses_tenant_id_as_ref(self) -> None:
        tenant_id = uuid4()
        assert (
            resolve_scope_ref(MemoryScope.TENANT, tenant_id=tenant_id)
            == tenant_id
        )

    def test_user_scope_requires_user_id(self) -> None:
        tenant_id = uuid4()
        user_id = uuid4()
        assert (
            resolve_scope_ref(MemoryScope.USER, tenant_id=tenant_id, user_id=user_id)
            == user_id
        )
        with pytest.raises(InvalidMemoryNamespace):
            resolve_scope_ref(MemoryScope.USER, tenant_id=tenant_id)

    def test_employee_scope_requires_employee_id(self) -> None:
        tenant_id = uuid4()
        employee_id = uuid4()
        assert (
            resolve_scope_ref(
                MemoryScope.EMPLOYEE, tenant_id=tenant_id, employee_id=employee_id
            )
            == employee_id
        )
        with pytest.raises(InvalidMemoryNamespace):
            resolve_scope_ref(MemoryScope.EMPLOYEE, tenant_id=tenant_id)

    def test_conversation_scope_requires_conversation_id(self) -> None:
        tenant_id = uuid4()
        conversation_id = uuid4()
        assert (
            resolve_scope_ref(
                MemoryScope.CONVERSATION,
                tenant_id=tenant_id,
                conversation_id=conversation_id,
            )
            == conversation_id
        )
        with pytest.raises(InvalidMemoryNamespace):
            resolve_scope_ref(MemoryScope.CONVERSATION, tenant_id=tenant_id)

    def test_mismatched_ref_for_scope_is_rejected(self) -> None:
        with pytest.raises(InvalidMemoryNamespace):
            resolve_scope_ref(
                MemoryScope.USER,
                tenant_id=uuid4(),
                employee_id=uuid4(),
            )


class TestSanitize:
    def test_plain_content_is_unchanged(self) -> None:
        sanitized, categories = sanitize_memory_content("用户偏好使用中文邮件签名")
        assert sanitized == "用户偏好使用中文邮件签名"
        assert categories == ()

    @pytest.mark.parametrize(
        "content",
        [
            "登录密码: hunter2-secret，用于测试环境",
            "记住 password=SuperSecret123 这个口令",
            "调用凭据 api_key: sk-abcdef1234567890 已配置",
            "腾讯云 AKIDzZ9x8y7w6v5u4t3s2r1q 请保存",
            "同步令牌 token = ghp_16C7e42F292c6912E7710c838347Ae178B4a 到工具",
        ],
    )
    def test_credentials_are_redacted(self, content: str) -> None:
        sanitized, categories = sanitize_memory_content(content)
        assert "hunter2" not in sanitized
        assert "SuperSecret123" not in sanitized
        assert "sk-abcdef1234567890" not in sanitized
        assert "AKIDzZ9x8y7w6v5u4t3s2r1q" not in sanitized
        assert "ghp_16C7e42F292c6912E7710c838347Ae178B4a" not in sanitized
        assert categories

    def test_phone_and_id_numbers_are_redacted(self) -> None:
        sanitized, categories = sanitize_memory_content(
            "联系人手机 13812345678，身份证 11010519491231002X"
        )
        assert "13812345678" not in sanitized
        assert "11010519491231002X" not in sanitized
        assert "联系人手机" in sanitized
        assert set(categories) == {"phone_number", "id_number"}

    def test_content_that_is_only_sensitive_data_is_rejected(self) -> None:
        with pytest.raises(MemoryContentRejected):
            sanitize_memory_content("password=OnlySecretValue123")


class TestContentLimit:
    def test_within_limit_is_unchanged(self) -> None:
        assert limit_memory_content("hello") == "hello"

    def test_oversized_content_is_truncated_with_marker(self) -> None:
        content = "x" * (MAX_MEMORY_CONTENT_CHARS + 100)
        limited = limit_memory_content(content)
        assert len(limited) <= MAX_MEMORY_CONTENT_CHARS
        assert "内容已截断" in limited


class TestMemoryEntity:
    def _memory(self, **kwargs: object) -> Memory:
        tenant_id = uuid4()
        defaults: dict[str, object] = {
            "tenant_id": tenant_id,
            "scope": MemoryScope.TENANT,
            "scope_ref": tenant_id,
            "content": "企业默认使用北京时区",
            "source": MemorySource.MANUAL,
            "created_by": uuid4(),
        }
        defaults.update(kwargs)
        return Memory.create(**defaults)  # type: ignore[arg-type]

    def test_create_sets_active_status_and_dedupe_key(self) -> None:
        memory = self._memory()
        assert memory.status is MemoryStatus.ACTIVE
        assert memory.key
        assert memory.created_at.tzinfo is not None

    def test_expiry_is_judged_at_read_time(self) -> None:
        memory = self._memory(
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )
        assert memory.is_expired()
        fresh = self._memory(expires_at=datetime.now(UTC) + timedelta(days=1))
        assert not fresh.is_expired()
        assert not self._memory().is_expired()

    def test_disable_enable_round_trip(self) -> None:
        memory = self._memory()
        disabled = memory.with_status(MemoryStatus.DISABLED)
        assert disabled.status is MemoryStatus.DISABLED
        assert disabled.updated_at >= memory.updated_at
        enabled = disabled.with_status(MemoryStatus.ACTIVE)
        assert enabled.status is MemoryStatus.ACTIVE

    def test_correct_content_updates_content_and_timestamp(self) -> None:
        memory = self._memory()
        corrected = memory.correct(content="企业默认使用上海时区")
        assert corrected.content == "企业默认使用上海时区"
        assert corrected.updated_at >= memory.updated_at
        assert corrected.id == memory.id

    def test_create_rejects_oversized_content(self) -> None:
        with pytest.raises(ValueError):
            self._memory(content="x" * (MAX_MEMORY_CONTENT_CHARS + 1))

    def test_create_rejects_blank_content(self) -> None:
        with pytest.raises(ValueError):
            self._memory(content="   ")


class TestExtraction:
    def test_extracts_marked_directives(self) -> None:
        text = "好的，已完成。<remember>用户偏好中文签名</remember>其余内容"
        assert extract_remember_directives(text) == ("用户偏好中文签名",)

    def test_ignores_text_without_markers(self) -> None:
        assert extract_remember_directives("普通输出，无记忆指令") == ()

    def test_deduplicates_and_bounds_directives(self) -> None:
        directives = "".join(
            f"<remember>记忆 {index}</remember>"
            for index in range(MEMORY_EXTRACTION_MAX_PER_RUN + 3)
        )
        text = directives + "<remember>记忆 0</remember>"
        extracted = extract_remember_directives(text)
        assert len(extracted) == MEMORY_EXTRACTION_MAX_PER_RUN
        assert len(set(extracted)) == MEMORY_EXTRACTION_MAX_PER_RUN

    def test_blank_directives_are_ignored(self) -> None:
        assert extract_remember_directives("<remember>   </remember>") == ()


class TestDedupeKey:
    def test_same_content_produces_same_key(self) -> None:
        assert memory_dedupe_key("用户偏好中文签名") == memory_dedupe_key("用户偏好中文签名")

    def test_different_content_produces_different_key(self) -> None:
        assert memory_dedupe_key("a") != memory_dedupe_key("b")
