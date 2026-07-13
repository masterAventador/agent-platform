import pytest

from agent_platform.platform.tenants.memberships import TenantRole
from agent_platform.platform.tenants.permissions import (
    TenantPermission,
    permissions_for_role,
    role_has_permission,
)


@pytest.mark.parametrize(
    ("role", "expected"),
    [
        (
            TenantRole.OWNER,
            frozenset(
                {
                    TenantPermission.WORKSPACE_MANAGE,
                    TenantPermission.EMPLOYEES_MANAGE,
                    TenantPermission.KNOWLEDGE_MANAGE,
                    TenantPermission.SKILLS_MANAGE,
                    TenantPermission.TOOLS_MANAGE,
                    TenantPermission.OPERATIONS_MANAGE,
                    TenantPermission.RUNS_EXECUTE,
                    TenantPermission.RUNS_MANAGE,
                }
            ),
        ),
        (
            TenantRole.ADMIN,
            frozenset(
                {
                    TenantPermission.EMPLOYEES_MANAGE,
                    TenantPermission.KNOWLEDGE_MANAGE,
                    TenantPermission.SKILLS_MANAGE,
                    TenantPermission.TOOLS_MANAGE,
                    TenantPermission.OPERATIONS_MANAGE,
                    TenantPermission.RUNS_EXECUTE,
                    TenantPermission.RUNS_MANAGE,
                }
            ),
        ),
        (TenantRole.MEMBER, frozenset({TenantPermission.RUNS_EXECUTE})),
    ],
)
def test_role_permission_matrix_is_explicit_and_complete(
    role: TenantRole,
    expected: frozenset[TenantPermission],
) -> None:
    assert permissions_for_role(role) == expected
    assert {
        permission
        for permission in TenantPermission
        if role_has_permission(role=role, permission=permission)
    } == expected


def test_permission_codes_are_stable_api_values() -> None:
    assert {permission.value for permission in TenantPermission} == {
        "workspace.manage",
        "employees.manage",
        "knowledge.manage",
        "skills.manage",
        "tools.manage",
        "operations.manage",
        "runs.execute",
        "runs.manage",
    }
