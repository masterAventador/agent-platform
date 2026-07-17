class InvalidApprovalTransition(Exception):
    """审批记录状态机不允许的转换。"""


class ApprovalConcurrencyConflict(Exception):
    """并发决策导致的 revision CAS 冲突，仅一人生效。"""


class ApprovalNotFound(Exception):
    """审批记录不存在或不属于当前租户。"""


class ApprovalPermissionDenied(Exception):
    """当前用户不是该审批的合法审批人/发起人。"""


class ApprovalNotPending(Exception):
    """审批记录已处于终态，不接受该操作。"""

    def __init__(self, status: str) -> None:
        self.status = status
        super().__init__(status)


class ApprovalExpired(Exception):
    """审批已超时过期，不接受决策。"""


class ApprovalReasonRequired(Exception):
    """拒绝审批必须填写理由。"""


class ApprovalRunNotActionable(Exception):
    """审批目标 run 缺失或已不在等待审批状态。"""

    def __init__(self, run_status: str | None) -> None:
        self.run_status = run_status
        super().__init__(run_status)


class ApprovalRecordMissing(Exception):
    """run 已进等待审批态，但按 run+invocation 查无审批记录（fail-closed）。"""


class ApprovalInvariantViolation(Exception):
    """审批记录违反其类型不变量（如 TOOL_RISK+run 审批却缺 invocation_id）。"""
