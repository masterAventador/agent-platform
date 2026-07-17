"""企业成员仓储：成员明细、行锁快照、角色变更、移除与新增。

角色变更/移除/Owner 转移前必须通过 ``lock_members`` 取得该租户全部成员的
``FOR UPDATE`` 快照，再在锁内运行 ``member_management`` 的领域校验并落库，
从而在真实 PostgreSQL 上消除并发角色变更绕过最后一个 Owner 保护的竞态。
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from agent_platform.infrastructure.database.repositories.auth import UserRecord
from agent_platform.infrastructure.database.repositories.tenants import (
    TenantMembershipRecord,
)
from agent_platform.platform.tenants.errors import AlreadyMember
from agent_platform.platform.tenants.member_management import MemberSummary
from agent_platform.platform.tenants.memberships import TenantRole


@dataclass(frozen=True, slots=True)
class MemberDetail:
    user_id: UUID
    membership_id: UUID
    email: str
    display_name: str | None
    role: TenantRole
    joined_at: datetime


class SqlAlchemyMembershipRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_members(self, tenant_id: UUID) -> list[MemberDetail]:
        result = await self._session.execute(
            select(TenantMembershipRecord, UserRecord)
            .join(UserRecord, UserRecord.id == TenantMembershipRecord.user_id)
            .where(TenantMembershipRecord.tenant_id == tenant_id)
            .order_by(TenantMembershipRecord.created_at)
        )
        return [
            MemberDetail(
                user_id=membership.user_id,
                membership_id=membership.id,
                email=user.email,
                display_name=user.display_name,
                role=TenantRole(membership.role),
                joined_at=_as_utc(membership.created_at),
            )
            for membership, user in result.all()
        ]

    async def lock_members(self, tenant_id: UUID) -> list[MemberSummary]:
        result = await self._session.execute(
            select(TenantMembershipRecord)
            .where(TenantMembershipRecord.tenant_id == tenant_id)
            .order_by(TenantMembershipRecord.created_at)
            .with_for_update()
        )
        return [
            MemberSummary(user_id=record.user_id, role=TenantRole(record.role))
            for record in result.scalars().all()
        ]

    async def get_summary(self, *, tenant_id: UUID, user_id: UUID) -> MemberSummary | None:
        result = await self._session.execute(
            select(TenantMembershipRecord).where(
                TenantMembershipRecord.tenant_id == tenant_id,
                TenantMembershipRecord.user_id == user_id,
            )
        )
        record = result.scalar_one_or_none()
        if record is None:
            return None
        return MemberSummary(user_id=record.user_id, role=TenantRole(record.role))

    async def set_role(self, *, tenant_id: UUID, user_id: UUID, role: TenantRole) -> None:
        await self._session.execute(
            update(TenantMembershipRecord)
            .where(
                TenantMembershipRecord.tenant_id == tenant_id,
                TenantMembershipRecord.user_id == user_id,
            )
            .values(role=role.value)
        )
        await self._session.flush()

    async def remove(self, *, tenant_id: UUID, user_id: UUID) -> None:
        await self._session.execute(
            delete(TenantMembershipRecord).where(
                TenantMembershipRecord.tenant_id == tenant_id,
                TenantMembershipRecord.user_id == user_id,
            )
        )
        await self._session.flush()

    async def add(self, *, tenant_id: UUID, user_id: UUID, role: TenantRole) -> None:
        """新增成员；(tenant_id, user_id) 已存在时抛 ``AlreadyMember``。

        契约：本方法不回滚共享 session（回滚边界归调用方），只抛领域错误，避免连带
        回滚调用方同事务中已 flush 的其它改动（如邀请状态）。在 PostgreSQL 上，唯一
        约束冲突会使**整个事务进入 aborted 状态**——调用方 catch ``AlreadyMember`` 后
        必须终止该事务（rollback / 退出 session 上下文），**不得在同一 session 上继续
        执行任何语句**（否则报 InFailedSqlTransaction）。当前调用方 ``accept_invitation``
        捕获后立即抛 HTTPException，由 ``async with session`` 退出时回滚，符合该契约。
        """

        self._session.add(
            TenantMembershipRecord(
                id=uuid4(),
                tenant_id=tenant_id,
                user_id=user_id,
                role=role.value,
                created_at=datetime.now(UTC),
            )
        )
        try:
            await self._session.flush()
        except IntegrityError as error:
            raise AlreadyMember from error


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
