from __future__ import annotations

from agent_platform.platform.tools.entities import ToolApprovalPolicy

from .models import (
    PolicyContext,
    PolicyDecision,
    PolicyOutcome,
    ToolDefinition,
    ToolInvocation,
    ToolRisk,
)

_APPROVAL_RISKS = frozenset({ToolRisk.EXTERNAL, ToolRisk.DESTRUCTIVE})


def _requires_approval(definition: ToolDefinition) -> bool:
    # 纵深防御：destructive 永远要求审批，即使数据层被绕过写入 never。
    if definition.risk is ToolRisk.DESTRUCTIVE:
        return True
    if definition.approval_policy is ToolApprovalPolicy.ALWAYS:
        return True
    if definition.approval_policy is ToolApprovalPolicy.NEVER:
        return False
    return definition.risk in _APPROVAL_RISKS


def evaluate_policy(
    invocation: ToolInvocation,
    definition: ToolDefinition,
    context: PolicyContext,
) -> PolicyOutcome:
    if invocation.tenant_id != definition.tenant_id:
        return PolicyOutcome(PolicyDecision.DENY, "tenant_mismatch")
    if invocation.tool_id != definition.tool_id:
        return PolicyOutcome(PolicyDecision.DENY, "tool_id_mismatch")
    if invocation.tool_name != definition.name:
        return PolicyOutcome(PolicyDecision.DENY, "tool_mismatch")
    if not definition.enabled:
        return PolicyOutcome(PolicyDecision.DENY, "tool_disabled")
    if not definition.server_enabled:
        return PolicyOutcome(PolicyDecision.DENY, "server_disabled")
    if definition.upstream_missing:
        return PolicyOutcome(PolicyDecision.DENY, "tool_upstream_missing")
    if invocation.tool_id not in context.allowed_tool_ids:
        return PolicyOutcome(PolicyDecision.DENY, "tool_not_allowed")
    if _requires_approval(definition) and not context.approval_granted:
        return PolicyOutcome(PolicyDecision.REQUIRE_APPROVAL, "approval_required")
    return PolicyOutcome(PolicyDecision.ALLOW)
