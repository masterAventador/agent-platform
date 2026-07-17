from enum import StrEnum

from agent_platform.platform.tenants.memberships import TenantRole


class TenantPermission(StrEnum):
    WORKSPACE_MANAGE = "workspace.manage"
    EMPLOYEES_MANAGE = "employees.manage"
    KNOWLEDGE_MANAGE = "knowledge.manage"
    SKILLS_MANAGE = "skills.manage"
    TOOLS_MANAGE = "tools.manage"
    OPERATIONS_MANAGE = "operations.manage"
    RUNS_EXECUTE = "runs.execute"
    RUNS_MANAGE = "runs.manage"
    MODELS_MANAGE = "models.manage"
    MODELS_USAGE_READ = "models.usage.read"


_MANAGER_PERMISSIONS = frozenset(
    {
        TenantPermission.EMPLOYEES_MANAGE,
        TenantPermission.KNOWLEDGE_MANAGE,
        TenantPermission.SKILLS_MANAGE,
        TenantPermission.TOOLS_MANAGE,
        TenantPermission.OPERATIONS_MANAGE,
        TenantPermission.RUNS_EXECUTE,
        TenantPermission.RUNS_MANAGE,
        TenantPermission.MODELS_USAGE_READ,
    }
)
_ROLE_PERMISSIONS: dict[TenantRole, frozenset[TenantPermission]] = {
    TenantRole.OWNER: _MANAGER_PERMISSIONS
    | {TenantPermission.WORKSPACE_MANAGE, TenantPermission.MODELS_MANAGE},
    TenantRole.ADMIN: _MANAGER_PERMISSIONS,
    TenantRole.MEMBER: frozenset({TenantPermission.RUNS_EXECUTE}),
}


def permissions_for_role(role: TenantRole) -> frozenset[TenantPermission]:
    return _ROLE_PERMISSIONS[role]


def role_has_permission(*, role: TenantRole, permission: TenantPermission) -> bool:
    return permission in permissions_for_role(role)


_ROLE_ORDER: dict[TenantRole, int] = {
    TenantRole.MEMBER: 0,
    TenantRole.ADMIN: 1,
    TenantRole.OWNER: 2,
}


def role_at_least(*, role: TenantRole, minimum: TenantRole) -> bool:
    """角色是否满足最低角色要求（owner > admin > member）。"""

    return _ROLE_ORDER[role] >= _ROLE_ORDER[minimum]
