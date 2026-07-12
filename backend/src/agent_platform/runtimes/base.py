from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable
from uuid import UUID

from pydantic import BaseModel, ConfigDict, JsonValue

from agent_platform.platform.runs.entities import RunStatus
from agent_platform.platform.runs.events import PlatformEvent


class RuntimeStartRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: UUID
    tenant_id: UUID
    user_id: UUID
    employee_id: UUID
    thread_id: str
    employee_definition: dict[str, JsonValue]
    input_data: dict[str, JsonValue]


class RuntimeState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: UUID
    status: RunStatus
    data: dict[str, JsonValue]


class ArtifactReference(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    artifact_id: UUID
    name: str
    media_type: str
    size_bytes: int


@runtime_checkable
class EmployeeRuntime(Protocol):
    async def start(self, request: RuntimeStartRequest) -> RuntimeState: ...

    def stream(
        self,
        run_id: UUID,
        *,
        after_sequence: int = 0,
    ) -> AsyncIterator[PlatformEvent]: ...

    async def send_message(self, run_id: UUID, message: str) -> None: ...

    async def approve(self, run_id: UUID, approval_id: UUID) -> None: ...

    async def reject(
        self,
        run_id: UUID,
        approval_id: UUID,
        reason: str | None = None,
    ) -> None: ...

    async def resume(self, run_id: UUID) -> None: ...

    async def cancel(self, run_id: UUID) -> None: ...

    async def get_state(self, run_id: UUID) -> RuntimeState: ...

    async def get_history(self, run_id: UUID) -> list[PlatformEvent]: ...

    async def get_artifacts(self, run_id: UUID) -> list[ArtifactReference]: ...


class RunWorkspace(Protocol):
    async def write_file(self, *, path: str, content: bytes) -> None: ...


class RunWorkspaceFactory(Protocol):
    """为可信运行身份创建隔离工作区，具体沙箱由基础设施适配器提供。"""

    async def create(
        self,
        *,
        run_id: UUID,
        tenant_id: UUID,
        user_id: UUID,
        employee_id: UUID,
        thread_id: str,
    ) -> RunWorkspace: ...


class PreparedRuntime(Protocol):
    """一次 run 已物化 Skill、包装 Tool 并选定执行内核后的结果。"""

    @property
    def runtime(self) -> EmployeeRuntime: ...

    @property
    def employee_definition(self) -> dict[str, JsonValue]: ...
