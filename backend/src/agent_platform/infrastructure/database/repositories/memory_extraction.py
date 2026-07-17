"""任务/会话完成后的受控记忆提取入口与受控写入服务。

与 conversation_dispatch 同层：业务语义定义在 platform/memory/entities，
本模块负责组合仓储完成受控写入（依赖方向：infrastructure 可用领域实体）。

提取策略（受控、确定性）：只收编模型在输出中通过 ``<remember>...</remember>``
显式声明的内容，不做任何隐式推断；写入统一经过敏感信息脱敏 / 受控拒绝、
长度受控截断、同来源同键收编幂等和自动来源容量裁剪。

调用方（Worker）必须把本入口包在独立安全事务中：提取失败只记录日志，
不阻断 Run 收尾（与 C05 会话投影相同的隔离模式）。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_platform.infrastructure.database.repositories.employees import (
    SqlAlchemyEmployeeVersionRepository,
)
from agent_platform.infrastructure.database.repositories.memories import (
    SqlAlchemyMemoryRepository,
)
from agent_platform.platform.memory.entities import (
    MEMORY_EXTRACTION_MAX_PER_RUN,
    MEMORY_NAMESPACE_AUTO_CAPACITY,
    Memory,
    MemoryContentRejected,
    MemoryScope,
    MemorySource,
    extract_remember_directives,
    limit_memory_content,
    memory_dedupe_key,
    sanitize_memory_content,
)
from agent_platform.platform.runs.entities import Run
from agent_platform.platform.runs.events import EventType, PlatformEvent


def memory_capability_enabled(definition: Mapping[str, object]) -> bool:
    """员工发布定义中的记忆能力开关；非布尔值 fail-closed 视为关闭。"""

    capabilities = definition.get("capabilities")
    if not isinstance(capabilities, Mapping):
        return False
    return capabilities.get("memory") is True


async def record_controlled_memory(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    scope: MemoryScope,
    scope_ref: UUID,
    content: str,
    source: MemorySource,
    source_ref: str,
    created_by: UUID | None = None,
    capacity: int = MEMORY_NAMESPACE_AUTO_CAPACITY,
) -> Memory | None:
    """自动来源（运行中工具写入 / 完成后提取）的受控写入。

    脱敏后整体为敏感数据时返回 ``None``（受控拒绝）；同来源同键收编，
    并按命名空间裁剪超容量的最旧自动记忆，保证长期成本有界。
    """

    try:
        sanitized, _ = sanitize_memory_content(content.strip())
    except MemoryContentRejected:
        return None
    sanitized = limit_memory_content(sanitized)
    if not sanitized.strip():
        return None
    repository = SqlAlchemyMemoryRepository(session)
    memory = await repository.upsert(
        Memory.create(
            tenant_id=tenant_id,
            scope=scope,
            scope_ref=scope_ref,
            content=sanitized,
            source=source,
            source_ref=source_ref,
            key=memory_dedupe_key(sanitized),
            created_by=created_by,
        )
    )
    await repository.prune_auto_capacity(
        tenant_id=tenant_id,
        scope=scope,
        scope_ref=scope_ref,
        capacity=capacity,
    )
    return memory


def _message_text(value: object) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return ""


async def extract_run_memories(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    run: Run,
    history: Sequence[PlatformEvent],
) -> int:
    """从已完成任务的输出中提取显式记忆声明并收编到用户命名空间。

    只有本批 history 含 ``run.completed`` 终态事件才提取；员工发布版本
    未开启记忆能力时不读不写。返回实际收编条数。
    """

    if not any(event.type is EventType.RUN_COMPLETED for event in history):
        return 0
    directives: list[str] = []
    for event in history:
        if event.type is not EventType.MESSAGE_OUTPUT:
            continue
        for directive in extract_remember_directives(
            _message_text(event.payload.get("content"))
        ):
            if directive not in directives:
                directives.append(directive)
    if not directives:
        return 0

    stored = 0
    async with session_factory() as session:
        version = await SqlAlchemyEmployeeVersionRepository(session).get(
            tenant_id=run.tenant_id,
            employee_id=run.employee_id,
            version=run.employee_version,
        )
        if version is None or not memory_capability_enabled(version.definition):
            return 0
        for directive in directives[:MEMORY_EXTRACTION_MAX_PER_RUN]:
            memory = await record_controlled_memory(
                session,
                tenant_id=run.tenant_id,
                scope=MemoryScope.USER,
                scope_ref=run.created_by,
                content=directive,
                source=MemorySource.RUN,
                source_ref=str(run.id),
                created_by=run.created_by,
            )
            if memory is not None:
                stored += 1
        await session.commit()
    return stored
