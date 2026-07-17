"""真实 PostgreSQL：已是成员时接受邀请不得留下半状态。

SQLite 的 IntegrityError 不复现 PostgreSQL「唯一约束冲突使整个事务 aborted」的
语义；本用例在真实 PG 上验证 memberships.add 移除仓储层 rollback 后的契约：
- 重复接受（用户已是成员）→ 受控 AlreadyMember；
- 冲突后同一 session 未 rollback 即复用会因事务 aborted 而报错（证明调用方必须终止事务）；
- 调用方 rollback 后，新事务里邀请仍 PENDING、成员不重复，无半提交状态。

缺少 ``TEST_DATABASE_URL`` 时条件跳过，遵循仓库既有 PG 门禁模式。
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_platform.infrastructure.database.repositories.auth import UserRecord
from agent_platform.infrastructure.database.repositories.invitations import (
    SqlAlchemyInvitationRepository,
    TenantInvitationRecord,
)
from agent_platform.infrastructure.database.repositories.memberships import (
    SqlAlchemyMembershipRepository,
)
from agent_platform.infrastructure.database.repositories.tenants import (
    SqlAlchemyTenantRepository,
    TenantMembershipRecord,
)
from agent_platform.platform.tenants.entities import Tenant
from agent_platform.platform.tenants.errors import AlreadyMember
from agent_platform.platform.tenants.invitations import (
    InvitationStatus,
    TenantInvitation,
)
from agent_platform.platform.tenants.memberships import TenantRole

BACKEND_ROOT = Path(__file__).parents[3]


@pytest.fixture(scope="module")
def migrated_postgres_url() -> str:
    database_url = os.getenv("TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("需要 TEST_DATABASE_URL 才运行真实 PostgreSQL 邀请接受门禁")
    config = Config(BACKEND_ROOT / "alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    return database_url


@pytest.mark.asyncio
async def test_accepting_when_already_member_leaves_no_half_state_on_postgres(
    migrated_postgres_url: str,
) -> None:
    engine = create_async_engine(migrated_postgres_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    tenant = Tenant.create(name="Invitation half-state", slug=f"inv-half-{uuid4().hex}")
    inviter_id = uuid4()
    invitee_id = uuid4()
    invitee_email = f"{invitee_id.hex}@example.com"
    token_digest = uuid4().hex

    async with session_factory() as session:
        await SqlAlchemyTenantRepository(session).add(tenant)
        for user_id, email in (
            (inviter_id, f"owner-{inviter_id.hex}@example.com"),
            (invitee_id, invitee_email),
        ):
            session.add(
                UserRecord(
                    id=user_id,
                    email=email,
                    password_hash="x",
                    email_verified=True,
                    created_at=datetime.now(UTC),
                )
            )
        # 被邀请者已经是该企业成员。
        session.add(
            TenantMembershipRecord(
                id=uuid4(),
                tenant_id=tenant.id,
                user_id=invitee_id,
                role=TenantRole.MEMBER.value,
                created_at=datetime.now(UTC),
            )
        )
        # 一封仍 PENDING 的邀请（token 摘要已知）。
        await SqlAlchemyInvitationRepository(session).add(
            TenantInvitation.issue(
                tenant_id=tenant.id,
                email=invitee_email,
                role=TenantRole.ADMIN,
                token_digest=token_digest,
                invited_by=inviter_id,
                ttl_seconds=7 * 24 * 3600,
            )
        )
        await session.commit()

    # 复刻 accept_invitation 临界区：save(ACCEPTED) 后 membership.add 冲突。
    async with session_factory() as session:
        invitations = SqlAlchemyInvitationRepository(session)
        invitation = await invitations.get_by_token_digest_for_update(token_digest)
        assert invitation is not None
        accepted = invitation.accept(
            user_id=invitee_id,
            user_email=invitee_email,
            now=datetime.now(UTC),
        )
        await invitations.save(accepted)  # flush：邀请状态在本事务内改为 accepted
        with pytest.raises(AlreadyMember):
            await SqlAlchemyMembershipRepository(session).add(
                tenant_id=tenant.id,
                user_id=invitee_id,
                role=TenantRole.ADMIN,
            )
        # PG：冲突后事务整体 aborted，未 rollback 即复用 session 必然报错，
        # 证明调用方必须终止该事务、不得复用 session。
        with pytest.raises(SQLAlchemyError):
            await session.execute(select(TenantInvitationRecord.id))
        # 调用方终止事务（等价于路由 async with session 退出时的回滚）。
        await session.rollback()

    # 新事务：邀请仍 PENDING、成员未重复，无半提交状态。
    async with session_factory() as session:
        refreshed = await SqlAlchemyInvitationRepository(session).get_for_update(
            tenant_id=tenant.id,
            invitation_id=accepted.id,
        )
        assert refreshed is not None
        assert refreshed.status is InvitationStatus.PENDING
        membership_count = (
            await session.execute(
                select(func.count())
                .select_from(TenantMembershipRecord)
                .where(
                    TenantMembershipRecord.tenant_id == tenant.id,
                    TenantMembershipRecord.user_id == invitee_id,
                )
            )
        ).scalar_one()
        assert membership_count == 1

    await engine.dispose()
