from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from langchain_core.tools import BaseTool
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_platform.config import AppSettings
from agent_platform.infrastructure.database.repositories.audit import SqlAlchemyToolAuditSink
from agent_platform.infrastructure.database.repositories.sandbox import (
    SqlAlchemySandboxLeaseUnitOfWorkFactory,
)
from agent_platform.infrastructure.secrets import LocalFileCredentialResolver
from agent_platform.runtimes.base import EmployeeRuntime
from agent_platform.runtimes.deep_agent import DeepAgentSandboxBackendValidator
from agent_platform.sandbox.manager import SandboxManager
from agent_platform.sandbox.ports import RunExecutionEnvironment
from agent_platform.sandbox.providers.local_controller import LocalControllerSandboxProvider
from agent_platform.workers.runtime_composition import (
    PermanentRuntimePreparationError,
    ResolvedModel,
    WorkflowRuntimeFactory,
)


class RuntimeAdapterConfigurationError(RuntimeError):
    """内建运行适配器缺少安全配置。"""


class WorkflowNotRegistered(PermanentRuntimePreparationError):
    """发布定义引用的 workflow/version 未注册；生产环境必须失败关闭。"""

    code = "workflow_not_registered"


class RegisteredWorkflowFactory:
    def __init__(
        self,
        factories: Mapping[tuple[UUID, int], WorkflowRuntimeFactory] | None = None,
    ) -> None:
        self._factories = dict(factories or {})

    def __call__(
        self,
        workflow_id: UUID,
        version: int,
        tools: Sequence[BaseTool],
        environment: RunExecutionEnvironment,
        model: ResolvedModel,
    ) -> EmployeeRuntime:
        factory = self._factories.get((workflow_id, version))
        if factory is None:
            raise WorkflowNotRegistered("published workflow is not registered")
        return factory(workflow_id, version, tools, environment, model)


@dataclass(slots=True)
class BuiltinRuntimeAdapters:
    sandbox_manager: SandboxManager
    workflow_factory: WorkflowRuntimeFactory
    credential_resolver: LocalFileCredentialResolver
    audit_sink: SqlAlchemyToolAuditSink
    sandbox_provider: LocalControllerSandboxProvider

    async def aclose(self) -> None:
        await self.sandbox_provider.aclose()


def validate_runtime_adapter_configuration(settings: AppSettings) -> None:
    if settings.sandbox_provider != "local-controller":
        raise RuntimeAdapterConfigurationError("unsupported sandbox provider")
    secret = settings.sandbox_controller_secret.get_secret_value()
    if len(secret) < 16:
        raise RuntimeAdapterConfigurationError(
            "sandbox controller secret must be at least 16 characters"
        )
    if (
        settings.local_credentials_file is not None
        and settings.local_credentials_repository_root is None
    ):
        raise RuntimeAdapterConfigurationError(
            "local credentials repository root is required when credentials file is configured"
        )


def create_runtime_adapters(
    *,
    settings: AppSettings,
    session_factory: async_sessionmaker[AsyncSession],
    workflow_factories: Mapping[tuple[UUID, int], WorkflowRuntimeFactory] | None = None,
) -> BuiltinRuntimeAdapters:
    validate_runtime_adapter_configuration(settings)
    secret = settings.sandbox_controller_secret.get_secret_value()
    repository_root = settings.local_credentials_repository_root
    provider = LocalControllerSandboxProvider(
        base_url=settings.sandbox_controller_url,
        bearer_secret=secret,
        request_timeout_seconds=settings.sandbox_controller_request_timeout_seconds,
    )
    manager = SandboxManager(
        unit_of_work_factory=SqlAlchemySandboxLeaseUnitOfWorkFactory(session_factory),
        providers={provider.name: provider},
        provider_name=provider.name,
        backend_validator=DeepAgentSandboxBackendValidator(),
    )
    return BuiltinRuntimeAdapters(
        sandbox_manager=manager,
        workflow_factory=RegisteredWorkflowFactory(workflow_factories),
        credential_resolver=LocalFileCredentialResolver(
            credentials_file=settings.local_credentials_file,
            # With no credentials file, empty reference sets return before filesystem access.
            # Root is deliberately / so an accidental future file path fails closed as in-repo.
            repository_root=Path(repository_root) if repository_root is not None else Path("/"),
        ),
        audit_sink=SqlAlchemyToolAuditSink(session_factory),
        sandbox_provider=provider,
    )
