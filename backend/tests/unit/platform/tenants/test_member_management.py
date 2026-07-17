from uuid import uuid4

import pytest

from agent_platform.platform.tenants.errors import (
    CannotRemoveSelf,
    InvalidRoleAssignment,
    LastOwnerProtected,
    MembershipNotFound,
)
from agent_platform.platform.tenants.member_management import (
    MemberSummary,
    RoleChange,
    plan_owner_transfer,
    validate_removal,
    validate_role_change,
)
from agent_platform.platform.tenants.memberships import TenantRole

OWNER_A = uuid4()
OWNER_B = uuid4()
ADMIN = uuid4()
MEMBER = uuid4()


def _members(*roles: tuple) -> list[MemberSummary]:
    return [MemberSummary(user_id=user_id, role=role) for user_id, role in roles]


DEFAULT = _members(
    (OWNER_A, TenantRole.OWNER),
    (ADMIN, TenantRole.ADMIN),
    (MEMBER, TenantRole.MEMBER),
)


# --- validate_role_change ---


def test_promote_member_to_admin_is_allowed() -> None:
    validate_role_change(members=DEFAULT, target_id=MEMBER, new_role=TenantRole.ADMIN)


def test_change_role_on_unknown_member_raises() -> None:
    with pytest.raises(MembershipNotFound):
        validate_role_change(members=DEFAULT, target_id=uuid4(), new_role=TenantRole.ADMIN)


def test_demoting_the_only_owner_is_blocked() -> None:
    with pytest.raises(LastOwnerProtected):
        validate_role_change(members=DEFAULT, target_id=OWNER_A, new_role=TenantRole.ADMIN)


def test_demoting_an_owner_when_another_owner_exists_is_allowed() -> None:
    members = _members(
        (OWNER_A, TenantRole.OWNER),
        (OWNER_B, TenantRole.OWNER),
    )
    validate_role_change(members=members, target_id=OWNER_A, new_role=TenantRole.ADMIN)


def test_no_op_role_change_on_last_owner_is_allowed() -> None:
    validate_role_change(members=DEFAULT, target_id=OWNER_A, new_role=TenantRole.OWNER)


# --- validate_removal ---


def test_removing_a_regular_member_is_allowed() -> None:
    validate_removal(members=DEFAULT, actor_id=OWNER_A, target_id=MEMBER)


def test_cannot_remove_self() -> None:
    with pytest.raises(CannotRemoveSelf):
        validate_removal(members=DEFAULT, actor_id=OWNER_A, target_id=OWNER_A)


def test_removing_unknown_member_raises() -> None:
    with pytest.raises(MembershipNotFound):
        validate_removal(members=DEFAULT, actor_id=OWNER_A, target_id=uuid4())


def test_cannot_remove_the_last_owner() -> None:
    members = _members(
        (OWNER_A, TenantRole.OWNER),
        (OWNER_B, TenantRole.OWNER),
    )
    # actor is OWNER_B removing OWNER_A while a second owner exists -> allowed
    validate_removal(members=members, actor_id=OWNER_B, target_id=OWNER_A)


def test_removing_last_owner_by_another_actor_is_blocked() -> None:
    members = _members(
        (OWNER_A, TenantRole.OWNER),
        (ADMIN, TenantRole.ADMIN),
    )
    with pytest.raises(LastOwnerProtected):
        validate_removal(members=members, actor_id=ADMIN, target_id=OWNER_A)


# --- plan_owner_transfer ---


def test_owner_transfer_promotes_target_and_demotes_actor() -> None:
    changes = plan_owner_transfer(members=DEFAULT, actor_id=OWNER_A, target_id=ADMIN)
    assert set(changes) == {
        RoleChange(user_id=ADMIN, role=TenantRole.OWNER),
        RoleChange(user_id=OWNER_A, role=TenantRole.ADMIN),
    }


def test_owner_transfer_to_self_is_rejected() -> None:
    with pytest.raises(InvalidRoleAssignment):
        plan_owner_transfer(members=DEFAULT, actor_id=OWNER_A, target_id=OWNER_A)


def test_owner_transfer_by_non_owner_is_rejected() -> None:
    with pytest.raises(InvalidRoleAssignment):
        plan_owner_transfer(members=DEFAULT, actor_id=ADMIN, target_id=MEMBER)


def test_owner_transfer_to_unknown_member_raises() -> None:
    with pytest.raises(MembershipNotFound):
        plan_owner_transfer(members=DEFAULT, actor_id=OWNER_A, target_id=uuid4())
