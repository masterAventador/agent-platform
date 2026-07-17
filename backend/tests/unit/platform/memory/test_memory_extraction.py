"""任务完成后的受控记忆提取单元测试。

覆盖失败矩阵：Worker 重投递不重复写、员工记忆能力禁用后不写、
敏感指令脱敏/受控跳过、超大内容受控截断、未完成任务不提取。
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from agent_platform.infrastructure.database.base import Base
from agent_platform.infrastructure.database.models import load_database_models
from agent_platform.infrastructure.database.repositories.employees import (
    EmployeeVersionRecord,
)
from agent_platform.infrastructure.database.repositories.memories import (
    SqlAlchemyMemoryRepository,
)
from agent_platform.infrastructure.database.repositories.memory_extraction import (
    extract_run_memories,
)
from agent_platform.infrastructure.database.repositories.tenants import TenantRecord
from agent_platform.platform.memory.entities import (
    MAX_MEMORY_CONTENT_CHARS,
    MemoryScope,
    MemorySource,
)
from agent_platform.platform.runs.entities import Run
from agent_platform.platform.runs.events import EventType, PlatformEvent


@pytest_asyncio.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    load_database_models()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _seed_run_fixture(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    memory_capability: bool = True,
) -> Run:
    run = Run.create(
        tenant_id=uuid4(),
        employee_id=uuid4(),
        employee_version=1,
        created_by=uuid4(),
        input_data={"message": "执行任务"},
    )
    async with session_factory() as session:
        session.add(
            TenantRecord(
                id=run.tenant_id,
                name="提取租户",
                slug=f"extract-{run.tenant_id.hex[:10]}",
                created_at=datetime.now(UTC),
            )
        )
        session.add(
            EmployeeVersionRecord(
                id=uuid4(),
                employee_id=run.employee_id,
                tenant_id=run.tenant_id,
                version=1,
                definition={
                    "work_mode": "autonomous",
                    "model": {"kind": "gateway_alias", "alias": "general-purpose"},
                    "capabilities": {
                        "conversation": True,
                        "scheduled_tasks": False,
                        "file_upload": False,
                        "memory": memory_capability,
                    },
                },
                published_by=run.created_by,
                published_at=datetime.now(UTC),
            )
        )
        await session.commit()
    return run


def _history(run: Run, *contents: str, completed: bool = True) -> list[PlatformEvent]:
    events = [
        PlatformEvent.create(
            tenant_id=run.tenant_id,
            employee_id=run.employee_id,
            run_id=run.id,
            sequence=index + 1,
            event_type=EventType.MESSAGE_OUTPUT,
            payload={"content": content},
        )
        for index, content in enumerate(contents)
    ]
    if completed:
        events.append(
            PlatformEvent.create(
                tenant_id=run.tenant_id,
                employee_id=run.employee_id,
                run_id=run.id,
                sequence=len(events) + 1,
                event_type=EventType.RUN_COMPLETED,
                payload={"status": "completed"},
            )
        )
    return events


@pytest.mark.asyncio
async def test_extracts_remember_directives_into_user_namespace(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    run = await _seed_run_fixture(session_factory)
    history = _history(
        run, "已完成。<remember>用户偏好中文邮件签名</remember>感谢使用。"
    )

    stored = await extract_run_memories(
        session_factory=session_factory, run=run, history=history
    )

    assert stored == 1
    async with session_factory() as session:
        rows = await SqlAlchemyMemoryRepository(session).list(
            tenant_id=run.tenant_id, visible_to=None
        )
    assert len(rows) == 1
    memory = rows[0]
    assert memory.scope is MemoryScope.USER
    assert memory.scope_ref == run.created_by
    assert memory.source is MemorySource.RUN
    assert memory.source_ref == str(run.id)
    assert memory.content == "用户偏好中文邮件签名"


@pytest.mark.asyncio
async def test_redelivery_does_not_duplicate_memories(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    run = await _seed_run_fixture(session_factory)
    history = _history(run, "<remember>用户偏好中文邮件签名</remember>")

    first = await extract_run_memories(
        session_factory=session_factory, run=run, history=history
    )
    second = await extract_run_memories(
        session_factory=session_factory, run=run, history=history
    )

    assert first == 1
    assert second == 1
    async with session_factory() as session:
        rows = await SqlAlchemyMemoryRepository(session).list(
            tenant_id=run.tenant_id, visible_to=None
        )
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_no_extraction_when_run_not_completed(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    run = await _seed_run_fixture(session_factory)
    history = _history(
        run, "<remember>不该被提取</remember>", completed=False
    )

    stored = await extract_run_memories(
        session_factory=session_factory, run=run, history=history
    )

    assert stored == 0
    async with session_factory() as session:
        rows = await SqlAlchemyMemoryRepository(session).list(
            tenant_id=run.tenant_id, visible_to=None
        )
    assert rows == []


@pytest.mark.asyncio
async def test_no_extraction_when_memory_capability_disabled(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    run = await _seed_run_fixture(session_factory, memory_capability=False)
    history = _history(run, "<remember>禁用后不写</remember>")

    stored = await extract_run_memories(
        session_factory=session_factory, run=run, history=history
    )

    assert stored == 0
    async with session_factory() as session:
        rows = await SqlAlchemyMemoryRepository(session).list(
            tenant_id=run.tenant_id, visible_to=None
        )
    assert rows == []


@pytest.mark.asyncio
async def test_sensitive_directive_is_sanitized_or_skipped(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    run = await _seed_run_fixture(session_factory)
    history = _history(
        run,
        "<remember>联系人手机 13812345678，偏好上午联系</remember>"
        "<remember>password=OnlySecretValue123</remember>",
    )

    stored = await extract_run_memories(
        session_factory=session_factory, run=run, history=history
    )

    assert stored == 1
    async with session_factory() as session:
        rows = await SqlAlchemyMemoryRepository(session).list(
            tenant_id=run.tenant_id, visible_to=None
        )
    assert len(rows) == 1
    assert "13812345678" not in rows[0].content
    assert "偏好上午联系" in rows[0].content


@pytest.mark.asyncio
async def test_oversized_directive_is_truncated(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    run = await _seed_run_fixture(session_factory)
    oversized = "长" * (MAX_MEMORY_CONTENT_CHARS + 500)
    history = _history(run, f"<remember>{oversized}</remember>")

    stored = await extract_run_memories(
        session_factory=session_factory, run=run, history=history
    )

    assert stored == 1
    async with session_factory() as session:
        rows = await SqlAlchemyMemoryRepository(session).list(
            tenant_id=run.tenant_id, visible_to=None
        )
    assert len(rows[0].content) <= MAX_MEMORY_CONTENT_CHARS
    assert "内容已截断" in rows[0].content
