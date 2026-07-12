from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from types import TracebackType
from typing import Protocol, Self
from uuid import UUID

from agent_platform.sandbox.entities import SandboxLease, SandboxLeaseStatus, SandboxScope


class SandboxWorkspace(Protocol):
    """与 SkillMaterializer.SkillWorkspace 结构兼容的供应商文件端口。"""

    async def write_file(self, *, path: str, content: bytes) -> None: ...


class SandboxBackendValidator(Protocol):
    """在环境进入运行时前，对官方公开 Backend 协议做 fail-fast 校验。"""

    def validate(self, backend: object) -> None: ...


@dataclass(frozen=True)
class SandboxAcquireRequest:
    lease_id: UUID
    scope: SandboxScope
    expires_at: datetime
    sandbox_epoch: int


@dataclass(frozen=True)
class ProviderSandbox:
    sandbox_id: str
    workspace: SandboxWorkspace
    # 具体适配器提供符合 Deep Agents 官方公开 BackendProtocol 的对象；
    # provider-neutral 核心不复制框架接口，也不导入私有 API。
    backend: object
    sandbox_epoch: int


@dataclass(frozen=True)
class RunExecutionEnvironment:
    lease: SandboxLease
    workspace: SandboxWorkspace
    backend: object


class SandboxProvider(Protocol):
    @property
    def name(self) -> str: ...

    async def acquire(self, request: SandboxAcquireRequest) -> ProviderSandbox:
        """按 request.lease_id 幂等创建或恢复供应商沙箱。"""
        ...

    async def reconnect(
        self, *, sandbox_id: str, lease_id: UUID, sandbox_epoch: int
    ) -> ProviderSandbox: ...

    async def delete(self, *, sandbox_id: str, lease_id: UUID, sandbox_epoch: int) -> None: ...

    async def discover(self, *, lease_id: UUID, sandbox_epoch: int) -> list[str]: ...

    async def delete_by_lease(self, *, lease_id: UUID, sandbox_epoch: int) -> str | None: ...

    async def disconnect(self, *, sandbox_id: str) -> None: ...


class SandboxLeaseRepository(Protocol):
    async def add(self, lease: SandboxLease) -> None: ...

    async def update(self, lease: SandboxLease) -> None: ...

    async def update_if_current(
        self,
        lease: SandboxLease,
        *,
        expected_status: SandboxLeaseStatus,
        expected_epoch: int,
    ) -> bool: ...

    async def get(self, *, tenant_id: UUID, lease_id: UUID) -> SandboxLease | None: ...

    async def get_by_scope(self, *, scope: SandboxScope, provider: str) -> SandboxLease | None: ...

    async def list_expired(self, *, now: datetime, limit: int) -> list[SandboxLease]: ...


class SandboxLeaseUnitOfWork(Protocol):
    @property
    def leases(self) -> SandboxLeaseRepository: ...

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    async def commit(self) -> None: ...


class SandboxLeaseUnitOfWorkFactory(Protocol):
    def __call__(self) -> SandboxLeaseUnitOfWork: ...


SandboxProviderRegistry = Mapping[str, SandboxProvider]
