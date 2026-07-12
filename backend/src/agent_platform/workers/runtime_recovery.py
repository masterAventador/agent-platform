from collections.abc import Awaitable, Callable
from typing import Protocol, runtime_checkable
from uuid import UUID

from agent_platform.platform.runs.entities import RunStatus
from agent_platform.runtimes.base import RuntimeStartRequest, RuntimeState


class RuntimeRecoveryUnavailable(RuntimeError):
    """已发布运行时没有可验证的持久检查点，不能安全恢复。"""

    code = "runtime_recovery_unavailable"

    def __init__(
        self,
        *,
        cleanup: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        super().__init__(self.code)
        self._cleanup = cleanup

    async def cleanup_after_failure(self) -> None:
        if self._cleanup is not None:
            await self._cleanup()


class RuntimeInterrupted(RuntimeRecoveryUnavailable):
    """崩溃发生在非幂等运行区间，平台拒绝自动重放。"""

    code = "runtime_interrupted"


class RuntimeRecoveryTransient(RuntimeError):
    """恢复依赖暂时不可用；不得把原始连接错误暴露到日志或任务状态。"""


class ToolExecutionUncertain(RuntimeRecoveryUnavailable):
    """工具已开始但checkpoint未证明完成，禁止自动重放副作用。"""

    code = "tool_execution_uncertain"

    def __init__(self, *, approval_id: UUID) -> None:
        super().__init__()
        self.approval_id = approval_id


class RuntimeControlMismatch(RuntimeError):
    """控制命令与持久化 interrupt 身份不匹配。"""


@runtime_checkable
class RecoverableEmployeeRuntime(Protocol):
    async def recover(
        self,
        request: RuntimeStartRequest,
        status: RunStatus,
    ) -> RuntimeState: ...


@runtime_checkable
class ApprovalCheckpointRuntime(Protocol):
    def pending_approval_id(self, run_id: UUID) -> UUID | None: ...
