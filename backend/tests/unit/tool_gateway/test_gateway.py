from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC
from typing import Any
from uuid import UUID, uuid4

import pytest

from agent_platform.platform.tool_gateway import (
    AuditEventType,
    PolicyContext,
    PolicyDecision,
    ToolDefinition,
    ToolExecutionError,
    ToolGateway,
    ToolInvocation,
    ToolRisk,
)

TOOL_ID = uuid4()
SERVER_ID = uuid4()


@dataclass
class RecordingAuditSink:
    events: list[Any] = field(default_factory=list)

    async def emit(self, event: Any) -> None:
        self.events.append(event)


@dataclass
class RecordingCredentialResolver:
    credentials: Mapping[str, str] = field(default_factory=dict)
    calls: list[tuple[UUID, Sequence[str]]] = field(default_factory=list)

    async def resolve(
        self, *, tenant_id: UUID, references: Sequence[str]
    ) -> Mapping[str, str]:
        self.calls.append((tenant_id, references))
        return self.credentials


@dataclass
class RecordingDefinitionResolver:
    definition: ToolDefinition | None
    calls: list[tuple[UUID, UUID]] = field(default_factory=list)

    async def resolve(self, *, tenant_id: UUID, tool_id: UUID) -> ToolDefinition | None:
        self.calls.append((tenant_id, tool_id))
        return self.definition


@dataclass
class RecordingExecutor:
    result: object = field(default_factory=lambda: {"ok": True})
    calls: list[tuple[ToolDefinition, Mapping[str, object], Mapping[str, str]]] = field(
        default_factory=list
    )

    async def execute(
        self,
        *,
        definition: ToolDefinition,
        arguments: Mapping[str, object],
        credentials: Mapping[str, str],
    ) -> object:
        self.calls.append((definition, arguments, credentials))
        return self.result


class SecretLeakingExecutor:
    async def execute(
        self,
        *,
        definition: ToolDefinition,
        arguments: Mapping[str, object],
        credentials: Mapping[str, str],
    ) -> object:
        del definition, arguments
        raise RuntimeError(f"upstream rejected token {credentials['token']}")


def invocation(
    *,
    tenant_id: UUID | None = None,
    tool_id: UUID = TOOL_ID,
    tool_name: str = "crm.read",
) -> ToolInvocation:
    return ToolInvocation(
        tenant_id=tenant_id or uuid4(),
        run_id=uuid4(),
        employee_id=uuid4(),
        user_id=uuid4(),
        tool_id=tool_id,
        tool_name=tool_name,
        arguments={"customer_id": 42, "password": "do-not-audit"},
    )


def definition(
    *,
    tenant_id: UUID,
    tool_id: UUID = TOOL_ID,
    name: str = "crm.read",
    risk: ToolRisk = ToolRisk.READ,
    enabled: bool = True,
    server_enabled: bool = True,
) -> ToolDefinition:
    return ToolDefinition(
        tenant_id=tenant_id,
        tool_id=tool_id,
        server_id=SERVER_ID,
        name=name,
        risk=risk,
        enabled=enabled,
        server_enabled=server_enabled,
        credential_references=("crm-api-token",),
    )


def context(*, tool_id: UUID = TOOL_ID, approved: bool = False) -> PolicyContext:
    return PolicyContext(allowed_tool_ids=frozenset({tool_id}), approval_granted=approved)


@pytest.mark.asyncio
async def test_allowed_invocation_executes_with_resolved_credentials_and_safe_audit() -> None:
    request = invocation()
    tool = definition(tenant_id=request.tenant_id)
    resolver = RecordingCredentialResolver(credentials={"token": "resolved-secret"})
    executor = RecordingExecutor(result={"record": "full-sensitive-result"})
    audit = RecordingAuditSink()
    definition_resolver = RecordingDefinitionResolver(tool)
    gateway = ToolGateway(
        executor=executor,
        definition_resolver=definition_resolver,
        credential_resolver=resolver,
        audit_sink=audit,
    )

    outcome = await gateway.invoke(request, context())

    assert outcome.decision is PolicyDecision.ALLOW
    assert outcome.result == {"record": "full-sensitive-result"}
    assert definition_resolver.calls == [(request.tenant_id, request.tool_id)]
    assert resolver.calls == [(request.tenant_id, ("crm-api-token",))]
    assert executor.calls == [(tool, request.arguments, {"token": "resolved-secret"})]
    assert [event.event_type for event in audit.events] == [
        AuditEventType.STARTED,
        AuditEventType.COMPLETED,
    ]
    for event in audit.events:
        rendered = repr(event)
        assert "do-not-audit" not in rendered
        assert "resolved-secret" not in rendered
        assert "full-sensitive-result" not in rendered
        assert event.tenant_id == request.tenant_id
        assert event.run_id == request.run_id
        assert event.employee_id == request.employee_id
        assert event.user_id == request.user_id
        assert event.tool_id == request.tool_id
        assert event.tool_name == request.tool_name
        assert event.occurred_at.tzinfo is UTC
        assert event.argument_summary.keys == ("customer_id", "password")
        assert len(event.argument_summary.sha256) == 64


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "tool_factory", "policy_context", "reason"),
    [
        (
            "tenant mismatch",
            lambda request: definition(tenant_id=uuid4()),
            context(),
            "tenant_mismatch",
        ),
        (
            "disabled",
            lambda request: definition(tenant_id=request.tenant_id, enabled=False),
            context(),
            "tool_disabled",
        ),
        (
            "server disabled",
            lambda request: definition(
                tenant_id=request.tenant_id, server_enabled=False
            ),
            context(),
            "server_disabled",
        ),
        (
            "not found",
            lambda request: None,
            context(),
            "tool_not_found",
        ),
        (
            "not whitelisted",
            lambda request: definition(tenant_id=request.tenant_id),
            PolicyContext(allowed_tool_ids=frozenset(), approval_granted=False),
            "tool_not_allowed",
        ),
        (
            "same name but different id",
            lambda request: definition(tenant_id=request.tenant_id, tool_id=uuid4()),
            context(),
            "tool_id_mismatch",
        ),
        (
            "definition name mismatch",
            lambda request: definition(tenant_id=request.tenant_id, name="other.tool"),
            context(),
            "tool_mismatch",
        ),
    ],
)
async def test_policy_denials_are_audited_without_resolving_or_executing(
    case: str,
    tool_factory: Any,
    policy_context: PolicyContext,
    reason: str,
) -> None:
    del case
    request = invocation()
    resolver = RecordingCredentialResolver()
    executor = RecordingExecutor()
    audit = RecordingAuditSink()
    definition_resolver = RecordingDefinitionResolver(tool_factory(request))
    gateway = ToolGateway(
        executor=executor,
        definition_resolver=definition_resolver,
        credential_resolver=resolver,
        audit_sink=audit,
    )

    outcome = await gateway.invoke(request, policy_context)

    assert outcome.decision is PolicyDecision.DENY
    assert outcome.reason == reason
    assert outcome.result is None
    assert definition_resolver.calls == [(request.tenant_id, request.tool_id)]
    assert resolver.calls == []
    assert executor.calls == []
    assert len(audit.events) == 1
    assert audit.events[0].event_type is AuditEventType.REJECTED
    assert audit.events[0].reason == reason
    assert "do-not-audit" not in repr(audit.events[0])


@pytest.mark.asyncio
@pytest.mark.parametrize("risk", [ToolRisk.EXTERNAL, ToolRisk.DESTRUCTIVE])
async def test_dangerous_tools_require_approval_before_credentials_or_execution(
    risk: ToolRisk,
) -> None:
    request = invocation(tool_name="crm.delete")
    tool = definition(tenant_id=request.tenant_id, name="crm.delete", risk=risk)
    resolver = RecordingCredentialResolver()
    executor = RecordingExecutor()
    audit = RecordingAuditSink()
    gateway = ToolGateway(
        executor=executor,
        definition_resolver=RecordingDefinitionResolver(tool),
        credential_resolver=resolver,
        audit_sink=audit,
    )

    outcome = await gateway.invoke(request, context())

    assert outcome.decision is PolicyDecision.REQUIRE_APPROVAL
    assert outcome.reason == "approval_required"
    assert resolver.calls == []
    assert executor.calls == []
    assert [event.event_type for event in audit.events] == [AuditEventType.APPROVAL_REQUIRED]


@pytest.mark.asyncio
async def test_approved_dangerous_tool_is_executed() -> None:
    request = invocation(tool_name="crm.delete")
    tool = definition(
        tenant_id=request.tenant_id, name="crm.delete", risk=ToolRisk.DESTRUCTIVE
    )
    executor = RecordingExecutor()
    audit = RecordingAuditSink()
    gateway = ToolGateway(
        executor=executor,
        definition_resolver=RecordingDefinitionResolver(tool),
        credential_resolver=RecordingCredentialResolver(),
        audit_sink=audit,
    )

    outcome = await gateway.invoke(request, context(approved=True))

    assert outcome.decision is PolicyDecision.ALLOW
    assert len(executor.calls) == 1


@pytest.mark.asyncio
async def test_executor_secret_is_not_exposed_by_error_or_audit_event() -> None:
    request = invocation()
    audit = RecordingAuditSink()
    gateway = ToolGateway(
        executor=SecretLeakingExecutor(),
        definition_resolver=RecordingDefinitionResolver(
            definition(tenant_id=request.tenant_id)
        ),
        credential_resolver=RecordingCredentialResolver(
            credentials={"token": "credential-must-remain-secret"}
        ),
        audit_sink=audit,
    )

    with pytest.raises(ToolExecutionError, match="Tool execution failed") as captured:
        await gateway.invoke(request, context())

    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert "credential-must-remain-secret" not in repr(captured.value)
    assert [event.event_type for event in audit.events] == [
        AuditEventType.STARTED,
        AuditEventType.COMPLETED,
    ]
    assert audit.events[-1].succeeded is False
    assert "credential-must-remain-secret" not in repr(audit.events[-1])


@pytest.mark.asyncio
async def test_argument_digest_fingerprints_structure_not_sensitive_values() -> None:
    tenant_id = uuid4()
    first = invocation(tenant_id=tenant_id)
    second = ToolInvocation(
        tenant_id=tenant_id,
        run_id=uuid4(),
        employee_id=first.employee_id,
        user_id=first.user_id,
        tool_id=first.tool_id,
        tool_name=first.tool_name,
        arguments={"customer_id": 99, "password": "hide-me-now!"},
    )
    first_audit = RecordingAuditSink()
    second_audit = RecordingAuditSink()

    await ToolGateway(
        executor=RecordingExecutor(),
        definition_resolver=RecordingDefinitionResolver(definition(tenant_id=tenant_id)),
        credential_resolver=RecordingCredentialResolver(),
        audit_sink=first_audit,
    ).invoke(first, context())
    await ToolGateway(
        executor=RecordingExecutor(),
        definition_resolver=RecordingDefinitionResolver(definition(tenant_id=tenant_id)),
        credential_resolver=RecordingCredentialResolver(),
        audit_sink=second_audit,
    ).invoke(second, context())

    assert first_audit.events[0].argument_summary == second_audit.events[0].argument_summary
