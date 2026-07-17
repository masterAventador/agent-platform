class TenantSlugAlreadyExists(Exception):
    """租户标识已存在。"""


class MembershipNotFound(Exception):
    """目标成员不在该企业中。"""


class LastOwnerProtected(Exception):
    """企业必须至少保留一个 Owner，不能移除或降级最后一个 Owner。"""


class CannotRemoveSelf(Exception):
    """不能通过成员管理接口移除自己。"""


class InvalidRoleAssignment(Exception):
    """非法的角色赋值。"""

    def __init__(self, message: str = "非法的角色赋值") -> None:
        super().__init__(message)


class AlreadyMember(Exception):
    """用户已经是该企业成员。"""


class InvitationNotFound(Exception):
    """邀请不存在。"""


class InvitationNotPending(Exception):
    """邀请已被处理（接受/拒绝/撤销）或已过期。"""


class InvitationExpired(Exception):
    """邀请已过期。"""


class InvitationEmailMismatch(Exception):
    """当前用户邮箱与邀请邮箱不一致。"""


class InvitationRoleNotAllowed(Exception):
    """邀请不允许直接授予该角色。"""
