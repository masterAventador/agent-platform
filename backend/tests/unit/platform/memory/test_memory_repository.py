"""Memory 仓储单元测试（sqlite 内存库，真实 SQLAlchemy 行为）。

覆盖失败矩阵：租户隔离、过期记忆不召回、禁用不召回、同来源同键收编
幂等（重投递/并发写同键）、命名空间可见性、自动来源容量裁剪。
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from agent_platform.infrastructure.database.base import Base
from agent_platform.infrastructure.database.models import load_database_models
from agent_platform.infrastructure.database.repositories.conversations import (
    ConversationRecord,
)
from agent_platform.infrastructure.database.repositories.memories import (
    SqlAlchemyMemoryRepository,
)
from agent_platform.infrastructure.database.repositories.tenants import TenantRecord
from agent_platform.platform.memory.entities import (
    Memory,
    MemoryScope,
    MemorySource,
    MemoryStatus,
)


@pytest_asyncio.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    load_database_models()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _seed_tenant(session: AsyncSession, tenant_id: UUID) -> None:
    session.add(
        TenantRecord(
            id=tenant_id,
            name=f"租户 {tenant_id.hex[:6]}",
            slug=f"tenant-{tenant_id.hex[:10]}",
            created_at=datetime.now(UTC),
        )
    )
    await session.flush()


def _memory(
    tenant_id: UUID,
    *,
    scope: MemoryScope = MemoryScope.TENANT,
    scope_ref: UUID | None = None,
    content: str = "企业统一使用北京时区",
    source: MemorySource = MemorySource.MANUAL,
    source_ref: str | None = None,
    key: str | None = None,
    expires_at: datetime | None = None,
) -> Memory:
    return Memory.create(
        tenant_id=tenant_id,
        scope=scope,
        scope_ref=scope_ref or tenant_id,
        content=content,
        source=source,
        source_ref=source_ref,
        key=key,
        expires_at=expires_at,
    )


@pytest.mark.asyncio
async def test_add_get_update_delete_round_trip(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    tenant_id = uuid4()
    async with session_factory() as session:
        await _seed_tenant(session, tenant_id)
        repository = SqlAlchemyMemoryRepository(session)
        memory = _memory(tenant_id)
        await repository.add(memory)
        await session.commit()

    async with session_factory() as session:
        repository = SqlAlchemyMemoryRepository(session)
        loaded = await repository.get(tenant_id=tenant_id, memory_id=memory.id)
        assert loaded is not None
        assert loaded.content == memory.content
        assert loaded.status is MemoryStatus.ACTIVE

        corrected = loaded.correct(content="企业统一使用上海时区")
        await repository.update(corrected)
        await session.commit()

    async with session_factory() as session:
        repository = SqlAlchemyMemoryRepository(session)
        reloaded = await repository.get(tenant_id=tenant_id, memory_id=memory.id)
        assert reloaded is not None
        assert reloaded.content == "企业统一使用上海时区"

        assert await repository.delete(tenant_id=tenant_id, memory_id=memory.id) is True
        await session.commit()

    async with session_factory() as session:
        repository = SqlAlchemyMemoryRepository(session)
        assert await repository.get(tenant_id=tenant_id, memory_id=memory.id) is None
        assert await repository.delete(tenant_id=tenant_id, memory_id=memory.id) is False


@pytest.mark.asyncio
async def test_cross_tenant_access_is_isolated(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    tenant_a = uuid4()
    tenant_b = uuid4()
    async with session_factory() as session:
        await _seed_tenant(session, tenant_a)
        await _seed_tenant(session, tenant_b)
        repository = SqlAlchemyMemoryRepository(session)
        memory = _memory(tenant_a)
        await repository.add(memory)
        await session.commit()

    async with session_factory() as session:
        repository = SqlAlchemyMemoryRepository(session)
        assert await repository.get(tenant_id=tenant_b, memory_id=memory.id) is None
        assert await repository.delete(tenant_id=tenant_b, memory_id=memory.id) is False
        listed = await repository.list(tenant_id=tenant_b, visible_to=None)
        assert listed == []


@pytest.mark.asyncio
async def test_upsert_same_source_and_key_is_idempotent(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Worker 重投递/并发写同键：同 (命名空间, 来源, key) 收编为一行。"""

    tenant_id = uuid4()
    user_id = uuid4()
    async with session_factory() as session:
        await _seed_tenant(session, tenant_id)
        repository = SqlAlchemyMemoryRepository(session)
        first = _memory(
            tenant_id,
            scope=MemoryScope.USER,
            scope_ref=user_id,
            content="用户偏好中文签名",
            source=MemorySource.RUN,
            source_ref="run-1",
        )
        stored_first = await repository.upsert(first)
        await session.commit()

    async with session_factory() as session:
        repository = SqlAlchemyMemoryRepository(session)
        duplicate = _memory(
            tenant_id,
            scope=MemoryScope.USER,
            scope_ref=user_id,
            content="用户偏好中文签名",
            source=MemorySource.RUN,
            source_ref="run-1-redelivered",
        )
        stored_second = await repository.upsert(duplicate)
        await session.commit()
        assert stored_second.id == stored_first.id

    async with session_factory() as session:
        repository = SqlAlchemyMemoryRepository(session)
        rows = await repository.list(tenant_id=tenant_id, visible_to=None)
        assert len(rows) == 1


@pytest.mark.asyncio
async def test_runtime_recall_excludes_expired_disabled_and_foreign_namespaces(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    tenant_id = uuid4()
    user_id = uuid4()
    other_user_id = uuid4()
    employee_id = uuid4()
    other_employee_id = uuid4()
    async with session_factory() as session:
        await _seed_tenant(session, tenant_id)
        repository = SqlAlchemyMemoryRepository(session)
        visible_tenant = _memory(tenant_id, content="企业记忆")
        visible_user = _memory(
            tenant_id, scope=MemoryScope.USER, scope_ref=user_id, content="我的偏好"
        )
        visible_employee = _memory(
            tenant_id,
            scope=MemoryScope.EMPLOYEE,
            scope_ref=employee_id,
            content="员工经验",
        )
        expired = _memory(
            tenant_id,
            content="已过期记忆",
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )
        foreign_user = _memory(
            tenant_id,
            scope=MemoryScope.USER,
            scope_ref=other_user_id,
            content="别人的偏好",
        )
        foreign_employee = _memory(
            tenant_id,
            scope=MemoryScope.EMPLOYEE,
            scope_ref=other_employee_id,
            content="其他员工经验",
        )
        conversation_memory = _memory(
            tenant_id,
            scope=MemoryScope.CONVERSATION,
            scope_ref=uuid4(),
            content="别的会话记忆",
        )
        for memory in (
            visible_tenant,
            visible_user,
            visible_employee,
            expired,
            foreign_user,
            foreign_employee,
            conversation_memory,
        ):
            await repository.add(memory)
        disabled = _memory(tenant_id, content="被禁用记忆").with_status(
            MemoryStatus.DISABLED
        )
        await repository.add(disabled)
        await session.commit()

    async with session_factory() as session:
        repository = SqlAlchemyMemoryRepository(session)
        recalled = await repository.search_for_runtime(
            tenant_id=tenant_id,
            user_id=user_id,
            employee_id=employee_id,
            conversation_id=None,
        )
        contents = {memory.content for memory in recalled}
        assert contents == {"企业记忆", "我的偏好", "员工经验"}


@pytest.mark.asyncio
async def test_runtime_recall_orders_by_recency_and_respects_limit(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    tenant_id = uuid4()
    async with session_factory() as session:
        await _seed_tenant(session, tenant_id)
        repository = SqlAlchemyMemoryRepository(session)
        for index in range(5):
            memory = _memory(tenant_id, content=f"记忆 {index}")
            object.__setattr__(
                memory,
                "updated_at",
                datetime.now(UTC) + timedelta(seconds=index),
            )
            await repository.add(memory)
        await session.commit()

    async with session_factory() as session:
        repository = SqlAlchemyMemoryRepository(session)
        recalled = await repository.search_for_runtime(
            tenant_id=tenant_id,
            user_id=uuid4(),
            employee_id=uuid4(),
            conversation_id=None,
            limit=3,
        )
        assert [memory.content for memory in recalled] == ["记忆 4", "记忆 3", "记忆 2"]


@pytest.mark.asyncio
async def test_list_visibility_for_member_and_keyword_filter(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    tenant_id = uuid4()
    member_id = uuid4()
    other_id = uuid4()
    conversation_id = uuid4()
    other_conversation_id = uuid4()
    async with session_factory() as session:
        await _seed_tenant(session, tenant_id)
        for conv_id, owner in (
            (conversation_id, member_id),
            (other_conversation_id, other_id),
        ):
            session.add(
                ConversationRecord(
                    id=conv_id,
                    tenant_id=tenant_id,
                    employee_id=uuid4(),
                    created_by=owner,
                    title="会话",
                    thread_id=f"conversation:{conv_id}",
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )
            )
        repository = SqlAlchemyMemoryRepository(session)
        await repository.add(_memory(tenant_id, content="企业级记忆"))
        await repository.add(
            _memory(
                tenant_id,
                scope=MemoryScope.EMPLOYEE,
                scope_ref=uuid4(),
                content="员工级记忆",
            )
        )
        await repository.add(
            _memory(
                tenant_id, scope=MemoryScope.USER, scope_ref=member_id, content="我的记忆"
            )
        )
        await repository.add(
            _memory(
                tenant_id, scope=MemoryScope.USER, scope_ref=other_id, content="他人记忆"
            )
        )
        await repository.add(
            _memory(
                tenant_id,
                scope=MemoryScope.CONVERSATION,
                scope_ref=conversation_id,
                content="我的会话记忆",
            )
        )
        await repository.add(
            _memory(
                tenant_id,
                scope=MemoryScope.CONVERSATION,
                scope_ref=other_conversation_id,
                content="他人会话记忆",
            )
        )
        await session.commit()

    async with session_factory() as session:
        repository = SqlAlchemyMemoryRepository(session)
        member_view = await repository.list(tenant_id=tenant_id, visible_to=member_id)
        assert {memory.content for memory in member_view} == {
            "企业级记忆",
            "员工级记忆",
            "我的记忆",
            "我的会话记忆",
        }

        admin_view = await repository.list(tenant_id=tenant_id, visible_to=None)
        assert len(admin_view) == 6

        keyword_view = await repository.list(
            tenant_id=tenant_id, visible_to=None, keyword="员工"
        )
        assert {memory.content for memory in keyword_view} == {"员工级记忆"}


@pytest.mark.asyncio
async def test_prune_auto_capacity_keeps_manual_and_latest_auto_memories(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    tenant_id = uuid4()
    user_id = uuid4()
    async with session_factory() as session:
        await _seed_tenant(session, tenant_id)
        repository = SqlAlchemyMemoryRepository(session)
        manual = _memory(
            tenant_id,
            scope=MemoryScope.USER,
            scope_ref=user_id,
            content="手工记忆",
        )
        await repository.add(manual)
        for index in range(5):
            memory = _memory(
                tenant_id,
                scope=MemoryScope.USER,
                scope_ref=user_id,
                content=f"自动记忆 {index}",
                source=MemorySource.RUN,
                source_ref=f"run-{index}",
            )
            object.__setattr__(
                memory,
                "updated_at",
                datetime.now(UTC) + timedelta(seconds=index),
            )
            await repository.add(memory)
        await session.commit()

    async with session_factory() as session:
        repository = SqlAlchemyMemoryRepository(session)
        removed = await repository.prune_auto_capacity(
            tenant_id=tenant_id,
            scope=MemoryScope.USER,
            scope_ref=user_id,
            capacity=3,
        )
        await session.commit()
        assert removed == 2

    async with session_factory() as session:
        repository = SqlAlchemyMemoryRepository(session)
        rows = await repository.list(tenant_id=tenant_id, visible_to=None)
        contents = {memory.content for memory in rows}
        assert "手工记忆" in contents
        assert contents.issuperset({"自动记忆 4", "自动记忆 3", "自动记忆 2"})
        assert "自动记忆 0" not in contents
        assert "自动记忆 1" not in contents
