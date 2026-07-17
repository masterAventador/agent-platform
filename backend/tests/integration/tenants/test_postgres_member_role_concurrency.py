"""真实 PostgreSQL 下并发角色变更不得绕过最后一个 Owner 保护。

SQLite 的 ``with_for_update`` 是 no-op，无法证明两个并发降级请求不会同时读到
"仍有 2 个 Owner" 而把租户降到 0 个 Owner。本文件按仓库既有 PG 门禁模式，
缺少 ``TEST_DATABASE_URL`` 时条件跳过。
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_platform.infrastructure.database.repositories.auth import UserRecord
from agent_platform.infrastructure.database.repositories.memberships import (
    SqlAlchemyMembershipRepository,
)
from agent_platform.infrastructure.database.repositories.tenants import (
    SqlAlchemyTenantRepository,
    TenantMembershipRecord,
)
from agent_platform.platform.tenants.entities import Tenant
from agent_platform.platform.tenants.errors import LastOwnerProtected
from agent_platform.platform.tenants.member_management import validate_role_change
from agent_platform.platform.tenants.memberships import TenantRole

BACKEND_ROOT = Path(__file__).parents[3]


@pytest.fixture(scope="module")
def migrated_postgres_url() -> str:
    database_url = os.getenv("TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("需要 TEST_DATABASE_URL 才运行真实 PostgreSQL 成员并发测试")
    config = Config(BACKEND_ROOT / "alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    return database_url


@pytest.mark.asyncio
async def test_concurrent_owner_demotions_cannot_drop_below_one_owner(
    migrated_postgres_url: str,
) -> None:
    engine = create_async_engine(migrated_postgres_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    tenant = Tenant.create(name="Member concurrency", slug=f"member-cas-{uuid4().hex}")
    owner_a = uuid4()
    owner_b = uuid4()
    async with session_factory() as session:
        await SqlAlchemyTenantRepository(session).add(tenant)
        for owner_id in (owner_a, owner_b):
            session.add(
                UserRecord(
                    id=owner_id,
                    email=f"{owner_id.hex}@example.com",
                    password_hash="x",
                    email_verified=True,
                    created_at=datetime.now(UTC),
                )
            )
            session.add(
                TenantMembershipRecord(
                    id=uuid4(),
                    tenant_id=tenant.id,
                    user_id=owner_id,
                    role=TenantRole.OWNER.value,
                    created_at=datetime.now(UTC),
                )
            )
        await session.commit()

    async def demote(target_id: UUID) -> str:
        # 复刻路由临界区：锁租户成员行 → 领域校验 → 落库 → 提交。
        async with session_factory() as session:
            repository = SqlAlchemyMembershipRepository(session)
            locked = await repository.lock_members(tenant.id)
            try:
                validate_role_change(
                    members=locked,
                    target_id=target_id,
                    new_role=TenantRole.ADMIN,
                )
            except LastOwnerProtected:
                await session.rollback()
                return "protected"
            await repository.set_role(
                tenant_id=tenant.id,
                user_id=target_id,
                role=TenantRole.ADMIN,
            )
            await session.commit()
            return "demoted"

    results = await asyncio.gather(demote(owner_a), demote(owner_b))

    assert sorted(results) == ["demoted", "protected"], results

    async with session_factory() as session:
        members = await SqlAlchemyMembershipRepository(session).list_members(tenant.id)
    owners = [m for m in members if m.role is TenantRole.OWNER]
    assert len(owners) == 1

    await engine.dispose()
