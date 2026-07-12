from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol, cast
from uuid import UUID

from langchain_core.tools import BaseTool, StructuredTool, ToolException

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


class ToolGatewayInvoker(Protocol):
    async def invoke(
        self,
        invocation: ToolInvocation,
        context: PolicyContext,
    ) -> ToolInvocationOutcome: ...


@dataclass(frozen=True, slots=True)
class InvocationContext:
    """由平台绑定、不可由模型工具参数覆盖的调用主体。"""

    tenant_id: UUID
    run_id: UUID
    employee_id: UUID
    user_id: UUID


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


class ToolGatewayAdapter:
    """把 Registry 工具包装为仅经 Tool Gateway 执行的 LangChain 工具。"""

    def __init__(
        self,
        *,
        gateway: ToolGatewayInvoker,
        invocation_context: InvocationContext,
        policy_context: PolicyContext,
    ) -> None:
        self._gateway = gateway
        self._invocation_context = invocation_context
        self._policy_context = policy_context

    def adapt(self, metadata: RegistryToolMetadata) -> BaseTool:
        invoke = self._build_invoker(metadata)
        return StructuredTool.from_function(
            coroutine=invoke,
            name=metadata.name,
            description=metadata.description,
            args_schema=cast(dict[str, Any], metadata.input_schema),
            infer_schema=False,
        )

    def _build_invoker(
        self,
        metadata: RegistryToolMetadata,
    ) -> Callable[..., Awaitable[object | None]]:
        async def invoke(**arguments: object) -> object | None:
            context = self._invocation_context
            execution_failed = False
            outcome: ToolInvocationOutcome | None = None
            try:
                outcome = await self._gateway.invoke(
                    ToolInvocation(
                        tenant_id=context.tenant_id,
                        run_id=context.run_id,
                        employee_id=context.employee_id,
                        user_id=context.user_id,
                        tool_id=metadata.id,
                        tool_name=metadata.name,
                        arguments=arguments,
                    ),
                    self._policy_context,
                )
            except ToolExecutionError:
                execution_failed = True

            if execution_failed:
                raise ToolException("tool_execution_failed") from None
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
