from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

from .errors import ToolExecutionError, ToolExecutionFailure, ToolInvocationClaimRejected
from .models import (
    ArgumentSummary,
    AuditEventType,
    PolicyContext,
    PolicyDecision,
    ToolAuditEvent,
    ToolDefinition,
    ToolInvocation,
    ToolInvocationOutcome,
)
from .policy import evaluate_policy
from .ports import (
    CredentialResolver,
    ExecutionCircuit,
    ToolAuditSink,
    ToolDefinitionResolver,
    ToolExecutor,
)


class ToolGateway:
    def __init__(
        self,
        *,
        executor: ToolExecutor,
        definition_resolver: ToolDefinitionResolver,
        credential_resolver: CredentialResolver,
        audit_sink: ToolAuditSink,
        execution_circuit: ExecutionCircuit | None = None,
    ) -> None:
        self._executor = executor
        self._definition_resolver = definition_resolver
        self._credential_resolver = credential_resolver
        self._audit_sink = audit_sink
        self._execution_circuit = execution_circuit

    async def invoke(
        self,
        invocation: ToolInvocation,
        context: PolicyContext,
    ) -> ToolInvocationOutcome:
        summary = _summarize_arguments(invocation.arguments)
        definition = await self._definition_resolver.resolve(
            tenant_id=invocation.tenant_id,
            tool_id=invocation.tool_id,
        )
        if definition is None:
            reason = "tool_not_found"
            await self._audit_sink.emit(
                _audit_event(
                    event_type=AuditEventType.REJECTED,
                    invocation=invocation,
                    definition=None,
                    argument_summary=summary,
                    reason=reason,
                )
            )
            return ToolInvocationOutcome(decision=PolicyDecision.DENY, reason=reason)

        policy = evaluate_policy(invocation, definition, context)
        if policy.decision is not PolicyDecision.ALLOW:
            event_type = (
                AuditEventType.APPROVAL_REQUIRED
                if policy.decision is PolicyDecision.REQUIRE_APPROVAL
                else AuditEventType.REJECTED
            )
            await self._audit_sink.emit(
                _audit_event(
                    event_type=event_type,
                    invocation=invocation,
                    definition=definition,
                    argument_summary=summary,
                    reason=policy.reason,
                )
            )
            return ToolInvocationOutcome(decision=policy.decision, reason=policy.reason)

        if self._execution_circuit is not None and not self._execution_circuit.allow(
            tenant_id=definition.tenant_id, server_id=definition.server_id
        ):
            reason = "tool_circuit_open"
            await self._audit_sink.emit(
                _audit_event(
                    event_type=AuditEventType.REJECTED,
                    invocation=invocation,
                    definition=definition,
                    argument_summary=summary,
                    reason=reason,
                )
            )
            return ToolInvocationOutcome(decision=PolicyDecision.DENY, reason=reason)

        try:
            await self._audit_sink.emit(
                _audit_event(
                    event_type=AuditEventType.STARTED,
                    invocation=invocation,
                    definition=definition,
                    argument_summary=summary,
                )
            )
        except ToolInvocationClaimRejected:
            return ToolInvocationOutcome(
                decision=PolicyDecision.DENY,
                reason="run_execution_not_allowed",
            )
        failure_code: str | None = None
        result: object | None = None
        try:
            credentials = await self._credential_resolver.resolve(
                tenant_id=invocation.tenant_id,
                references=definition.credential_references,
            )
        except Exception:
            failure_code = "credential_unavailable"
        else:
            try:
                result = await self._executor.execute(
                    definition=definition,
                    arguments=invocation.arguments,
                    credentials=credentials,
                    invocation_id=invocation.invocation_id,
                )
            except ToolExecutionFailure as failure:
                failure_code = failure.code
            except Exception:
                failure_code = "tool_execution_failed"

        if failure_code is not None:
            if self._execution_circuit is not None:
                self._execution_circuit.record_failure(
                    tenant_id=definition.tenant_id, server_id=definition.server_id
                )
            await self._audit_sink.emit(
                _audit_event(
                    event_type=AuditEventType.COMPLETED,
                    invocation=invocation,
                    definition=definition,
                    argument_summary=summary,
                    succeeded=False,
                    reason=failure_code,
                )
            )
            raise ToolExecutionError(failure_code)

        if self._execution_circuit is not None:
            self._execution_circuit.record_success(
                tenant_id=definition.tenant_id, server_id=definition.server_id
            )
        await self._audit_sink.emit(
            _audit_event(
                event_type=AuditEventType.COMPLETED,
                invocation=invocation,
                definition=definition,
                argument_summary=summary,
                succeeded=True,
            )
        )
        return ToolInvocationOutcome(decision=PolicyDecision.ALLOW, result=result)


def _audit_event(
    *,
    event_type: AuditEventType,
    invocation: ToolInvocation,
    definition: ToolDefinition | None,
    argument_summary: ArgumentSummary,
    reason: str | None = None,
    succeeded: bool | None = None,
) -> ToolAuditEvent:
    return ToolAuditEvent(
        event_type=event_type,
        occurred_at=datetime.now(UTC),
        tenant_id=invocation.tenant_id,
        run_id=invocation.run_id,
        employee_id=invocation.employee_id,
        user_id=invocation.user_id,
        tool_id=invocation.tool_id,
        tool_name=invocation.tool_name,
        risk=definition.risk if definition is not None else None,
        argument_summary=argument_summary,
        reason=reason,
        succeeded=succeeded,
        invocation_id=invocation.invocation_id,
    )


def _summarize_arguments(arguments: Mapping[str, object]) -> ArgumentSummary:
    canonical = json.dumps(
        _canonicalize(arguments),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return ArgumentSummary(
        keys=tuple(sorted(arguments)),
        sha256=hashlib.sha256(canonical).hexdigest(),
        size_bytes=len(canonical),
    )


def _canonicalize(value: object) -> object:
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, str):
        return {"length": len(value), "type": "string"}
    if isinstance(value, int):
        return {"type": "integer"}
    if isinstance(value, float):
        return {"type": "number"}
    if isinstance(value, Mapping):
        return {
            "fields": {str(key): _canonicalize(item) for key, item in value.items()},
            "type": "object",
        }
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return {
            "items": [_canonicalize(item) for item in value],
            "length": len(value),
            "type": "array",
        }
    return {"type": type(value).__qualname__}
