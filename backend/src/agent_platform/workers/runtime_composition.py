from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from importlib.util import find_spec
from typing import Any, Literal, cast
from uuid import UUID

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from pydantic import BaseModel, ConfigDict, JsonValue, TypeAdapter, field_validator
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_platform.infrastructure.database.repositories.skills import (
    SqlAlchemySkillRepository,
)
from agent_platform.infrastructure.database.repositories.tools import (
    SqlAlchemyToolRepository,
)
from agent_platform.platform.runs.entities import Run
from agent_platform.platform.skills.errors import SkillNotFound, SkillVersionNotFound
from agent_platform.platform.skills.materializer import (
    SkillBundleDigestMismatch,
    SkillMaterializer,
    SkillNotPublished,
)
from agent_platform.platform.skills.ports import SkillStorage
from agent_platform.platform.tool_gateway import PolicyContext
from agent_platform.runtimes.base import EmployeeRuntime
from agent_platform.runtimes.deep_agent import (
    DeepAgentFactory,
    DeepAgentRuntime,
    require_sandbox_backend,
)
from agent_platform.runtimes.tool_gateway_adapter import (
    InvocationContext,
    OneTimeToolApprovalStore,
    ToolGatewayAdapter,
    ToolGatewayInvoker,
)
from agent_platform.sandbox.entities import SandboxScope
from agent_platform.sandbox.manager import SandboxManager
from agent_platform.sandbox.ports import RunExecutionEnvironment
from agent_platform.workers.runtime_recovery import (
    RuntimeRecoveryTransient,
    RuntimeRecoveryUnavailable,
)

RuntimeWorkMode = Literal["autonomous", "workflow", "hybrid"]
ResolvedModel = str | BaseChatModel
DEFAULT_RUN_SANDBOX_TTL = timedelta(hours=1)
DEFAULT_MODEL_PROVIDERS = frozenset({"anthropic", "openai"})
MODEL_PROVIDER_MODULES = {
    "anthropic": "langchain_anthropic",
    "openai": "langchain_openai",
}
logger = logging.getLogger(__name__)


class PermanentRuntimePreparationError(Exception):
    """无需重试的发布配置错误；code 可安全持久化。"""

    code = "runtime_preparation_failed"

    def __init__(self, message: str = "") -> None:
        super().__init__(message)
        self._cleanup: Callable[[], Awaitable[None]] | None = None

    def defer_cleanup(self, cleanup: Callable[[], Awaitable[None]]) -> None:
        self._cleanup = cleanup

    async def cleanup_after_failure(self) -> None:
        if self._cleanup is not None:
            await self._cleanup()


class UntrustedRuntimeDefinition(PermanentRuntimePreparationError):
    """发布定义缺失可信运行配置，或包含只能由准备层生成的字段。"""

    code = "invalid_runtime_definition"


class UnsupportedModelProvider(PermanentRuntimePreparationError):
    """发布定义请求了平台未启用的模型供应商。"""

    code = "unsupported_model_provider"


class ModelProviderAdapterMissing(PermanentRuntimePreparationError):
    """平台允许了供应商，但宿主进程没有安装对应官方适配器。"""

    code = "model_provider_unavailable"


class PublishedModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str
    name: str

    @field_validator("provider", "name")
    @classmethod
    def validate_component(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or normalized != value or ":" in normalized:
            raise ValueError("model provider and name must be non-empty canonical components")
        return normalized


@dataclass(frozen=True, slots=True)
class PublishedRuntimeCapabilities:
    """从不可变员工版本读取的可信运行能力，不读取用户 input。"""

    work_mode: RuntimeWorkMode
    model: PublishedModel
    skill_ids: tuple[UUID, ...]
    tool_ids: tuple[UUID, ...]
    workflow_id: UUID | None
    workflow_version: int | None

    @classmethod
    def from_definition(
        cls,
        definition: dict[str, object],
    ) -> PublishedRuntimeCapabilities:
        try:
            work_mode: RuntimeWorkMode = TypeAdapter(RuntimeWorkMode).validate_python(
                definition["work_mode"]
            )
            workflow_id = (
                TypeAdapter(UUID).validate_python(definition["workflow_id"])
                if definition.get("workflow_id") is not None
                else None
            )
            workflow_version = (
                TypeAdapter(int).validate_python(definition["workflow_version"])
                if definition.get("workflow_version") is not None
                else None
            )
            if work_mode != "autonomous" and (
                workflow_id is None or workflow_version is None or workflow_version <= 0
            ):
                raise UntrustedRuntimeDefinition(
                    "workflow and hybrid definitions require workflow_id and workflow_version"
                )
            return cls(
                work_mode=work_mode,
                model=PublishedModel.model_validate(definition["model"]),
                skill_ids=tuple(
                    TypeAdapter(list[UUID]).validate_python(definition.get("skill_ids", []))
                ),
                tool_ids=tuple(
                    TypeAdapter(list[UUID]).validate_python(definition.get("tool_ids", []))
                ),
                workflow_id=workflow_id,
                workflow_version=workflow_version,
            )
        except UntrustedRuntimeDefinition:
            raise
        except (KeyError, ValueError, TypeError):
            raise UntrustedRuntimeDefinition("invalid published runtime definition") from None


class PlatformModelResolver:
    """只解析平台允许的发布模型；凭据始终留在宿主进程环境。"""

    def __init__(
        self,
        *,
        allowed_providers: frozenset[str] = DEFAULT_MODEL_PROVIDERS,
        injected_models: Mapping[tuple[str, str], BaseChatModel] | None = None,
        module_finder: Callable[[str], object | None] = find_spec,
    ) -> None:
        self._allowed_providers = allowed_providers
        self._injected_models = dict(injected_models or {})
        self._module_finder = module_finder

    def resolve(self, model: PublishedModel) -> ResolvedModel:
        if model.provider not in self._allowed_providers:
            raise UnsupportedModelProvider(model.provider)
        injected = self._injected_models.get((model.provider, model.name))
        if injected is not None:
            return injected
        module_name = MODEL_PROVIDER_MODULES.get(model.provider)
        if module_name is None or self._module_finder(module_name) is None:
            raise ModelProviderAdapterMissing(model.provider)
        return f"{model.provider}:{model.name}"


AutonomousRuntimeFactory = Callable[
    [Sequence[BaseTool], RunExecutionEnvironment, ResolvedModel], EmployeeRuntime
]
WorkflowRuntimeFactory = Callable[
    [
        UUID,
        int,
        Sequence[BaseTool],
        RunExecutionEnvironment,
        ResolvedModel,
    ],
    EmployeeRuntime,
]


def create_deep_agent_runtime(
    tools: Sequence[BaseTool],
    environment: RunExecutionEnvironment,
    model: ResolvedModel,
    checkpointer: BaseCheckpointSaver[Any] | None,
    approval_store: OneTimeToolApprovalStore | None,
) -> EmployeeRuntime:
    """用同一个 run environment 的官方 Backend 创建自主员工。"""

    tool_ids_by_name = {
        tool.name: UUID(str(tool.metadata["agent_platform_tool_id"]))
        for tool in tools
        if tool.metadata is not None and "agent_platform_tool_id" in tool.metadata
    }
    interrupt_on = {
        tool.name: True
        for tool in tools
        if tool.metadata is not None
        and tool.metadata.get("agent_platform_tool_risk") in {"external", "destructive"}
    }
    return DeepAgentRuntime(
        agent_factory=DeepAgentFactory(
            model=model,
            tools=tools,
            backend=require_sandbox_backend(environment.backend),
            checkpointer=checkpointer,
        interrupt_on=cast(dict[str, bool | dict[str, object]], interrupt_on) or None,
        ),
        approval_store=approval_store,
        tool_ids_by_name=tool_ids_by_name,
    )


class PlatformRuntimeSelector:
    """自主员工走 Deep Agents；流程员工必须命中已发布流程注册表。"""

    def __init__(
        self,
        *,
        workflow_factory: WorkflowRuntimeFactory,
        autonomous_factory: AutonomousRuntimeFactory | None = None,
        checkpointer: BaseCheckpointSaver[Any] | None = None,
    ) -> None:
        self._autonomous_factory = autonomous_factory
        self._checkpointer = checkpointer
        self._workflow_factory = workflow_factory

    def select(
        self,
        *,
        capabilities: PublishedRuntimeCapabilities,
        tools: Sequence[BaseTool],
        environment: RunExecutionEnvironment,
        model: ResolvedModel,
        approval_store: OneTimeToolApprovalStore | None = None,
    ) -> EmployeeRuntime:
        if capabilities.work_mode == "autonomous":
            if self._autonomous_factory is not None:
                return self._autonomous_factory(tools, environment, model)
            return create_deep_agent_runtime(
                tools,
                environment,
                model,
                self._checkpointer,
                approval_store,
            )
        if capabilities.workflow_id is None or capabilities.workflow_version is None:
            raise UntrustedRuntimeDefinition("published workflow reference is required")
        return self._workflow_factory(
            capabilities.workflow_id,
            capabilities.workflow_version,
            tools,
            environment,
            model,
        )


@dataclass(slots=True)
class PreparedRuntimeResult:
    runtime: EmployeeRuntime
    employee_definition: dict[str, JsonValue]
    environment: RunExecutionEnvironment
    scope: SandboxScope
    sandbox_manager: SandboxManager
    sandbox_ttl: timedelta
    _closed: bool = field(default=False, init=False)

    async def close(self) -> None:
        if self._closed:
            return
        await self.sandbox_manager.delete(
            lease_id=self.environment.lease.id,
            scope=self.scope,
        )
        self._closed = True

    async def detach(self) -> None:
        """仅释放当前 Worker 的本地客户端，不删除可被新 owner 恢复的沙箱。"""

        if self._closed:
            return
        close = getattr(self.environment.backend, "aclose", None)
        if callable(close):
            await close()
        self._closed = True

    async def renew(self) -> None:
        if self._closed:
            return
        await self.sandbox_manager.renew(
            lease_id=self.environment.lease.id,
            scope=self.scope,
            ttl=self.sandbox_ttl,
        )


class ComposedRuntimeResolver:
    """按发布版本白名单，在同一个 run 沙盒中装配 Skill、Tool 与执行内核。"""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        skill_storage: SkillStorage,
        sandbox_manager: SandboxManager,
        gateway: ToolGatewayInvoker,
        runtime_selector: PlatformRuntimeSelector,
        model_resolver: PlatformModelResolver | None = None,
        sandbox_ttl: timedelta = DEFAULT_RUN_SANDBOX_TTL,
        close_callback: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._skill_storage = skill_storage
        self._sandbox_manager = sandbox_manager
        self._gateway = gateway
        self._runtime_selector = runtime_selector
        self._model_resolver = model_resolver or PlatformModelResolver()
        self._sandbox_ttl = sandbox_ttl
        self._close_callback = close_callback

    async def resolve(
        self,
        run: Run,
        definition: dict[str, object],
    ) -> PreparedRuntimeResult:
        if "skill_paths" in definition:
            raise UntrustedRuntimeDefinition("skill_paths must be supplied by runtime preparation")
        capabilities = PublishedRuntimeCapabilities.from_definition(definition)
        model = self._model_resolver.resolve(capabilities.model)
        scope = SandboxScope(
            run_id=run.id,
            tenant_id=run.tenant_id,
            user_id=run.created_by,
            thread_id=run.thread_id,
        )
        environment = await self._sandbox_manager.acquire(
            scope=scope,
            ttl=self._sandbox_ttl,
        )
        return await self._compose(
            run=run,
            definition=definition,
            capabilities=capabilities,
            model=model,
            scope=scope,
            environment=environment,
            delete_on_error=True,
        )

    async def recover(
        self,
        run: Run,
        definition: dict[str, object],
    ) -> PreparedRuntimeResult:
        if "skill_paths" in definition:
            raise UntrustedRuntimeDefinition("skill_paths must be supplied by runtime preparation")
        capabilities = PublishedRuntimeCapabilities.from_definition(definition)
        model = self._model_resolver.resolve(capabilities.model)
        scope = SandboxScope(
            run_id=run.id,
            tenant_id=run.tenant_id,
            user_id=run.created_by,
            thread_id=run.thread_id,
        )
        environment = await self._sandbox_manager.reconnect_active(
            scope=scope,
            ttl=self._sandbox_ttl,
        )
        return await self._compose(
            run=run,
            definition=definition,
            capabilities=capabilities,
            model=model,
            scope=scope,
            environment=environment,
            delete_on_error=False,
        )

    async def _compose(
        self,
        *,
        run: Run,
        definition: dict[str, object],
        capabilities: PublishedRuntimeCapabilities,
        model: ResolvedModel,
        scope: SandboxScope,
        environment: RunExecutionEnvironment,
        delete_on_error: bool,
    ) -> PreparedRuntimeResult:
        try:
            async with self._session_factory() as session:
                try:
                    skill_paths = await SkillMaterializer(
                        repository=SqlAlchemySkillRepository(session),
                        storage=self._skill_storage,
                    ).materialize(
                        tenant_id=run.tenant_id,
                        skill_ids=capabilities.skill_ids,
                        workspace=environment.workspace,
                    )
                except (
                    SkillNotFound,
                    SkillVersionNotFound,
                    SkillNotPublished,
                    SkillBundleDigestMismatch,
                ):
                    raise UntrustedRuntimeDefinition("published skill is unavailable") from None
                tool_repository = SqlAlchemyToolRepository(session)
                tool_metadata = []
                for tool_id in capabilities.tool_ids:
                    tool = await tool_repository.get_tool(
                        tenant_id=run.tenant_id,
                        tool_id=tool_id,
                    )
                    if tool is None or not tool.enabled:
                        raise UntrustedRuntimeDefinition("published tool is unavailable")
                    tool_metadata.append(tool)

            gateway_adapter = ToolGatewayAdapter(
                gateway=self._gateway,
                invocation_context=InvocationContext(
                    tenant_id=run.tenant_id,
                    run_id=run.id,
                    employee_id=run.employee_id,
                    user_id=run.created_by,
                ),
                policy_context=PolicyContext(
                    allowed_tool_ids=frozenset(capabilities.tool_ids),
                ),
                approval_store=(approval_store := OneTimeToolApprovalStore()),
            )
            tools = [gateway_adapter.adapt(metadata) for metadata in tool_metadata]
            runtime = self._runtime_selector.select(
                capabilities=capabilities,
                tools=tools,
                environment=environment,
                model=model,
                approval_store=approval_store,
            )
        except PermanentRuntimePreparationError as error:
            async def cleanup() -> None:
                await self._sandbox_manager.delete(
                    lease_id=environment.lease.id,
                    scope=scope,
                )

            if not delete_on_error:
                raise RuntimeRecoveryUnavailable(cleanup=cleanup) from None
            error.defer_cleanup(cleanup)
            raise
        except Exception:
            close = getattr(environment.backend, "aclose", None)
            if callable(close):
                try:
                    await close()
                except Exception:
                    logger.error(
                        "runtime_recovery_detach_failed",
                        extra={"run_id": str(run.id)},
                    )
            if not delete_on_error:
                raise RuntimeRecoveryTransient from None
            raise

        return PreparedRuntimeResult(
            runtime=runtime,
            employee_definition=extend_runtime_definition(
                definition,
                skill_paths=skill_paths,
            ),
            environment=environment,
            scope=scope,
            sandbox_manager=self._sandbox_manager,
            sandbox_ttl=self._sandbox_ttl,
        )

    async def aclose(self) -> None:
        if self._close_callback is None:
            return
        await self._close_callback()


def extend_runtime_definition(
    definition: dict[str, object],
    *,
    skill_paths: list[str],
) -> dict[str, JsonValue]:
    """仅以准备层的物化结果扩展发布定义，不接受持久化路径注入。"""

    if "skill_paths" in definition:
        raise UntrustedRuntimeDefinition("skill_paths must be supplied by runtime preparation")
    trusted_definition = TypeAdapter(dict[str, JsonValue]).validate_python(definition)
    return {**trusted_definition, "skill_paths": list(skill_paths)}
