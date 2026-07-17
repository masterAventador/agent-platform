"""成员仓储 add 的回滚边界：仓储层不得回滚共享 session。

重复接受邀请（用户已是成员）时，`add` 只应抛领域错误 AlreadyMember，把事务
回滚边界交给调用方（路由），避免在共享 session 中回滚掉调用方同事务里的其它已
flush 改动。
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
from agent_platform.infrastructure.database.repositories.auth import UserRecord
from agent_platform.infrastructure.database.repositories.memberships import (
    SqlAlchemyMembershipRepository,
)
from agent_platform.infrastructure.database.repositories.tenants import (
    TenantMembershipRecord,
    TenantRecord,
)
from agent_platform.platform.tenants.errors import AlreadyMember
from agent_platform.platform.tenants.memberships import TenantRole


@pytest_asyncio.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    load_database_models()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_add_duplicate_member_raises_without_rolling_back_shared_session(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    tenant_id = uuid4()
    user_id = uuid4()
    async with session_factory() as session:
        session.add(
            TenantRecord(
                id=tenant_id,
                name="租户",
                slug=f"t-{tenant_id.hex}",
                created_at=datetime.now(UTC),
            )
        )
        session.add(
            UserRecord(
                id=user_id,
                email=f"{user_id.hex}@example.com",
                password_hash="x",
                email_verified=True,
                created_at=datetime.now(UTC),
            )
        )
        await session.flush()
        session.add(
            TenantMembershipRecord(
                id=uuid4(),
                tenant_id=tenant_id,
                user_id=user_id,
                role=TenantRole.MEMBER.value,
                created_at=datetime.now(UTC),
            )
        )
        await session.commit()

    async with session_factory() as session:
        repository = SqlAlchemyMembershipRepository(session)
        rollback_calls = 0
        original_rollback = session.rollback

        async def counting_rollback() -> None:
            nonlocal rollback_calls
            rollback_calls += 1
            await original_rollback()

        session.rollback = counting_rollback  # type: ignore[method-assign]

        with pytest.raises(AlreadyMember):
            await repository.add(
                tenant_id=tenant_id,
                user_id=user_id,
                role=TenantRole.MEMBER,
            )
        # 仓储不得回滚共享 session：回滚边界归调用方。
        assert rollback_calls == 0
