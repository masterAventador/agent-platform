"""平台级长期记忆领域模型。

与 LangGraph Checkpoint 的职责分离（架构边界，勿混用）：

- LangGraph Checkpointer 保存单个任务线程的运行内执行状态（消息、节点、
  中断位置），生命周期跟随任务线程，属于运行时内部数据，平台业务不直接
  读写其内容；
- Memory 是跨任务、跨会话的长期知识，按企业/用户/员工/会话四级命名空间
  保存在平台 PostgreSQL 自有 ``memories`` 表中，由平台 API 与 RBAC 治理。
  运行时只把记忆作为数据注入员工上下文（``input_data["memory_context"]``），
  并通过受控入口（save_memory 工具、任务完成后的受控提取）写入，绝不把
  记忆内容拼接为系统指令级文本，也不读写 Checkpoint。

命名空间键设计：``(tenant_id, scope, scope_ref)``。

- ``tenant`` 级：``scope_ref = tenant_id``；
- ``user`` 级：``scope_ref = user_id``；
- ``employee`` 级：``scope_ref = employee_id``；
- ``conversation`` 级：``scope_ref = conversation_id``。

``scope_ref`` 非空，配合 ``(tenant_id, scope, scope_ref, source, key)`` 唯一
约束实现同来源同键收编幂等（Worker 重投递、并发写同键均不重复落行）。
"""

import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from uuid import UUID, uuid4

MAX_MEMORY_CONTENT_CHARS = 4_000
MEMORY_TRUNCATED_MARKER = "内容已截断"
# 每次任务完成后受控提取的记忆条数上限（防单次输出制造无界写入）。
MEMORY_EXTRACTION_MAX_PER_RUN = 5
# 单命名空间内自动来源（run/conversation）记忆的容量上限；超出时收编服务
# 会裁剪最旧的自动记忆，手工记忆不受自动裁剪影响（长期成本有界）。
MEMORY_NAMESPACE_AUTO_CAPACITY = 200
# 运行时按权限注入员工上下文的记忆条数上限（按最近性截断）。
MEMORY_RUNTIME_INJECTION_LIMIT = 20

_REMEMBER_PATTERN = re.compile(r"<remember>(.*?)</remember>", re.DOTALL)

_SENSITIVE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "credential",
        re.compile(
            r"(?i)(password|passwd|pwd|secret|token|api[-_ ]?key|access[-_ ]?key|密码|口令)"
            r"\s*[:=＝：]\s*\S+"
        ),
    ),
    ("credential", re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b")),
    ("credential", re.compile(r"\bAKID[A-Za-z0-9]{10,}\b")),
    ("credential", re.compile(r"\bghp_[A-Za-z0-9]{20,}\b")),
    ("phone_number", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    ("id_number", re.compile(r"(?<!\d)\d{17}[\dXx](?![\dXx])")),
)

_REDACTED_TEMPLATE = "[已脱敏:{category}]"


class MemoryScope(StrEnum):
    TENANT = "tenant"
    USER = "user"
    EMPLOYEE = "employee"
    CONVERSATION = "conversation"


class MemorySource(StrEnum):
    RUN = "run"
    CONVERSATION = "conversation"
    MANUAL = "manual"


class MemoryStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class InvalidMemoryNamespace(ValueError):
    """scope 与引用组合不合法（缺引用或带了不属于该 scope 的引用）。"""


class MemoryContentRejected(ValueError):
    """内容整体属于敏感数据，按治理策略受控拒绝写入。"""


def resolve_scope_ref(
    scope: MemoryScope,
    *,
    tenant_id: UUID,
    user_id: UUID | None = None,
    employee_id: UUID | None = None,
    conversation_id: UUID | None = None,
) -> UUID:
    """把四级命名空间归一为非空 ``scope_ref``，拒绝错配引用。"""

    provided = {
        MemoryScope.USER: user_id,
        MemoryScope.EMPLOYEE: employee_id,
        MemoryScope.CONVERSATION: conversation_id,
    }
    for candidate_scope, value in provided.items():
        if candidate_scope is not scope and value is not None:
            raise InvalidMemoryNamespace(
                f"{scope.value} 命名空间不接受 {candidate_scope.value} 引用"
            )
    if scope is MemoryScope.TENANT:
        return tenant_id
    ref = provided[scope]
    if ref is None:
        raise InvalidMemoryNamespace(f"{scope.value} 命名空间缺少对应引用")
    return ref


def sanitize_memory_content(content: str) -> tuple[str, tuple[str, ...]]:
    """写入前脱敏：命中敏感模式的片段替换为脱敏标记。

    若脱敏后除标记外没有任何有效内容，说明整条内容就是敏感数据，
    受控拒绝（抛 :class:`MemoryContentRejected`），不落库。
    """

    sanitized = content
    categories: list[str] = []
    for category, pattern in _SENSITIVE_PATTERNS:
        replaced, count = pattern.subn(_REDACTED_TEMPLATE.format(category=category), sanitized)
        if count:
            sanitized = replaced
            if category not in categories:
                categories.append(category)
    if categories:
        residue = sanitized
        for category in categories:
            residue = residue.replace(_REDACTED_TEMPLATE.format(category=category), "")
        if not residue.strip():
            raise MemoryContentRejected("记忆内容整体为敏感数据，已拒绝写入")
    return sanitized, tuple(categories)


def limit_memory_content(content: str) -> str:
    """受控截断超长内容（用于提取路径；手工 API 直接拒绝超长）。"""

    if len(content) <= MAX_MEMORY_CONTENT_CHARS:
        return content
    digest = sha256(content.encode("utf-8")).hexdigest()[:12]
    suffix = f"\n\n[{MEMORY_TRUNCATED_MARKER}；sha256:{digest}]"
    prefix_chars = MAX_MEMORY_CONTENT_CHARS - len(suffix)
    if prefix_chars <= 0:
        return content[:MAX_MEMORY_CONTENT_CHARS]
    return f"{content[:prefix_chars]}{suffix}"


def extract_remember_directives(text: str) -> tuple[str, ...]:
    """从任务输出中提取显式 ``<remember>`` 记忆指令。

    只收编模型显式声明的内容（受控提取），去重、去空白，
    每次最多 :data:`MEMORY_EXTRACTION_MAX_PER_RUN` 条。
    """

    directives: list[str] = []
    for match in _REMEMBER_PATTERN.finditer(text):
        candidate = match.group(1).strip()
        if candidate and candidate not in directives:
            directives.append(candidate)
        if len(directives) >= MEMORY_EXTRACTION_MAX_PER_RUN:
            break
    return tuple(directives)


def memory_dedupe_key(content: str) -> str:
    """内容派生的确定性收编键：同来源同内容重复写入命中唯一约束。"""

    return sha256(content.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class Memory:
    id: UUID
    tenant_id: UUID
    scope: MemoryScope
    scope_ref: UUID
    key: str
    content: str
    source: MemorySource
    source_ref: str | None
    confidence: float
    status: MemoryStatus
    expires_at: datetime | None
    created_by: UUID | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def create(
        cls,
        *,
        tenant_id: UUID,
        scope: MemoryScope,
        scope_ref: UUID,
        content: str,
        source: MemorySource,
        source_ref: str | None = None,
        key: str | None = None,
        confidence: float = 1.0,
        expires_at: datetime | None = None,
        created_by: UUID | None = None,
    ) -> "Memory":
        stripped = content.strip()
        if not stripped:
            raise ValueError("记忆内容不能为空")
        if len(stripped) > MAX_MEMORY_CONTENT_CHARS:
            raise ValueError("记忆内容超过长度上限")
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("置信度必须位于 [0, 1]")
        now = datetime.now(UTC)
        return cls(
            id=uuid4(),
            tenant_id=tenant_id,
            scope=scope,
            scope_ref=scope_ref,
            key=key or memory_dedupe_key(stripped),
            content=stripped,
            source=source,
            source_ref=source_ref,
            confidence=confidence,
            status=MemoryStatus.ACTIVE,
            expires_at=expires_at,
            created_by=created_by,
            created_at=now,
            updated_at=now,
        )

    def is_expired(self, now: datetime | None = None) -> bool:
        if self.expires_at is None:
            return False
        return (now or datetime.now(UTC)) >= self.expires_at

    def with_status(self, status: MemoryStatus) -> "Memory":
        return replace(self, status=status, updated_at=datetime.now(UTC))

    def correct(
        self,
        *,
        content: str | None = None,
        confidence: float | None = None,
        expires_at: datetime | None | object = ...,
    ) -> "Memory":
        """纠正错误记忆：更新内容/置信/过期时间，保持身份与命名空间不变。"""

        updated = self
        if content is not None:
            stripped = content.strip()
            if not stripped:
                raise ValueError("记忆内容不能为空")
            if len(stripped) > MAX_MEMORY_CONTENT_CHARS:
                raise ValueError("记忆内容超过长度上限")
            updated = replace(updated, content=stripped)
        if confidence is not None:
            if not 0.0 <= confidence <= 1.0:
                raise ValueError("置信度必须位于 [0, 1]")
            updated = replace(updated, confidence=confidence)
        if expires_at is not ...:
            updated = replace(updated, expires_at=expires_at)  # type: ignore[arg-type]
        return replace(updated, updated_at=datetime.now(UTC))
