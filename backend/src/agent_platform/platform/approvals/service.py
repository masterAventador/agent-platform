"""审批中心平台协议的服务编排（C13）。

统一承载审批决策（批准/拒绝）、转交、撤回与超时过期的业务规则：
- RBAC 服务端校验：指定审批人时仅审批人可决策，未指定时按最低角色要求；
- revision CAS：并发决策只一人生效；
- 超时：决策/转交/撤回路径惰性判定过期并结算，后台清扫兜底；
- Tool 风险审批（C09）联动：决策通过既有 run approve/reject 命令驱动运行时，
  不另起旁路审批通道；过期与撤回驱动 run 拒绝，任务不会永远悬挂；
- 全部动作经 C14 统一审计协议留痕（AuditSink 注入）；
- 系统通知复用平台事件协议（run.progress 事件），通知失败不阻断主流程。

平台层只依赖 ports.py 中的存储/审计协议，具体 SQLAlchemy 实现由
infrastructure 层的 create_approval_service 工厂注入。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import JsonValue

from agent_platform.platform.approvals.entities import (
    Approval,
    ApprovalSource,
    ApprovalStatus,
)
from agent_platform.platform.approvals.errors import (
    ApprovalConcurrencyConflict,
    ApprovalExpired,
    ApprovalInvariantViolation,
    ApprovalNotFound,
    ApprovalNotPending,
    ApprovalPermissionDenied,
    ApprovalReasonRequired,
    ApprovalRunNotActionable,
)
from agent_platform.platform.approvals.ports import (
    ApprovalStore,
    AuditSink,
    RunCommandStore,
    RunEventStore,
    RunStore,
)
from agent_platform.platform.runs.commands import RunCommand, RunCommandAction
from agent_platform.platform.runs.entities import Run, RunStatus
from agent_platform.platform.runs.events import EventType, PlatformEvent
from agent_platform.platform.tenants.memberships import TenantRole
from agent_platform.platform.tenants.permissions import role_at_least

logger = logging.getLogger(__name__)

APPROVAL_EXPIRED_REASON = "approval_expired"
APPROVAL_WITHDRAWN_REASON = "approval_withdrawn"

DecisionAction = Literal["approve", "reject"]


class ApprovalService:
    def __init__(
        self,
        *,
        approvals: ApprovalStore,
        runs: RunStore,
        run_commands: RunCommandStore,
        run_events: RunEventStore,
        audit: AuditSink,
    ) -> None:
        self._approvals = approvals
        self._runs = runs
        self._run_commands = run_commands
        self._run_events = run_events
        self._audit_sink = audit

    async def approve(
        self,
        *,
        tenant_id: UUID,
        approval_id: UUID,
        actor_id: UUID,
        actor_role: TenantRole,
        reason: str | None = None,
        decision_key: UUID | None = None,
    ) -> Approval:
        return await self._decide(
            action="approve",
            tenant_id=tenant_id,
            approval_id=approval_id,
            actor_id=actor_id,
            actor_role=actor_role,
            reason=reason,
            decision_key=decision_key,
        )

    async def reject(
        self,
        *,
        tenant_id: UUID,
        approval_id: UUID,
        actor_id: UUID,
        actor_role: TenantRole,
        reason: str,
        decision_key: UUID | None = None,
    ) -> Approval:
        if not reason or not reason.strip():
            raise ApprovalReasonRequired
        return await self._decide(
            action="reject",
            tenant_id=tenant_id,
            approval_id=approval_id,
            actor_id=actor_id,
            actor_role=actor_role,
            reason=reason.strip(),
            decision_key=decision_key,
        )

    async def withdraw(
        self,
        *,
        tenant_id: UUID,
        approval_id: UUID,
        actor_id: UUID,
        reason: str | None = None,
    ) -> Approval:
        approval = await self._required(tenant_id=tenant_id, approval_id=approval_id)
        if approval.requested_by != actor_id:
            raise ApprovalPermissionDenied
        approval = await self._expire_if_overdue(approval, raise_expired=True)
        if approval.status is not ApprovalStatus.PENDING:
            raise ApprovalNotPending(approval.status.value)
        run = await self._lock_actionable_run(approval)
        withdrawn = approval.withdraw(decided_by=actor_id, reason=reason)
        await self._apply_cas(withdrawn, expected_revision=approval.revision)
        if run is not None:
            await self._dispatch_run_command(
                run=run,
                action="reject",
                approval=withdrawn,
                requested_by=str(actor_id),
                reason=reason or APPROVAL_WITHDRAWN_REASON,
            )
        await self._audit(
            approval=withdrawn,
            actor_user_id=actor_id,
            action="approval.withdrawn",
            metadata={"reason_present": reason is not None},
        )
        return withdrawn

    async def transfer(
        self,
        *,
        tenant_id: UUID,
        approval_id: UUID,
        actor_id: UUID,
        actor_role: TenantRole,
        assignee_id: UUID,
        assignee_role: TenantRole,
        reason: str | None = None,
    ) -> Approval:
        approval = await self._required(tenant_id=tenant_id, approval_id=approval_id)
        self._ensure_can_decide(approval, actor_id=actor_id, actor_role=actor_role)
        if not role_at_least(role=assignee_role, minimum=approval.required_role):
            raise ApprovalPermissionDenied
        approval = await self._expire_if_overdue(approval, raise_expired=True)
        if approval.status is not ApprovalStatus.PENDING:
            raise ApprovalNotPending(approval.status.value)
        transferred, child = approval.transfer(
            decided_by=actor_id,
            assignee_id=assignee_id,
            reason=reason,
        )
        await self._apply_cas(transferred, expected_revision=approval.revision)
        await self._approvals.add_idempotent(child)
        await self._audit(
            approval=transferred,
            actor_user_id=actor_id,
            action="approval.transferred",
            metadata={
                "assignee_id": str(assignee_id),
                "child_approval_id": str(child.id),
            },
        )
        return child

    async def decide_by_invocation(
        self,
        *,
        tenant_id: UUID,
        run_id: UUID,
        invocation_id: UUID,
        action: DecisionAction,
        actor_id: UUID,
        actor_role: TenantRole,
        reason: str | None = None,
    ) -> Approval | None:
        """按 run + invocation 结算审批链上的 pending 记录（run 控制入口复用）。

        返回 None 表示没有对应审批记录（历史/测试直造场景），调用方走原有流程。
        """

        approval = await self._approvals.get_active_for_invocation(
            tenant_id=tenant_id,
            run_id=run_id,
            invocation_id=invocation_id,
        )
        if approval is None:
            latest = await self._approvals.get_latest_for_invocation(
                tenant_id=tenant_id,
                run_id=run_id,
                invocation_id=invocation_id,
            )
            if latest is not None:
                # 链上已有终态记录：不允许经 run 控制入口绕过审批中心重复决策。
                raise ApprovalNotPending(latest.status.value)
            return None
        if action == "reject":
            return await self.reject(
                tenant_id=tenant_id,
                approval_id=approval.id,
                actor_id=actor_id,
                actor_role=actor_role,
                # run 控制入口历史上允许不填理由，这里保底一个稳定标记。
                reason=reason or "rejected_via_run_control",
            )
        return await self.approve(
            tenant_id=tenant_id,
            approval_id=approval.id,
            actor_id=actor_id,
            actor_role=actor_role,
            reason=reason,
        )

    async def _decide(
        self,
        *,
        action: DecisionAction,
        tenant_id: UUID,
        approval_id: UUID,
        actor_id: UUID,
        actor_role: TenantRole,
        reason: str | None,
        decision_key: UUID | None,
    ) -> Approval:
        approval = await self._required(tenant_id=tenant_id, approval_id=approval_id)
        expected_status = (
            ApprovalStatus.APPROVED if action == "approve" else ApprovalStatus.REJECTED
        )
        if (
            decision_key is not None
            and approval.status is expected_status
            and approval.decision_key == decision_key
            and approval.decided_by == actor_id
        ):
            # 同幂等键重复提交：返回原记录，不追加副作用。
            return approval
        self._ensure_can_decide(approval, actor_id=actor_id, actor_role=actor_role)
        approval = await self._expire_if_overdue(approval, raise_expired=True)
        if approval.status is not ApprovalStatus.PENDING:
            raise ApprovalNotPending(approval.status.value)
        run = await self._lock_actionable_run(approval)
        decided = (
            approval.approve(decided_by=actor_id, reason=reason, decision_key=decision_key)
            if action == "approve"
            else approval.reject(
                decided_by=actor_id, reason=reason or "", decision_key=decision_key
            )
        )
        await self._apply_cas(decided, expected_revision=approval.revision)
        if run is not None:
            await self._dispatch_run_command(
                run=run,
                action=action,
                approval=decided,
                requested_by=str(actor_id),
                reason=reason,
            )
        await self._audit(
            approval=decided,
            actor_user_id=actor_id,
            action=f"approval.{decided.status.value}",
            metadata={"reason_present": reason is not None},
        )
        return decided

    async def _required(self, *, tenant_id: UUID, approval_id: UUID) -> Approval:
        approval = await self._approvals.get(tenant_id=tenant_id, approval_id=approval_id)
        if approval is None:
            raise ApprovalNotFound
        return approval

    @staticmethod
    def _ensure_can_decide(
        approval: Approval, *, actor_id: UUID, actor_role: TenantRole
    ) -> None:
        if approval.assignee_id is not None:
            if approval.assignee_id != actor_id:
                raise ApprovalPermissionDenied
            return
        if not role_at_least(role=actor_role, minimum=approval.required_role):
            raise ApprovalPermissionDenied

    async def _expire_if_overdue(
        self, approval: Approval, *, raise_expired: bool
    ) -> Approval:
        if not approval.is_expired(now=datetime.now(UTC)):
            return approval
        expired = await self.settle_expired(approval)
        if raise_expired:
            raise ApprovalExpired
        return expired

    async def settle_expired(self, approval: Approval) -> Approval:
        """把过期的 pending 审批结算为 expired，并驱动 run 拒绝。

        CAS 保证读取时惰性判定与后台清扫并发时只有一方生效；
        run 事件通知失败不阻断结算主流程。
        """

        run = await self._lock_run(approval)
        expired = approval.expire(reason=APPROVAL_EXPIRED_REASON)
        if not await self._approvals.update_with_cas(
            expired, expected_revision=approval.revision
        ):
            stored = await self._approvals.get(
                tenant_id=approval.tenant_id, approval_id=approval.id
            )
            return stored if stored is not None else expired
        if run is not None and run.status is RunStatus.WAITING_FOR_APPROVAL:
            await self._dispatch_run_command(
                run=run,
                action="reject",
                approval=expired,
                requested_by="system:approval-timeout",
                reason=APPROVAL_EXPIRED_REASON,
            )
        await self._audit(
            approval=expired,
            actor_user_id=None,
            action="approval.expired",
            metadata={},
        )
        return expired

    async def _lock_actionable_run(self, approval: Approval) -> Run | None:
        """Tool 审批必须挂在等待审批的 run 上；目标已终态则拒绝操作。"""

        if approval.source is not ApprovalSource.TOOL_RISK or approval.run_id is None:
            return None
        run = await self._runs.get_for_update(
            tenant_id=approval.tenant_id, run_id=approval.run_id
        )
        if run is None or run.status is not RunStatus.WAITING_FOR_APPROVAL:
            raise ApprovalRunNotActionable(run.status.value if run is not None else None)
        return run

    async def _lock_run(self, approval: Approval) -> Run | None:
        if approval.run_id is None:
            return None
        return await self._runs.get_for_update(
            tenant_id=approval.tenant_id, run_id=approval.run_id
        )

    async def _apply_cas(self, approval: Approval, *, expected_revision: int) -> None:
        if not await self._approvals.update_with_cas(
            approval, expected_revision=expected_revision
        ):
            raise ApprovalConcurrencyConflict

    async def _dispatch_run_command(
        self,
        *,
        run: Run,
        action: DecisionAction,
        approval: Approval,
        requested_by: str,
        reason: str | None,
    ) -> None:
        if approval.invocation_id is None:
            # 不变量：绑定 run 的 TOOL_RISK 审批必然带 invocation_id。缺失说明数据异常，
            # 绝不能下发 approval_id="None" 的命令（worker 侧 UUID("None") 会崩）。
            raise ApprovalInvariantViolation(
                f"tool approval {approval.id} bound to run {run.id} has no invocation_id"
            )
        payload: dict[str, JsonValue] = {
            "requested_by": requested_by,
            "approval_id": str(approval.invocation_id),
        }
        if reason is not None:
            payload["reason"] = reason
        await self._run_commands.add(
            RunCommand.create(
                run_id=run.id,
                tenant_id=run.tenant_id,
                action=RunCommandAction(action),
                payload=payload,
            )
        )
        try:
            await self._run_events.append(
                PlatformEvent.create(
                    tenant_id=run.tenant_id,
                    employee_id=run.employee_id,
                    run_id=run.id,
                    sequence=await self._run_events.next_sequence(run_id=run.id),
                    event_type=EventType.RUN_PROGRESS,
                    payload={
                        "action": (
                            "reject_requested" if action == "reject" else action
                        ),
                        **payload,
                    },
                )
            )
        except Exception:
            # 系统通知（平台事件）失败不阻断审批主流程。
            logger.exception(
                "approval_run_event_append_failed",
                extra={"run_id": str(run.id), "approval_id": str(approval.id)},
            )

    async def _audit(
        self,
        *,
        approval: Approval,
        actor_user_id: UUID | None,
        action: str,
        metadata: dict[str, JsonValue],
    ) -> None:
        await self._audit_sink(
            tenant_id=approval.tenant_id,
            actor_user_id=actor_user_id,
            action=action,
            resource_type="approval",
            resource_id=approval.id,
            metadata={
                "source": approval.source.value,
                "approval_type": approval.approval_type,
                "run_id": str(approval.run_id) if approval.run_id else None,
                "invocation_id": (
                    str(approval.invocation_id) if approval.invocation_id else None
                ),
                **metadata,
            },
        )
