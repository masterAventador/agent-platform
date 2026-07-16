"""C09 网关韧性：审批策略、上游缺失拒绝、错误转换与熔断。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

import pytest

from agent_platform.platform.tool_gateway import (
    AuditEventType,
    PolicyContext,
    PolicyDecision,
    ToolDefinition,
    ToolExecutionError,
    ToolExecutionFailure,
    ToolGateway,
    ToolInvocation,
    ToolRisk,
)
from agent_platform.platform.tool_gateway.circuit import InMemoryToolCircuitBreaker
from agent_platform.platform.tool_gateway.policy import evaluate_policy
from agent_platform.platform.tools.entities import ToolApprovalPolicy

TOOL_ID = uuid4()
SERVER_ID = uuid4()
TENANT_ID = uuid4()


@dataclass
class RecordingAuditSink:
    events: list[Any] = field(default_factory=list)

    async def emit(self, event: Any) -> None:
        self.events.append(event)


@dataclass
class StaticDefinitionResolver:
    definition: ToolDefinition | None

    async def resolve(self, *, tenant_id: UUID, tool_id: UUID) -> ToolDefinition | None:
        del tenant_id, tool_id
        return self.definition


class EmptyCredentialResolver:
    async def resolve(self, *, tenant_id, references) -> Mapping[str, str]:
        del tenant_id, references
        return {}


@dataclass
class FailingExecutor:
    failure: Exception
    calls: int = 0

    async def execute(self, *, definition, arguments, credentials, invocation_id) -> object:
        del definition, arguments, credentials, invocation_id
        self.calls += 1
        raise self.failure


@dataclass
class SucceedingExecutor:
    calls: int = 0

    async def execute(self, *, definition, arguments, credentials, invocation_id) -> object:
        del definition, arguments, credentials, invocation_id
        self.calls += 1
        return {"ok": True}


def definition(
    *,
    risk: ToolRisk = ToolRisk.READ,
    approval_policy: ToolApprovalPolicy = ToolApprovalPolicy.RISK_BASED,
    upstream_missing: bool = False,
) -> ToolDefinition:
    return ToolDefinition(
        tenant_id=TENANT_ID,
        tool_id=TOOL_ID,
        server_id=SERVER_ID,
        name="crm.read",
        risk=risk,
        approval_policy=approval_policy,
        upstream_missing=upstream_missing,
    )


def invocation() -> ToolInvocation:
    return ToolInvocation(
        tenant_id=TENANT_ID,
        run_id=uuid4(),
        employee_id=uuid4(),
        user_id=uuid4(),
        tool_id=TOOL_ID,
        tool_name="crm.read",
        arguments={},
        invocation_id=uuid4(),
    )


def context() -> PolicyContext:
    return PolicyContext(allowed_tool_ids=frozenset({TOOL_ID}))


def test_policy_denies_upstream_missing_tool() -> None:
    outcome = evaluate_policy(invocation(), definition(upstream_missing=True), context())
    assert outcome.decision is PolicyDecision.DENY
    assert outcome.reason == "tool_upstream_missing"


def test_policy_always_approval_forces_approval_even_for_read() -> None:
    outcome = evaluate_policy(
        invocation(),
        definition(risk=ToolRisk.READ, approval_policy=ToolApprovalPolicy.ALWAYS),
        context(),
    )
    assert outcome.decision is PolicyDecision.REQUIRE_APPROVAL


def test_policy_never_approval_skips_approval_for_external_only() -> None:
    outcome = evaluate_policy(
        invocation(),
        definition(risk=ToolRisk.EXTERNAL, approval_policy=ToolApprovalPolicy.NEVER),
        context(),
    )
    assert outcome.decision is PolicyDecision.ALLOW

    # 纵深防御：即使数据被绕过写入 destructive + never，也仍要求审批。
    defensive = evaluate_policy(
        invocation(),
        definition(risk=ToolRisk.DESTRUCTIVE, approval_policy=ToolApprovalPolicy.NEVER),
        context(),
    )
    assert defensive.decision is PolicyDecision.REQUIRE_APPROVAL


@pytest.mark.asyncio
async def test_typed_execution_failure_is_audited_with_stable_code() -> None:
    audit = RecordingAuditSink()
    executor = FailingExecutor(ToolExecutionFailure("tool_timeout"))
    gateway = ToolGateway(
        executor=executor,
        definition_resolver=StaticDefinitionResolver(definition()),
        credential_resolver=EmptyCredentialResolver(),
        audit_sink=audit,
    )

    with pytest.raises(ToolExecutionError) as error:
        await gateway.invoke(invocation(), context())

    assert error.value.code == "tool_timeout"
    completed = [e for e in audit.events if e.event_type is AuditEventType.COMPLETED]
    assert len(completed) == 1
    assert completed[0].succeeded is False
    assert completed[0].reason == "tool_timeout"


@pytest.mark.asyncio
async def test_credential_failure_reports_stable_code_without_executing() -> None:
    class ExplodingCredentialResolver:
        async def resolve(self, *, tenant_id, references) -> Mapping[str, str]:
            del tenant_id, references
            raise RuntimeError("vault token=super-secret exploded")

    audit = RecordingAuditSink()
    executor = SucceedingExecutor()
    gateway = ToolGateway(
        executor=executor,
        definition_resolver=StaticDefinitionResolver(
            ToolDefinition(
                tenant_id=TENANT_ID,
                tool_id=TOOL_ID,
                server_id=SERVER_ID,
                name="crm.read",
                risk=ToolRisk.READ,
                credential_references=("ref",),
            )
        ),
        credential_resolver=ExplodingCredentialResolver(),
        audit_sink=audit,
    )

    with pytest.raises(ToolExecutionError) as error:
        await gateway.invoke(invocation(), context())

    assert error.value.code == "credential_unavailable"
    assert executor.calls == 0
    completed = [e for e in audit.events if e.event_type is AuditEventType.COMPLETED]
    assert completed[0].reason == "credential_unavailable"
    assert "super-secret" not in str(error.value)


@pytest.mark.asyncio
async def test_open_circuit_denies_before_started_claim() -> None:
    audit = RecordingAuditSink()
    circuit = InMemoryToolCircuitBreaker(
        failure_threshold=2, cooldown_seconds=60, clock=lambda: 100.0
    )
    executor = FailingExecutor(ToolExecutionFailure("tool_remote_error"))
    gateway = ToolGateway(
        executor=executor,
        definition_resolver=StaticDefinitionResolver(definition()),
        credential_resolver=EmptyCredentialResolver(),
        audit_sink=audit,
        execution_circuit=circuit,
    )

    for _ in range(2):
        with pytest.raises(ToolExecutionError):
            await gateway.invoke(invocation(), context())

    outcome = await gateway.invoke(invocation(), context())
    assert outcome.decision is PolicyDecision.DENY
    assert outcome.reason == "tool_circuit_open"
    assert executor.calls == 2
    rejected = [e for e in audit.events if e.event_type is AuditEventType.REJECTED]
    assert rejected[-1].reason == "tool_circuit_open"
    started = [e for e in audit.events if e.event_type is AuditEventType.STARTED]
    assert len(started) == 2


@pytest.mark.asyncio
async def test_circuit_recovers_after_cooldown_and_success() -> None:
    now = {"value": 100.0}
    circuit = InMemoryToolCircuitBreaker(
        failure_threshold=1, cooldown_seconds=30, clock=lambda: now["value"]
    )
    audit = RecordingAuditSink()
    failing = FailingExecutor(ToolExecutionFailure("tool_remote_error"))
    gateway = ToolGateway(
        executor=failing,
        definition_resolver=StaticDefinitionResolver(definition()),
        credential_resolver=EmptyCredentialResolver(),
        audit_sink=audit,
        execution_circuit=circuit,
    )
    with pytest.raises(ToolExecutionError):
        await gateway.invoke(invocation(), context())
    denied = await gateway.invoke(invocation(), context())
    assert denied.reason == "tool_circuit_open"

    now["value"] = 131.0  # 冷却结束，半开放行一次
    recovered_gateway = ToolGateway(
        executor=SucceedingExecutor(),
        definition_resolver=StaticDefinitionResolver(definition()),
        credential_resolver=EmptyCredentialResolver(),
        audit_sink=audit,
        execution_circuit=circuit,
    )
    outcome = await recovered_gateway.invoke(invocation(), context())
    assert outcome.decision is PolicyDecision.ALLOW

    # 恢复后连续调用不再拒绝
    outcome = await recovered_gateway.invoke(invocation(), context())
    assert outcome.decision is PolicyDecision.ALLOW


def test_circuit_memory_is_bounded() -> None:
    circuit = InMemoryToolCircuitBreaker(
        failure_threshold=1, cooldown_seconds=30, max_entries=2, clock=lambda: 0.0
    )
    for _ in range(10):
        circuit.record_failure(tenant_id=uuid4(), server_id=uuid4())
    assert circuit.entry_count <= 2
