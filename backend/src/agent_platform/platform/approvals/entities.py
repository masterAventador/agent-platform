"""C13 独立审批中心的平台级审批协议实体。

Tool 风险审批、后续工作流审批与能力包审批复用同一 Approval 记录与状态机：
pending -> approved / rejected / expired / withdrawn / transferred，其余状态终态。
转交产生新的 pending 记录并与原记录双向链接（转交链），超时不因转交重置。
"""

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import JsonValue

from agent_platform.platform.approvals.errors import InvalidApprovalTransition
from agent_platform.platform.tenants.memberships import TenantRole


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    WITHDRAWN = "withdrawn"
    TRANSFERRED = "transferred"


class ApprovalSource(StrEnum):
    TOOL_RISK = "tool_risk"
    WORKFLOW = "workflow"
    CAPABILITY = "capability"


_ALLOWED_TRANSITIONS: dict[ApprovalStatus, frozenset[ApprovalStatus]] = {
    ApprovalStatus.PENDING: frozenset(
        {
            ApprovalStatus.APPROVED,
            ApprovalStatus.REJECTED,
            ApprovalStatus.EXPIRED,
            ApprovalStatus.WITHDRAWN,
            ApprovalStatus.TRANSFERRED,
        }
    ),
    ApprovalStatus.APPROVED: frozenset(),
    ApprovalStatus.REJECTED: frozenset(),
    ApprovalStatus.EXPIRED: frozenset(),
    ApprovalStatus.WITHDRAWN: frozenset(),
    ApprovalStatus.TRANSFERRED: frozenset(),
}


@dataclass(frozen=True, slots=True)
class Approval:
    id: UUID
    tenant_id: UUID
    source: ApprovalSource
    approval_type: str
    risk_level: str
    requested_by: UUID
    context: dict[str, JsonValue]
    required_role: TenantRole
    status: ApprovalStatus
    revision: int
    created_at: datetime
    updated_at: datetime
    request_key: str | None = None
    run_id: UUID | None = None
    invocation_id: UUID | None = None
    employee_id: UUID | None = None
    assignee_id: UUID | None = None
    expires_at: datetime | None = None
    decided_by: UUID | None = None
    decision_reason: str | None = None
    decided_at: datetime | None = None
    decision_key: UUID | None = None
    transferred_from_id: UUID | None = None
    transferred_to_id: UUID | None = None

    @classmethod
    def create(
        cls,
        *,
        tenant_id: UUID,
        source: ApprovalSource,
        approval_type: str,
        risk_level: str,
        requested_by: UUID,
        context: dict[str, JsonValue],
        request_key: str | None = None,
        required_role: TenantRole = TenantRole.ADMIN,
        run_id: UUID | None = None,
        invocation_id: UUID | None = None,
        employee_id: UUID | None = None,
        assignee_id: UUID | None = None,
        expires_at: datetime | None = None,
        transferred_from_id: UUID | None = None,
    ) -> "Approval":
        now = datetime.now(UTC)
        return cls(
            id=uuid4(),
            tenant_id=tenant_id,
            source=source,
            approval_type=approval_type,
            risk_level=risk_level,
            requested_by=requested_by,
            context=context,
            required_role=required_role,
            status=ApprovalStatus.PENDING,
            revision=1,
            created_at=now,
            updated_at=now,
            request_key=request_key,
            run_id=run_id,
            invocation_id=invocation_id,
            employee_id=employee_id,
            assignee_id=assignee_id,
            expires_at=expires_at,
            transferred_from_id=transferred_from_id,
        )

    def is_expired(self, *, now: datetime) -> bool:
        return (
            self.status is ApprovalStatus.PENDING
            and self.expires_at is not None
            and now >= self.expires_at
        )

    def approve(
        self,
        *,
        decided_by: UUID,
        reason: str | None = None,
        decision_key: UUID | None = None,
    ) -> "Approval":
        return self._decide(
            ApprovalStatus.APPROVED,
            decided_by=decided_by,
            reason=reason,
            decision_key=decision_key,
        )

    def reject(
        self,
        *,
        decided_by: UUID,
        reason: str,
        decision_key: UUID | None = None,
    ) -> "Approval":
        return self._decide(
            ApprovalStatus.REJECTED,
            decided_by=decided_by,
            reason=reason,
            decision_key=decision_key,
        )

    def expire(self, *, reason: str | None = None) -> "Approval":
        return self._decide(ApprovalStatus.EXPIRED, decided_by=None, reason=reason)

    def withdraw(self, *, decided_by: UUID | None, reason: str | None = None) -> "Approval":
        """撤回/系统结算：decided_by 为 None 表示由平台在 run 终态时结算。"""

        return self._decide(ApprovalStatus.WITHDRAWN, decided_by=decided_by, reason=reason)

    def transfer(
        self,
        *,
        decided_by: UUID,
        assignee_id: UUID,
        reason: str | None = None,
    ) -> tuple["Approval", "Approval"]:
        self._ensure_transition(ApprovalStatus.TRANSFERRED)
        child = Approval.create(
            tenant_id=self.tenant_id,
            source=self.source,
            approval_type=self.approval_type,
            risk_level=self.risk_level,
            requested_by=self.requested_by,
            context=self.context,
            # 幂等键只属于最初创建；转交记录靠 transferred_from_id 追溯。
            request_key=None,
            required_role=self.required_role,
            run_id=self.run_id,
            invocation_id=self.invocation_id,
            employee_id=self.employee_id,
            assignee_id=assignee_id,
            # 超时不因转交重置。
            expires_at=self.expires_at,
            transferred_from_id=self.id,
        )
        now = datetime.now(UTC)
        transferred = replace(
            self,
            status=ApprovalStatus.TRANSFERRED,
            decided_by=decided_by,
            decision_reason=reason,
            decided_at=now,
            updated_at=now,
            transferred_to_id=child.id,
            revision=self.revision + 1,
        )
        return transferred, child

    def _decide(
        self,
        status: ApprovalStatus,
        *,
        decided_by: UUID | None,
        reason: str | None,
        decision_key: UUID | None = None,
    ) -> "Approval":
        self._ensure_transition(status)
        now = datetime.now(UTC)
        return replace(
            self,
            status=status,
            decided_by=decided_by,
            decision_reason=reason,
            decided_at=now,
            decision_key=decision_key,
            updated_at=now,
            revision=self.revision + 1,
        )

    def _ensure_transition(self, status: ApprovalStatus) -> None:
        if status not in _ALLOWED_TRANSITIONS[self.status]:
            raise InvalidApprovalTransition(f"{self.status.value} -> {status.value}")
