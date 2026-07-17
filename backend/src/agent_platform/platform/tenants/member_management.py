"""企业成员角色管理的纯领域规则。

集中 Owner 唯一性保护、最后一个 Owner 保护、自我操作边界与 Owner 转移语义，
不依赖任何仓储或 I/O，便于穷举失败矩阵。所有校验函数在非法时抛出
``agent_platform.platform.tenants.errors`` 中的领域异常，合法时静默返回。
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from uuid import UUID

from agent_platform.platform.tenants.errors import (
    CannotRemoveSelf,
    InvalidRoleAssignment,
    InvitationRoleNotAllowed,
    LastOwnerProtected,
    MembershipNotFound,
)
from agent_platform.platform.tenants.memberships import TenantRole

# 邀请只能直接授予非 Owner 角色；Owner 只能通过显式的 Owner 转移产生。
INVITABLE_ROLES: frozenset[TenantRole] = frozenset({TenantRole.ADMIN, TenantRole.MEMBER})


@dataclass(frozen=True, slots=True)
class MemberSummary:
    user_id: UUID
    role: TenantRole


@dataclass(frozen=True, slots=True)
class RoleChange:
    user_id: UUID
    role: TenantRole


def _find(members: Iterable[MemberSummary], target_id: UUID) -> MemberSummary:
    for member in members:
        if member.user_id == target_id:
            return member
    raise MembershipNotFound


def _owner_ids(members: Iterable[MemberSummary]) -> set[UUID]:
    return {member.user_id for member in members if member.role is TenantRole.OWNER}


def validate_role_change(
    *,
    members: Sequence[MemberSummary],
    target_id: UUID,
    new_role: TenantRole,
) -> None:
    """校验角色变更是否合法；不合法时抛出领域异常。"""

    target = _find(members, target_id)
    if target.role is new_role:
        return
    if (
        target.role is TenantRole.OWNER
        and new_role is not TenantRole.OWNER
        and len(_owner_ids(members)) <= 1
    ):
        raise LastOwnerProtected


def validate_removal(
    *,
    members: Sequence[MemberSummary],
    actor_id: UUID,
    target_id: UUID,
) -> None:
    """校验移除成员是否合法；不合法时抛出领域异常。"""

    target = _find(members, target_id)
    if target_id == actor_id:
        raise CannotRemoveSelf
    if target.role is TenantRole.OWNER and len(_owner_ids(members)) <= 1:
        raise LastOwnerProtected


def plan_owner_transfer(
    *,
    members: Sequence[MemberSummary],
    actor_id: UUID,
    target_id: UUID,
) -> tuple[RoleChange, RoleChange]:
    """规划 Owner 转移：目标升为 Owner，当前 Owner 降为 Admin。"""

    if target_id == actor_id:
        raise InvalidRoleAssignment("不能把 Owner 转移给自己")
    actor = _find(members, actor_id)
    if actor.role is not TenantRole.OWNER:
        raise InvalidRoleAssignment("只有 Owner 才能转移企业所有权")
    _find(members, target_id)
    return (
        RoleChange(user_id=target_id, role=TenantRole.OWNER),
        RoleChange(user_id=actor_id, role=TenantRole.ADMIN),
    )


def ensure_role_is_invitable(role: TenantRole) -> None:
    """邀请只能授予 Admin/Member；拒绝直接邀请为 Owner。"""

    if role not in INVITABLE_ROLES:
        raise InvitationRoleNotAllowed
