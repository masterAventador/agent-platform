from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Any, Protocol, cast
from uuid import UUID, uuid4

from langchain_core.tools import BaseTool, StructuredTool, ToolException
from pydantic import JsonValue, TypeAdapter

from agent_platform.platform.tool_gateway import (
    PolicyContext,
    PolicyDecision,
    ToolExecutionError,
    ToolInvocation,
    ToolInvocationOutcome,
)


class RegistryToolMetadata(Protocol):
    """Tool Registry 中构造模型工具所需的公开元数据。"""

    @property
    def id(self) -> UUID: ...

    @property
    def name(self) -> str: ...

    @property
    def description(self) -> str: ...

    @property
    def input_schema(self) -> dict[str, object]: ...

    @property
    def risk_level(self) -> object: ...


class ToolGatewayInvoker(Protocol):
    async def invoke(
        self,
        invocation: ToolInvocation,
        context: PolicyContext,
    ) -> ToolInvocationOutcome: ...


class ToolExecutionGuard(Protocol):
    async def assert_allowed(self) -> None: ...


@dataclass(frozen=True, slots=True)
class InvocationContext:
    """由平台绑定、不可由模型工具参数覆盖的调用主体。"""

    tenant_id: UUID
    run_id: UUID
    employee_id: UUID
    user_id: UUID


class OneTimeToolApprovalStore:
    """run 内一次性精确授权；不把 approval_granted 扩大到其他调用。"""

    def __init__(self) -> None:
        self._grants: dict[tuple[UUID, UUID, str, str], UUID] = {}

    def grant(
        self,
        *,
        invocation_id: UUID,
        run_id: UUID,
        tool_id: UUID,
        tool_name: str,
        arguments: dict[str, object],
    ) -> None:
        self._grants[(run_id, tool_id, tool_name, self._argument_digest(arguments))] = invocation_id

    def consume(
        self,
        *,
        run_id: UUID,
        tool_id: UUID,
        tool_name: str,
        arguments: dict[str, object],
    ) -> UUID | None:
        grant = (run_id, tool_id, tool_name, self._argument_digest(arguments))
        if grant not in self._grants:
            return None
        return self._grants.pop(grant)

    def revoke(
        self,
        *,
        run_id: UUID,
        tool_id: UUID,
        tool_name: str,
        arguments: dict[str, object],
    ) -> None:
        self._grants.pop(
            (run_id, tool_id, tool_name, self._argument_digest(arguments)),
            None,
        )

    @staticmethod
    def _argument_digest(arguments: dict[str, object]) -> str:
        OneTimeToolApprovalStore._validate_json_shape(arguments)
        canonical_arguments = TypeAdapter(dict[str, JsonValue]).validate_python(arguments)
        canonical = json.dumps(
            canonical_arguments,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    @staticmethod
    def _validate_json_shape(value: object) -> None:
        if value is None or isinstance(value, bool | str | int):
            return
        if isinstance(value, float):
            if not math.isfinite(value):
                raise ValueError("approval arguments must contain finite JSON numbers")
            return
        if isinstance(value, list):
            for item in value:
                OneTimeToolApprovalStore._validate_json_shape(item)
            return
        if isinstance(value, dict):
            if any(not isinstance(key, str) for key in value):
                raise TypeError("approval argument keys must be strings")
            for item in value.values():
                OneTimeToolApprovalStore._validate_json_shape(item)
            return
        raise TypeError("approval arguments must be canonical JSON")


class ToolApprovalRequired(ToolException):
    """网关要求中断运行并走平台审批流程。"""

    def __init__(
        self,
        *,
        tool_id: UUID,
        tool_name: str,
        reason: str,
    ) -> None:
        self.tool_id = tool_id
        self.tool_name = tool_name
        self.reason = reason
        super().__init__(f"tool_approval_required:{reason}")


class ToolExecutionBlocked(ToolException):
    """平台状态已禁止该 run 开始新的外部工具副作用。"""


def _tool_error_as_model_feedback(error: ToolException) -> str:
    """把调用点拒绝/失败转换为模型可见的稳定错误消息。

    审批中断（ToolApprovalRequired）与取消守卫（ToolExecutionBlocked）必须
    继续向运行时冒泡，不能被吞成普通工具错误。
    """
    if isinstance(error, (ToolApprovalRequired, ToolExecutionBlocked)):
        raise error
    message = str(error.args[0]) if error.args else "tool_execution_failed"
    return message


class ToolGatewayAdapter:
    """把 Registry 工具包装为仅经 Tool Gateway 执行的 LangChain 工具。"""

    def __init__(
        self,
        *,
        gateway: ToolGatewayInvoker,
        invocation_context: InvocationContext,
        policy_context: PolicyContext,
        approval_store: OneTimeToolApprovalStore | None = None,
        execution_guard: ToolExecutionGuard | None = None,
    ) -> None:
        self._gateway = gateway
        self._invocation_context = invocation_context
        self._policy_context = policy_context
        self._approval_store = approval_store
        self._execution_guard = execution_guard

    def adapt(self, metadata: RegistryToolMetadata) -> BaseTool:
        invoke = self._build_invoker(metadata)
        return StructuredTool.from_function(
            coroutine=invoke,
            name=metadata.name,
            description=metadata.description,
            args_schema=cast(dict[str, Any], metadata.input_schema),
            infer_schema=False,
            handle_tool_error=_tool_error_as_model_feedback,
            metadata={
                "agent_platform_tool_id": str(metadata.id),
                "agent_platform_tool_risk": str(metadata.risk_level),
            },
        )

    def _build_invoker(
        self,
        metadata: RegistryToolMetadata,
    ) -> Callable[..., Awaitable[object | None]]:
        async def invoke(**arguments: object) -> object | None:
            context = self._invocation_context
            execution_failed = False
            failure_code = "tool_execution_failed"
            outcome: ToolInvocationOutcome | None = None
            try:
                if self._execution_guard is not None:
                    await self._execution_guard.assert_allowed()
                approval_invocation_id = (
                    self._approval_store.consume(
                        run_id=context.run_id,
                        tool_id=metadata.id,
                        tool_name=metadata.name,
                        arguments=arguments,
                    )
                    if self._approval_store is not None
                    else None
                )
                invocation_id = approval_invocation_id or uuid4()
                outcome = await self._gateway.invoke(
                    ToolInvocation(
                        tenant_id=context.tenant_id,
                        run_id=context.run_id,
                        employee_id=context.employee_id,
                        user_id=context.user_id,
                        tool_id=metadata.id,
                        tool_name=metadata.name,
                        arguments=arguments,
                        invocation_id=invocation_id,
                    ),
                    replace(
                        self._policy_context,
                        approval_granted=(
                            self._policy_context.approval_granted
                            or approval_invocation_id is not None
                        ),
                    ),
                )
            except ToolExecutionError as failure:
                execution_failed = True
                failure_code = failure.code

            if execution_failed:
                raise ToolException(f"tool_execution_failed:{failure_code}") from None
            if outcome is None:
                raise ToolException("tool_execution_failed")
            if outcome.decision is PolicyDecision.DENY:
                reason = outcome.reason or "policy_denied"
                raise ToolException(f"tool_denied:{reason}")
            if outcome.decision is PolicyDecision.REQUIRE_APPROVAL:
                reason = outcome.reason or "approval_required"
                raise ToolApprovalRequired(
                    tool_id=metadata.id,
                    tool_name=metadata.name,
                    reason=reason,
                )
            return outcome.result

        return invoke
