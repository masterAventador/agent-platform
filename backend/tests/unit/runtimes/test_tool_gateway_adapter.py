from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from langchain_core.tools import StructuredTool, ToolException

from agent_platform.platform.tool_gateway import (
    PolicyContext,
    PolicyDecision,
    ToolExecutionError,
    ToolInvocation,
    ToolInvocationOutcome,
)
from agent_platform.platform.tools.entities import Tool, ToolRiskLevel
from agent_platform.runtimes.tool_gateway_adapter import (
    InvocationContext,
    OneTimeToolApprovalStore,
    ToolApprovalRequired,
    ToolExecutionBlocked,
    ToolGatewayAdapter,
)


@dataclass
class RecordingGateway:
    outcome: ToolInvocationOutcome
    calls: list[tuple[ToolInvocation, PolicyContext]] = field(default_factory=list)

    async def invoke(
        self,
        invocation: ToolInvocation,
        context: PolicyContext,
    ) -> ToolInvocationOutcome:
        self.calls.append((invocation, context))
        return self.outcome


class FailingGateway:
    async def invoke(
        self,
        invocation: ToolInvocation,
        context: PolicyContext,
    ) -> ToolInvocationOutcome:
        del invocation, context
        raise ToolExecutionError("safe gateway failure")


class BlockingExecutionGuard:
    async def assert_allowed(self) -> None:
        raise ToolExecutionBlocked("run_cancellation_requested")


def registry_tool() -> Tool:
    now = datetime.now(UTC)
    return Tool(
        id=uuid4(),
        tenant_id=uuid4(),
        server_id=uuid4(),
        name="crm.lookup",
        description="Look up one CRM customer.",
        input_schema={
            "type": "object",
            "properties": {"customer_id": {"type": "integer"}},
            "required": ["customer_id"],
            "additionalProperties": False,
        },
        risk_level=ToolRiskLevel.READ,
        enabled=True,
        created_at=now,
        updated_at=now,
    )


def invocation_context(*, tenant_id: UUID) -> InvocationContext:
    return InvocationContext(
        tenant_id=tenant_id,
        run_id=uuid4(),
        employee_id=uuid4(),
        user_id=uuid4(),
    )


@pytest.mark.asyncio
async def test_adapts_registry_metadata_and_binds_trusted_context() -> None:
    metadata = registry_tool()
    trusted = invocation_context(tenant_id=metadata.tenant_id)
    policy = PolicyContext(
        allowed_tool_ids=frozenset({metadata.id}),
        approval_granted=False,
    )
    gateway = RecordingGateway(
        ToolInvocationOutcome(
            decision=PolicyDecision.ALLOW,
            result={"customer": "Ada"},
        )
    )

    tool = ToolGatewayAdapter(
        gateway=gateway,
        invocation_context=trusted,
        policy_context=policy,
    ).adapt(metadata)

    assert isinstance(tool, StructuredTool)
    assert tool.name == metadata.name
    assert tool.description == metadata.description
    assert tool.args_schema == metadata.input_schema
    assert await tool.ainvoke({"customer_id": 42}) == {"customer": "Ada"}
    assert len(gateway.calls) == 1
    captured_invocation, captured_policy = gateway.calls[0]
    assert captured_policy == policy
    assert captured_invocation == ToolInvocation(
        tenant_id=trusted.tenant_id,
        run_id=trusted.run_id,
        employee_id=trusted.employee_id,
        user_id=trusted.user_id,
        tool_id=metadata.id,
        tool_name=metadata.name,
        arguments={"customer_id": 42},
        invocation_id=captured_invocation.invocation_id,
    )
    assert captured_invocation.invocation_id is not None


@pytest.mark.asyncio
async def test_model_cannot_override_identity_or_policy_through_arguments() -> None:
    metadata = registry_tool()
    gateway = RecordingGateway(
        ToolInvocationOutcome(decision=PolicyDecision.ALLOW, result={"ok": True})
    )
    tool = ToolGatewayAdapter(
        gateway=gateway,
        invocation_context=invocation_context(tenant_id=metadata.tenant_id),
        policy_context=PolicyContext(
            allowed_tool_ids=frozenset({metadata.id}),
            approval_granted=False,
        ),
    ).adapt(metadata)

    untrusted_tenant_id = str(uuid4())
    await tool.ainvoke(
        {
            "customer_id": 42,
            "tenant_id": untrusted_tenant_id,
            "approval_granted": True,
        }
    )

    invocation, received_policy = gateway.calls[0]
    assert invocation.tenant_id == metadata.tenant_id
    assert invocation.arguments == {
        "customer_id": 42,
        "tenant_id": untrusted_tenant_id,
        "approval_granted": True,
    }
    assert received_policy.approval_granted is False


@pytest.mark.asyncio
async def test_deny_becomes_stable_tool_exception() -> None:
    metadata = registry_tool()
    gateway = RecordingGateway(
        ToolInvocationOutcome(
            decision=PolicyDecision.DENY,
            reason="tool_not_allowed",
        )
    )
    tool = ToolGatewayAdapter(
        gateway=gateway,
        invocation_context=invocation_context(tenant_id=metadata.tenant_id),
        policy_context=PolicyContext(allowed_tool_ids=frozenset()),
    ).adapt(metadata)

    with pytest.raises(ToolException, match=r"^tool_denied:tool_not_allowed$"):
        await tool.ainvoke({"customer_id": 42})


@pytest.mark.asyncio
async def test_approval_decision_becomes_explicit_approval_exception() -> None:
    metadata = registry_tool()
    gateway = RecordingGateway(
        ToolInvocationOutcome(
            decision=PolicyDecision.REQUIRE_APPROVAL,
            reason="approval_required",
        )
    )
    tool = ToolGatewayAdapter(
        gateway=gateway,
        invocation_context=invocation_context(tenant_id=metadata.tenant_id),
        policy_context=PolicyContext(allowed_tool_ids=frozenset({metadata.id})),
    ).adapt(metadata)

    with pytest.raises(ToolApprovalRequired) as captured:
        await tool.ainvoke({"customer_id": 42})

    assert str(captured.value) == "tool_approval_required:approval_required"
    assert captured.value.tool_id == metadata.id
    assert captured.value.tool_name == metadata.name
    assert captured.value.reason == "approval_required"


@pytest.mark.asyncio
async def test_gateway_execution_error_is_sanitized_for_the_model() -> None:
    metadata = registry_tool()
    tool = ToolGatewayAdapter(
        gateway=FailingGateway(),
        invocation_context=invocation_context(tenant_id=metadata.tenant_id),
        policy_context=PolicyContext(allowed_tool_ids=frozenset({metadata.id})),
    ).adapt(metadata)

    with pytest.raises(ToolException, match=r"^tool_execution_failed$") as captured:
        await tool.ainvoke({"customer_id": 42})

    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


@pytest.mark.asyncio
async def test_cancellation_guard_blocks_gateway_before_external_side_effect() -> None:
    metadata = registry_tool()
    gateway = RecordingGateway(
        ToolInvocationOutcome(decision=PolicyDecision.ALLOW, result={"unsafe": True})
    )
    tool = ToolGatewayAdapter(
        gateway=gateway,
        invocation_context=invocation_context(tenant_id=metadata.tenant_id),
        policy_context=PolicyContext(allowed_tool_ids=frozenset({metadata.id})),
        execution_guard=BlockingExecutionGuard(),
    ).adapt(metadata)

    with pytest.raises(ToolExecutionBlocked, match="run_cancellation_requested"):
        await tool.ainvoke({"customer_id": 42})

    assert gateway.calls == []


@pytest.mark.asyncio
async def test_one_time_approval_is_bound_to_run_tool_and_exact_arguments() -> None:
    metadata = registry_tool()
    trusted = invocation_context(tenant_id=metadata.tenant_id)
    gateway = RecordingGateway(
        ToolInvocationOutcome(decision=PolicyDecision.ALLOW, result={"ok": True})
    )
    approvals = OneTimeToolApprovalStore()
    invocation_id = uuid4()
    approvals.grant(
        invocation_id=invocation_id,
        run_id=trusted.run_id,
        tool_id=metadata.id,
        tool_name=metadata.name,
        arguments={"customer_id": 42},
    )
    tool = ToolGatewayAdapter(
        gateway=gateway,
        invocation_context=trusted,
        policy_context=PolicyContext(allowed_tool_ids=frozenset({metadata.id})),
        approval_store=approvals,
    ).adapt(metadata)

    await tool.ainvoke({"customer_id": 41})
    await tool.ainvoke({"customer_id": 42})
    await tool.ainvoke({"customer_id": 42})

    assert [context.approval_granted for _, context in gateway.calls] == [
        False,
        True,
        False,
    ]
    assert gateway.calls[1][0].invocation_id == invocation_id


@pytest.mark.parametrize(
    "arguments",
    [
        {"value": float("nan")},
        {"value": object()},
        {1: "non-string-key"},
    ],
)
def test_one_time_approval_rejects_non_canonical_json(arguments: dict[object, object]) -> None:
    approvals = OneTimeToolApprovalStore()

    with pytest.raises((TypeError, ValueError)):
        approvals.grant(
            invocation_id=uuid4(),
            run_id=uuid4(),
            tool_id=uuid4(),
            tool_name="unsafe",
            arguments=arguments,
        )
