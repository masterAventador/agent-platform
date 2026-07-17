"""Approval 领域实体与状态机单元测试（C13）。

状态机：pending -> approved / rejected / expired / withdrawn / transferred；
其余状态全部终态。穷举全部合法与非法转换。
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from agent_platform.platform.approvals.entities import (
    Approval,
    ApprovalSource,
    ApprovalStatus,
)
from agent_platform.platform.approvals.errors import InvalidApprovalTransition
from agent_platform.platform.tenants.memberships import TenantRole


def _pending_approval(**overrides: object) -> Approval:
    defaults: dict[str, object] = {
        "tenant_id": uuid4(),
        "source": ApprovalSource.TOOL_RISK,
        "approval_type": "tool.invocation",
        "risk_level": "external",
        "requested_by": uuid4(),
        "request_key": f"tool:{uuid4()}:{uuid4()}",
        "context": {"tool_name": "send_email", "arguments": {"to": "a@b.c"}},
        "run_id": uuid4(),
        "invocation_id": uuid4(),
        "employee_id": uuid4(),
        "expires_at": datetime.now(UTC) + timedelta(hours=1),
    }
    defaults.update(overrides)
    return Approval.create(**defaults)  # type: ignore[arg-type]


def test_create_produces_pending_approval_with_revision_one() -> None:
    approval = _pending_approval()

    assert approval.status is ApprovalStatus.PENDING
    assert approval.revision == 1
    assert approval.required_role is TenantRole.ADMIN
    assert approval.assignee_id is None
    assert approval.decided_by is None
    assert approval.decided_at is None
    assert approval.created_at.tzinfo is not None


def test_approve_transitions_to_approved_and_records_decision() -> None:
    approval = _pending_approval()
    decider = uuid4()

    approved = approval.approve(decided_by=decider, reason="风险可接受")

    assert approved.status is ApprovalStatus.APPROVED
    assert approved.decided_by == decider
    assert approved.decision_reason == "风险可接受"
    assert approved.decided_at is not None
    assert approved.revision == approval.revision + 1


def test_reject_transitions_to_rejected_with_reason() -> None:
    approval = _pending_approval()
    decider = uuid4()

    rejected = approval.reject(decided_by=decider, reason="不允许外部发送")

    assert rejected.status is ApprovalStatus.REJECTED
    assert rejected.decision_reason == "不允许外部发送"
    assert rejected.decided_by == decider
    assert rejected.revision == approval.revision + 1


def test_expire_transitions_to_expired_without_decider() -> None:
    approval = _pending_approval()

    expired = approval.expire()

    assert expired.status is ApprovalStatus.EXPIRED
    assert expired.decided_by is None
    assert expired.decided_at is not None


def test_withdraw_transitions_to_withdrawn() -> None:
    approval = _pending_approval()
    requester = approval.requested_by

    withdrawn = approval.withdraw(decided_by=requester, reason="不需要了")

    assert withdrawn.status is ApprovalStatus.WITHDRAWN
    assert withdrawn.decided_by == requester
    assert withdrawn.decision_reason == "不需要了"


def test_transfer_marks_original_transferred_and_creates_new_pending() -> None:
    approval = _pending_approval()
    transferer = uuid4()
    new_assignee = uuid4()

    transferred, child = approval.transfer(
        decided_by=transferer,
        assignee_id=new_assignee,
        reason="转给更熟悉的人",
    )

    assert transferred.status is ApprovalStatus.TRANSFERRED
    assert transferred.decided_by == transferer
    assert transferred.transferred_to_id == child.id
    assert child.id != approval.id
    assert child.status is ApprovalStatus.PENDING
    assert child.assignee_id == new_assignee
    assert child.transferred_from_id == approval.id
    assert child.revision == 1
    # 转交链继承业务上下文与来源引用
    assert child.run_id == approval.run_id
    assert child.invocation_id == approval.invocation_id
    assert child.context == approval.context
    assert child.requested_by == approval.requested_by
    # 超时不因转交重置
    assert child.expires_at == approval.expires_at
    # 幂等键只属于最初创建，转交产生的新记录不复用
    assert child.request_key is None


_TERMINAL_STATUSES = (
    ApprovalStatus.APPROVED,
    ApprovalStatus.REJECTED,
    ApprovalStatus.EXPIRED,
    ApprovalStatus.WITHDRAWN,
    ApprovalStatus.TRANSFERRED,
)


def _force_status(approval: Approval, status: ApprovalStatus) -> Approval:
    if status is ApprovalStatus.PENDING:
        return approval
    if status is ApprovalStatus.APPROVED:
        return approval.approve(decided_by=uuid4())
    if status is ApprovalStatus.REJECTED:
        return approval.reject(decided_by=uuid4(), reason="no")
    if status is ApprovalStatus.EXPIRED:
        return approval.expire()
    if status is ApprovalStatus.WITHDRAWN:
        return approval.withdraw(decided_by=uuid4())
    return approval.transfer(decided_by=uuid4(), assignee_id=uuid4())[0]


@pytest.mark.parametrize("terminal", _TERMINAL_STATUSES)
@pytest.mark.parametrize(
    "action",
    ["approve", "reject", "expire", "withdraw", "transfer"],
)
def test_terminal_statuses_reject_all_transitions(
    terminal: ApprovalStatus, action: str
) -> None:
    approval = _force_status(_pending_approval(), terminal)

    with pytest.raises(InvalidApprovalTransition):
        if action == "approve":
            approval.approve(decided_by=uuid4())
        elif action == "reject":
            approval.reject(decided_by=uuid4(), reason="no")
        elif action == "expire":
            approval.expire()
        elif action == "withdraw":
            approval.withdraw(decided_by=uuid4())
        else:
            approval.transfer(decided_by=uuid4(), assignee_id=uuid4())


def test_is_expired_only_for_overdue_pending() -> None:
    now = datetime.now(UTC)
    overdue = _pending_approval(expires_at=now - timedelta(seconds=1))
    fresh = _pending_approval(expires_at=now + timedelta(hours=1))
    never = _pending_approval(expires_at=None)

    assert overdue.is_expired(now=now) is True
    assert fresh.is_expired(now=now) is False
    assert never.is_expired(now=now) is False
    # 已终态的记录即使超过 expires_at 也不再算“待过期”
    assert overdue.approve(decided_by=uuid4()).is_expired(now=now) is False
