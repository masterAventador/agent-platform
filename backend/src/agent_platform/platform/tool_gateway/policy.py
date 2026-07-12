from __future__ import annotations

from .models import (
    PolicyContext,
    PolicyDecision,
    PolicyOutcome,
    ToolDefinition,
    ToolInvocation,
    ToolRisk,
)

_APPROVAL_RISKS = frozenset({ToolRisk.EXTERNAL, ToolRisk.DESTRUCTIVE})


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
    if invocation.tool_id not in context.allowed_tool_ids:
        return PolicyOutcome(PolicyDecision.DENY, "tool_not_allowed")
    if definition.risk in _APPROVAL_RISKS and not context.approval_granted:
        return PolicyOutcome(PolicyDecision.REQUIRE_APPROVAL, "approval_required")
    return PolicyOutcome(PolicyDecision.ALLOW)
