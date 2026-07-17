from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Literal, Protocol, cast
from uuid import UUID

from deepagents import create_deep_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from pydantic import JsonValue, SecretStr, TypeAdapter
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_platform.infrastructure.database.repositories.artifacts import (
    SqlAlchemyArtifactRepository,
    SqlAlchemyArtifactStorageOperationRepository,
    SqlAlchemyFileRepository,
    SqlAlchemyTaskAttachmentRepository,
)
from agent_platform.infrastructure.database.repositories.knowledge import (
    SqlAlchemyKnowledgeBaseRepository,
)
from agent_platform.infrastructure.database.repositories.memories import (
    SqlAlchemyMemoryRepository,
)
from agent_platform.infrastructure.database.repositories.memory_extraction import (
    memory_capability_enabled,
    record_controlled_memory,
)
from agent_platform.infrastructure.database.repositories.runs import (
    EventSequenceConflict,
    SqlAlchemyRunCommandRepository,
    SqlAlchemyRunEventRepository,
    SqlAlchemyRunRepository,
)
from agent_platform.infrastructure.database.repositories.skills import (
    SqlAlchemySkillRepository,
)
from agent_platform.infrastructure.database.repositories.tools import (
    SqlAlchemyToolRepository,
)
from agent_platform.platform.artifacts.entities import Artifact, validate_workspace_path
from agent_platform.platform.artifacts.ports import ArtifactStorageProvider
from agent_platform.platform.artifacts.services import (
    DEFAULT_STORAGE_OPERATION_HEARTBEAT_SECONDS,
    DEFAULT_STORAGE_OPERATION_LEASE,
    DEFAULT_STORAGE_REQUEST_TIMEOUT_SECONDS,
    ArtifactService,
    TaskAttachmentService,
)
from agent_platform.platform.knowledge.errors import (
    InvalidKnowledgeProviderResponse,
    KnowledgeProviderNotConfigured,
    KnowledgeProviderRequestRejected,
    KnowledgeProviderUnavailable,
)
from agent_platform.platform.knowledge.models import KnowledgeCitation
from agent_platform.platform.knowledge.registry import KnowledgeProviderRegistry
from agent_platform.platform.knowledge.retrieval import (
    InvalidKnowledgeRetrievalConfig,
    KnowledgeRetrievalConfig,
    validate_knowledge_retrieval_config,
)
from agent_platform.platform.memory.entities import Memory, MemoryScope, MemorySource
from agent_platform.platform.model_gateway.errors import (
    CorruptModelGatewayPolicy,
    ModelGatewayCredentialNotReady,
    ModelGatewayCredentialUnavailable,
    ModelGatewayPolicyPersistenceError,
)
from agent_platform.platform.model_gateway.pricing import (
    DEFAULT_MODEL_PRICING,
    ModelPricingTable,
)
from agent_platform.platform.model_gateway.usage import ModelUsageRecorder
from agent_platform.platform.models import (
    DEFAULT_MODEL_ALIASES,
    GatewayModelReference,
)
from agent_platform.platform.runs.entities import Run, RunStatus
from agent_platform.platform.runs.events import EventType, PlatformEvent
from agent_platform.platform.skills.errors import SkillNotFound, SkillVersionNotFound
from agent_platform.platform.skills.materializer import (
    SkillBundleDigestMismatch,
    SkillMaterializer,
    SkillNotPublished,
    SkillVersionReference,
)
from agent_platform.platform.skills.ports import SkillStorage
from agent_platform.platform.tool_gateway import PolicyContext
from agent_platform.platform.workflows.graph_spec import (
    WorkflowGraphSpec,
)
from agent_platform.runtimes.artifacts import ArtifactBackedRuntime
from agent_platform.runtimes.base import ArtifactReference, EmployeeRuntime
from agent_platform.runtimes.deep_agent import (
    DeepAgentFactory,
    DeepAgentRuntime,
    require_sandbox_backend,
)
from agent_platform.runtimes.recovery import (
    RuntimeRecoveryTransient,
    RuntimeRecoveryUnavailable,
)
from agent_platform.runtimes.tool_gateway_adapter import (
    InvocationContext,
    OneTimeToolApprovalStore,
    ToolExecutionBlocked,
    ToolGatewayAdapter,
    ToolGatewayInvoker,
)
from agent_platform.runtimes.workflow_graph import (
    WorkflowNodeDependencies,
    build_workflow_runtime,
)
from agent_platform.sandbox.entities import SandboxScope
from agent_platform.sandbox.manager import SandboxManager
from agent_platform.sandbox.ports import RunExecutionEnvironment
from agent_platform.workers.model_usage_capture import (
    ModelUsageCaptureHandler,
    attach_usage_capture,
)

RuntimeWorkMode = Literal["autonomous", "workflow", "hybrid"]
ResolvedModel = BaseChatModel
DEFAULT_RUN_SANDBOX_TTL = timedelta(hours=1)
logger = logging.getLogger(__name__)


class DatabaseToolExecutionGuard:
    """工具调用前的尽力检查；它不构成与外部副作用线性化的执行声明。"""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        run_id: UUID,
        tenant_id: UUID,
    ) -> None:
        self._session_factory = session_factory
        self._run_id = run_id
        self._tenant_id = tenant_id

    async def assert_allowed(self) -> None:
        async with self._session_factory() as session:
            run = await SqlAlchemyRunRepository(session).get(
                tenant_id=self._tenant_id,
                run_id=self._run_id,
            )
            cancellation_commands = await SqlAlchemyRunCommandRepository(
                session
            ).unprocessed_cancel_commands(run_id=self._run_id)
        if run is None or run.status in {
            RunStatus.COMPLETED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }:
            raise ToolExecutionBlocked("run_execution_not_allowed")
        if cancellation_commands:
            raise ToolExecutionBlocked("run_cancellation_requested")


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


class ModelGatewayUnavailable(PermanentRuntimePreparationError):
    """宿主进程或租户没有可用的模型网关凭据，且重投不会改变结果。

    ``code`` 可覆盖为更精确的稳定原因码（已撤销 / alias 越权 / 对账确定失败等），它会随 Run
    持久化：只报一个笼统的 model_gateway_unavailable 会让用户无从区分该找谁修。
    这些码都是稳定标识，不含任何凭据材料。
    """

    code = "model_gateway_unavailable"

    def __init__(self, message: str = "", *, code: str | None = None) -> None:
        super().__init__(message or code or "")
        if code is not None:
            self.code = code


class InvalidKnowledgeRuntimeResponse(PermanentRuntimePreparationError):
    """知识供应商返回了平台无法安全解释的响应；消息不携带原始响应内容。"""

    code = "invalid_knowledge_provider_response"


class KnowledgeRuntimeRequestRejected(PermanentRuntimePreparationError):
    """知识供应商明确拒绝了检索请求（认证、权限、资源或业务错误）；消息不携带原始响应内容。"""

    code = "knowledge_provider_rejected"


class KnowledgeRuntimeNotConfigured(PermanentRuntimePreparationError):
    """知识库引用的供应商未在当前部署注册；部署配置缺陷，重投递无法恢复。"""

    code = "knowledge_provider_not_configured"


class TransientRuntimePreparationError(RuntimeError):
    """运行时准备依赖暂时不可用；由队列重投递重试，消息不携带底层连接细节。"""


class ModelGatewayProvisioningInProgress(TransientRuntimePreparationError):
    """租户网关凭据正在对账中：Controller 秒级收敛，交队列重投。

    这是**瞬态**而非永久：``policy.status`` 只是对账进度，不代表凭据不可用。任何一次策略
    变更（哪怕只是改 rpm_limit）都会短暂进入 pending，判成永久会打死窗口内的每个 Run。
    """

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class PublishedModel(GatewayModelReference):
    pass


@dataclass(frozen=True, slots=True)
class PublishedSkillVersion:
    skill_id: UUID
    version: int


@dataclass(frozen=True, slots=True)
class RuntimeKnowledgeContext:
    citations: tuple[KnowledgeCitation, ...]

    def as_input_payload(self) -> dict[str, JsonValue]:
        payload = {"citations": [citation.model_dump(mode="json") for citation in self.citations]}
        return TypeAdapter(dict[str, JsonValue]).validate_python(payload)


@dataclass(frozen=True, slots=True)
class MemoryRuntimeContext:
    """按权限召回的长期记忆快照。

    只作为数据经 ``input_data["memory_context"]`` 注入员工上下文，
    绝不拼接为系统指令级文本（记忆是数据不是指令，防提示注入放大）。
    """

    memories: tuple[Memory, ...]

    def as_input_payload(self) -> dict[str, JsonValue]:
        payload = {
            "memories": [
                {
                    "scope": memory.scope.value,
                    "content": memory.content,
                    "updated_at": memory.updated_at.isoformat(),
                }
                for memory in self.memories
            ]
        }
        return TypeAdapter(dict[str, JsonValue]).validate_python(payload)


def _parse_knowledge_retrieval(value: object) -> KnowledgeRetrievalConfig:
    if value is None:
        return KnowledgeRetrievalConfig()
    try:
        return validate_knowledge_retrieval_config(value)
    except InvalidKnowledgeRetrievalConfig:
        raise UntrustedRuntimeDefinition("invalid knowledge retrieval configuration") from None


def _parse_skill_versions(
    value: object,
    fallback_skill_ids: tuple[UUID, ...],
) -> tuple[PublishedSkillVersion, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise UntrustedRuntimeDefinition("skill_versions must be a list")
    parsed: list[PublishedSkillVersion] = []
    expected_ids = set(fallback_skill_ids)
    for item in value:
        if not isinstance(item, dict):
            raise UntrustedRuntimeDefinition("invalid skill_versions entry")
        skill_id = TypeAdapter(UUID).validate_python(item.get("skill_id"))
        version = TypeAdapter(int).validate_python(item.get("version"))
        if version <= 0 or (expected_ids and skill_id not in expected_ids):
            raise UntrustedRuntimeDefinition("invalid skill version reference")
        parsed.append(PublishedSkillVersion(skill_id=skill_id, version=version))
    return tuple(parsed)


def _knowledge_query_from_input(input_data: Mapping[str, JsonValue]) -> str:
    question = input_data.get("question")
    if isinstance(question, str) and question.strip():
        return question.strip()
    message = input_data.get("message")
    if isinstance(message, str) and message.strip():
        return message.strip()
    return json.dumps(input_data, ensure_ascii=False, sort_keys=True, default=str)[:4000]


@dataclass(frozen=True, slots=True)
class PublishedRuntimeCapabilities:
    """从不可变员工版本读取的可信运行能力，不读取用户 input。"""

    work_mode: RuntimeWorkMode
    model: PublishedModel
    skill_ids: tuple[UUID, ...]
    skill_versions: tuple[PublishedSkillVersion, ...]
    tool_ids: tuple[UUID, ...]
    knowledge_base_ids: tuple[UUID, ...]
    knowledge_retrieval: KnowledgeRetrievalConfig
    workflow_id: UUID | None
    workflow_version: int | None
    memory_enabled: bool = False

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
            skill_ids = tuple(
                TypeAdapter(list[UUID]).validate_python(definition.get("skill_ids", []))
            )
            return cls(
                work_mode=work_mode,
                model=PublishedModel.model_validate(definition["model"]),
                skill_ids=skill_ids,
                skill_versions=_parse_skill_versions(definition.get("skill_versions"), skill_ids),
                tool_ids=tuple(
                    TypeAdapter(list[UUID]).validate_python(definition.get("tool_ids", []))
                ),
                knowledge_base_ids=tuple(
                    TypeAdapter(list[UUID]).validate_python(
                        definition.get("knowledge_base_ids", [])
                    )
                ),
                knowledge_retrieval=_parse_knowledge_retrieval(
                    definition.get("knowledge_retrieval")
                ),
                workflow_id=workflow_id,
                workflow_version=workflow_version,
                memory_enabled=memory_capability_enabled(definition),
            )
        except UntrustedRuntimeDefinition:
            raise
        except (KeyError, ValueError, TypeError):
            raise UntrustedRuntimeDefinition("invalid published runtime definition") from None


class TenantGatewayCredentials(Protocol):
    async def resolve(self, *, tenant_id: UUID, alias: str) -> SecretStr: ...


class PlatformModelResolver:
    """只解析 provider-neutral alias；凭据按租户解析，路由留在宿主进程。

    C16：网关凭据从应用级共享 Key 升级为租户专属虚拟 Key，因此解析需要 tenant_id。
    宿主侧注入的测试模型（``injected_models``）不经过凭据解析——它们根本不触达网关。
    """

    def __init__(
        self,
        *,
        injected_models: Mapping[str, BaseChatModel] | None = None,
        model_factory: Callable[[str, SecretStr], BaseChatModel] | None = None,
        tenant_credentials: TenantGatewayCredentials | None = None,
        allowed_aliases: frozenset[str] = DEFAULT_MODEL_ALIASES,
    ) -> None:
        self._injected_models = dict(injected_models or {})
        self._model_factory = model_factory
        self._tenant_credentials = tenant_credentials
        self._allowed_aliases = allowed_aliases | self._injected_models.keys()

    async def resolve(self, model: PublishedModel, *, tenant_id: UUID) -> ResolvedModel:
        if model.alias not in self._allowed_aliases:
            raise ModelGatewayUnavailable("model alias is outside the platform allowlist")
        injected = self._injected_models.get(model.alias)
        if injected is not None:
            return injected
        if self._model_factory is None:
            raise ModelGatewayUnavailable("model gateway factory is unavailable")
        if self._tenant_credentials is None:
            # 缺凭据解析器时宁可失败关闭：静默放行等于恢复不可归因的共享 Key 调用。
            raise ModelGatewayUnavailable("tenant gateway credentials are unavailable")
        try:
            api_key = await self._tenant_credentials.resolve(tenant_id=tenant_id, alias=model.alias)
        except ModelGatewayCredentialNotReady as error:
            # 对账进行中：Controller 秒级收敛，交队列重投。判成永久会让「管理员改了个
            # rpm_limit」这种事在对账窗口内打死每一个并发 Run。
            raise ModelGatewayProvisioningInProgress(error.code) from None
        except CorruptModelGatewayPolicy:
            # 数据损坏：重投永远不会好。必须排在 PersistenceError 之前——它是其子类。
            raise ModelGatewayUnavailable(code="corrupt_model_gateway_policy") from None
        except ModelGatewayPolicyPersistenceError:
            # 数据库瞬时抖动：不是配置缺陷，交队列重投。
            raise TransientRuntimePreparationError(
                "model gateway credential lookup is temporarily unavailable"
            ) from None
        except ModelGatewayCredentialUnavailable as error:
            raise ModelGatewayUnavailable(code=error.code) from None
        return self._model_factory(model.alias, api_key)


AutonomousRuntimeFactory = Callable[
    [Sequence[BaseTool], RunExecutionEnvironment, ResolvedModel], EmployeeRuntime
]
SubagentRunnerFactory = Callable[..., Any]


class WorkflowNotRegistered(PermanentRuntimePreparationError):
    """发布定义引用的 workflow/version 未在注册表命中；生产环境必须失败关闭。"""

    code = "workflow_not_registered"


class WorkflowSpecLoader(Protocol):
    """按发布固化的 (workflow_id, version) 加载并解析已注册工作流图。"""

    async def load(
        self,
        *,
        tenant_id: UUID,
        workflow_id: UUID,
        version: int,
    ) -> WorkflowGraphSpec | None: ...


def build_deep_agent_subagent_runner(
    *,
    model: ResolvedModel,
    tools: Sequence[BaseTool],
    backend: object,
) -> Callable[..., Any]:
    """混合型员工工作流节点内调用 Deep Agents 子智能体（公开 create_deep_agent 工厂）。"""

    sandbox_backend = require_sandbox_backend(backend)

    async def runner(*, prompt: str, input_data: dict[str, JsonValue], node_name: str) -> str:
        del node_name
        agent = create_deep_agent(
            model=model,
            tools=list(tools),
            system_prompt=prompt,
            backend=sandbox_backend,
        )
        message = (
            json.dumps(input_data, ensure_ascii=False, sort_keys=True, default=str)
            if input_data
            else prompt
        )
        result = await agent.ainvoke({"messages": [HumanMessage(content=message)]})
        messages = result.get("messages") if isinstance(result, Mapping) else None
        if messages:
            last = messages[-1]
            return str(getattr(last, "content", last))
        return ""

    return runner


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
    """自主员工走 Deep Agents；流程/混合员工按已固化的注册工作流图用 LangGraph 编排。

    工作流图由准备层（ComposedRuntimeResolver）按 (workflow_id, version) 从注册表加载
    并传入 ``workflow_spec``；命中不到即 fail-closed，不构造任何执行体。混合型员工的
    Deep Agents 子智能体由 ``subagent_runner_factory`` 在节点内调用（公开工厂，零侵入）。
    """

    def __init__(
        self,
        *,
        autonomous_factory: AutonomousRuntimeFactory | None = None,
        checkpointer: BaseCheckpointSaver[Any] | None = None,
        subagent_runner_factory: SubagentRunnerFactory = build_deep_agent_subagent_runner,
    ) -> None:
        self._autonomous_factory = autonomous_factory
        self._checkpointer = checkpointer
        self._subagent_runner_factory = subagent_runner_factory

    def select(
        self,
        *,
        capabilities: PublishedRuntimeCapabilities,
        tools: Sequence[BaseTool],
        environment: RunExecutionEnvironment,
        model: ResolvedModel,
        approval_store: OneTimeToolApprovalStore | None = None,
        workflow_spec: WorkflowGraphSpec | None = None,
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
        if workflow_spec is None:
            # 引用的已发布工作流版本不在注册表 → 失败关闭，不构造任何执行体。
            raise WorkflowNotRegistered("published workflow is not registered")
        deps = WorkflowNodeDependencies(
            model=model,
            tools_by_name={tool.name: tool for tool in tools},
            subagent_runner=self._subagent_runner_factory(
                model=model,
                tools=tools,
                backend=environment.backend,
            ),
        )
        return build_workflow_runtime(
            spec=workflow_spec,
            deps=deps,
            checkpointer=self._checkpointer,
        )


@dataclass(slots=True)
class PreparedRuntimeResult:
    runtime: EmployeeRuntime
    employee_definition: dict[str, JsonValue]
    environment: RunExecutionEnvironment
    scope: SandboxScope
    sandbox_manager: SandboxManager
    sandbox_ttl: timedelta
    knowledge_context: RuntimeKnowledgeContext | None = None
    memory_context: MemoryRuntimeContext | None = None
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
        artifact_storage: ArtifactStorageProvider | None = None,
        sandbox_manager: SandboxManager,
        gateway: ToolGatewayInvoker,
        runtime_selector: PlatformRuntimeSelector,
        workflow_spec_loader: WorkflowSpecLoader | None = None,
        model_resolver: PlatformModelResolver | None = None,
        knowledge_provider_registry: KnowledgeProviderRegistry | None = None,
        model_usage_recorder: ModelUsageRecorder | None = None,
        model_pricing: ModelPricingTable = DEFAULT_MODEL_PRICING,
        sandbox_ttl: timedelta = DEFAULT_RUN_SANDBOX_TTL,
        artifact_operation_lease_duration: timedelta = DEFAULT_STORAGE_OPERATION_LEASE,
        artifact_operation_heartbeat_interval: float = (
            DEFAULT_STORAGE_OPERATION_HEARTBEAT_SECONDS
        ),
        artifact_storage_request_timeout: float = DEFAULT_STORAGE_REQUEST_TIMEOUT_SECONDS,
        close_callback: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._skill_storage = skill_storage
        self._artifact_storage = artifact_storage
        self._sandbox_manager = sandbox_manager
        self._gateway = gateway
        self._runtime_selector = runtime_selector
        self._workflow_spec_loader = workflow_spec_loader
        self._model_resolver = model_resolver or PlatformModelResolver()
        self._knowledge_provider_registry = knowledge_provider_registry
        self._model_usage_recorder = model_usage_recorder
        self._model_pricing = model_pricing
        self._sandbox_ttl = sandbox_ttl
        self._artifact_operation_lease_duration = artifact_operation_lease_duration
        self._artifact_operation_heartbeat_interval = artifact_operation_heartbeat_interval
        self._artifact_storage_request_timeout = artifact_storage_request_timeout
        self._close_callback = close_callback

    async def resolve(
        self,
        run: Run,
        definition: dict[str, object],
    ) -> PreparedRuntimeResult:
        if "skill_paths" in definition:
            raise UntrustedRuntimeDefinition("skill_paths must be supplied by runtime preparation")
        capabilities = PublishedRuntimeCapabilities.from_definition(definition)
        model = await self._model_resolver.resolve(capabilities.model, tenant_id=run.tenant_id)
        knowledge_context = await self._prepare_knowledge_context(
            run=run,
            capabilities=capabilities,
        )
        memory_context = await self._prepare_memory_context(
            run=run,
            capabilities=capabilities,
        )
        # 工作流图在获取沙盒前加载：引用未注册版本即失败关闭，避免无谓的沙盒申请。
        workflow_spec = await self._prepare_workflow_spec(run=run, capabilities=capabilities)
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
            knowledge_context=knowledge_context,
            memory_context=memory_context,
            workflow_spec=workflow_spec,
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
        model = await self._model_resolver.resolve(capabilities.model, tenant_id=run.tenant_id)
        knowledge_context = await self._prepare_knowledge_context(
            run=run,
            capabilities=capabilities,
        )
        memory_context = await self._prepare_memory_context(
            run=run,
            capabilities=capabilities,
        )
        workflow_spec = await self._prepare_workflow_spec(run=run, capabilities=capabilities)
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
            knowledge_context=knowledge_context,
            memory_context=memory_context,
            workflow_spec=workflow_spec,
            scope=scope,
            environment=environment,
            delete_on_error=False,
        )

    async def _prepare_workflow_spec(
        self,
        *,
        run: Run,
        capabilities: PublishedRuntimeCapabilities,
    ) -> WorkflowGraphSpec | None:
        """流程/混合员工按固化版本加载注册工作流图；未命中即失败关闭。"""

        if capabilities.work_mode == "autonomous":
            return None
        if capabilities.workflow_id is None or capabilities.workflow_version is None:
            raise UntrustedRuntimeDefinition("published workflow reference is required")
        if self._workflow_spec_loader is None:
            raise WorkflowNotRegistered("workflow registry is unavailable")
        spec = await self._workflow_spec_loader.load(
            tenant_id=run.tenant_id,
            workflow_id=capabilities.workflow_id,
            version=capabilities.workflow_version,
        )
        if spec is None:
            raise WorkflowNotRegistered("published workflow is not registered")
        return spec

    async def _prepare_knowledge_context(
        self,
        *,
        run: Run,
        capabilities: PublishedRuntimeCapabilities,
    ) -> RuntimeKnowledgeContext | None:
        if not capabilities.knowledge_base_ids:
            return None
        if self._knowledge_provider_registry is None:
            raise UntrustedRuntimeDefinition("published knowledge is unavailable")

        async with self._session_factory() as session:
            knowledge_bases = await SqlAlchemyKnowledgeBaseRepository(session).list_by_ids(
                tenant_id=run.tenant_id,
                knowledge_base_ids=capabilities.knowledge_base_ids,
            )
        if {base.id for base in knowledge_bases} != set(capabilities.knowledge_base_ids):
            raise UntrustedRuntimeDefinition("published knowledge is unavailable")

        question = _knowledge_query_from_input(run.input_data)
        citations: list[KnowledgeCitation] = []
        grouped: dict[str, list[str]] = {}
        for knowledge_base in knowledge_bases:
            grouped.setdefault(knowledge_base.provider, []).append(knowledge_base.provider_id)

        for provider_name, dataset_ids in grouped.items():
            try:
                provider = self._knowledge_provider_registry.resolve(provider_name)
                result = await provider.retrieve(
                    question=question,
                    dataset_ids=dataset_ids,
                    options=capabilities.knowledge_retrieval,
                )
            except KnowledgeProviderNotConfigured:
                raise KnowledgeRuntimeNotConfigured(
                    "knowledge provider is not configured for this deployment"
                ) from None
            except KnowledgeProviderUnavailable:
                raise TransientRuntimePreparationError(
                    "knowledge provider is temporarily unavailable"
                ) from None
            except KnowledgeProviderRequestRejected:
                raise KnowledgeRuntimeRequestRejected(
                    "knowledge provider rejected the retrieval request"
                ) from None
            except InvalidKnowledgeProviderResponse:
                raise InvalidKnowledgeRuntimeResponse(
                    "knowledge provider returned an uninterpretable response"
                ) from None
            allowed_dataset_ids = set(dataset_ids)
            citations.extend(
                citation
                for citation in result.citations
                if citation.dataset_id in allowed_dataset_ids
            )

        return RuntimeKnowledgeContext(citations=tuple(citations))

    async def _prepare_memory_context(
        self,
        *,
        run: Run,
        capabilities: PublishedRuntimeCapabilities,
    ) -> MemoryRuntimeContext | None:
        """员工开启记忆能力时按权限召回长期记忆（禁用后不读）。

        可召回命名空间 = 企业级 + 当前员工 + 发起用户 + 当前会话（如有），
        active、未过期（读取时判定）、按最近性截断。
        """

        if not capabilities.memory_enabled:
            return None
        async with self._session_factory() as session:
            memories = await SqlAlchemyMemoryRepository(session).search_for_runtime(
                tenant_id=run.tenant_id,
                user_id=run.created_by,
                employee_id=run.employee_id,
                conversation_id=run.conversation_id,
            )
        return MemoryRuntimeContext(memories=tuple(memories))

    def _create_save_memory_tool(self, run: Run) -> BaseTool:
        """运行中写入新记忆的受控工具（公开 Tool 扩展点，零侵入）。

        模型输出不可信：工具只允许写入发起用户与当前会话命名空间，
        企业/员工级命名空间必须经带 RBAC 的平台 API 手工维护。
        """

        async def save_memory(content: str, scope: str = "user") -> str:
            """Persist a long-term memory for the requesting user.

            scope: "user" (default) or "conversation" (only inside a conversation run).
            """

            if scope == "conversation":
                if run.conversation_id is None:
                    return "memory rejected: this run does not belong to a conversation"
                memory_scope = MemoryScope.CONVERSATION
                scope_ref = run.conversation_id
            elif scope == "user":
                memory_scope = MemoryScope.USER
                scope_ref = run.created_by
            else:
                return "memory rejected: scope must be 'user' or 'conversation'"
            async with self._session_factory() as session:
                stored = await record_controlled_memory(
                    session,
                    tenant_id=run.tenant_id,
                    scope=memory_scope,
                    scope_ref=scope_ref,
                    content=content,
                    source=MemorySource.RUN,
                    source_ref=str(run.id),
                    created_by=run.created_by,
                )
                if stored is None:
                    return "memory rejected: content is sensitive data"
                await session.commit()
            return "memory saved"

        return StructuredTool.from_function(
            coroutine=save_memory,
            name="save_memory",
            description=(
                "Persist a concise long-term memory (user preference or confirmed "
                "fact) so future tasks can recall it."
            ),
        )

    async def _compose(
        self,
        *,
        run: Run,
        definition: dict[str, object],
        capabilities: PublishedRuntimeCapabilities,
        model: ResolvedModel,
        knowledge_context: RuntimeKnowledgeContext | None,
        memory_context: MemoryRuntimeContext | None,
        workflow_spec: WorkflowGraphSpec | None,
        scope: SandboxScope,
        environment: RunExecutionEnvironment,
        delete_on_error: bool,
    ) -> PreparedRuntimeResult:
        model = self._attach_usage_capture(model=model, run=run, capabilities=capabilities)
        try:
            async with self._session_factory() as session:
                if self._artifact_storage is not None:
                    await TaskAttachmentService(
                        file_repository=SqlAlchemyFileRepository(session),
                        attachment_repository=SqlAlchemyTaskAttachmentRepository(session),
                        storage=self._artifact_storage,
                        storage_request_timeout=self._artifact_storage_request_timeout,
                    ).materialize(
                        tenant_id=run.tenant_id,
                        run_id=run.id,
                        workspace=environment.workspace,
                    )
                try:
                    skill_paths = await SkillMaterializer(
                        repository=SqlAlchemySkillRepository(session),
                        storage=self._skill_storage,
                    ).materialize(
                        tenant_id=run.tenant_id,
                        skill_ids=capabilities.skill_ids,
                        skill_versions=[
                            SkillVersionReference(
                                skill_id=reference.skill_id,
                                version=reference.version,
                            )
                            for reference in capabilities.skill_versions
                        ]
                        or None,
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
                    if tool is None:
                        # 已删除的引用没有可组装的定义，保持 fail-closed。
                        raise UntrustedRuntimeDefinition("published tool is unavailable")
                    # 禁用/上游移除的工具仍然组装：调用点由 Tool Gateway 策略
                    # 拒绝并留下 tool.rejected 审计，避免单个工具下线导致
                    # 员工其他能力整体不可用。
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
                execution_guard=DatabaseToolExecutionGuard(
                    session_factory=self._session_factory,
                    run_id=run.id,
                    tenant_id=run.tenant_id,
                ),
            )
            tools = [gateway_adapter.adapt(metadata) for metadata in tool_metadata]
            if capabilities.memory_enabled:
                tools.append(self._create_save_memory_tool(run))
            artifact_storage = self._artifact_storage
            if artifact_storage is not None:

                async def create_artifact(
                    name: str,
                    media_type: str,
                    workspace_path: str,
                ) -> str:
                    """Publish a file from /workspace as a task artifact."""

                    safe_path = validate_workspace_path(workspace_path)
                    content = await environment.workspace.read_file(path=f"/workspace/{safe_path}")
                    async with self._session_factory() as artifact_session:

                        async def persist_created_event(artifact: Artifact) -> None:
                            events = SqlAlchemyRunEventRepository(artifact_session)
                            for attempt in range(3):
                                created_event = PlatformEvent.create(
                                    tenant_id=run.tenant_id,
                                    employee_id=run.employee_id,
                                    run_id=run.id,
                                    sequence=await events.next_sequence(run_id=run.id),
                                    event_type=EventType.ARTIFACT_CREATED,
                                    payload={
                                        "artifact_id": str(artifact.id),
                                        "name": artifact.name,
                                        "media_type": artifact.media_type,
                                        "size_bytes": artifact.size_bytes,
                                    },
                                )
                                try:
                                    await events.append(created_event)
                                    return
                                except EventSequenceConflict:
                                    if attempt == 2:
                                        raise

                        artifact = await ArtifactService(
                            file_repository=SqlAlchemyFileRepository(artifact_session),
                            artifact_repository=SqlAlchemyArtifactRepository(artifact_session),
                            operation_repository=(
                                SqlAlchemyArtifactStorageOperationRepository(
                                    artifact_session,
                                    heartbeat_session_factory=self._session_factory,
                                )
                            ),
                            storage=artifact_storage,
                            operation_lease_duration=(self._artifact_operation_lease_duration),
                            operation_heartbeat_interval=(
                                self._artifact_operation_heartbeat_interval
                            ),
                            storage_request_timeout=self._artifact_storage_request_timeout,
                        ).create_artifact(
                            tenant_id=run.tenant_id,
                            run_id=run.id,
                            created_by=run.created_by,
                            name=name,
                            media_type=media_type,
                            content=content,
                            before_commit=persist_created_event,
                            commit=artifact_session.commit,
                        )
                    return str(artifact.id)

                tools.append(
                    StructuredTool.from_function(
                        coroutine=create_artifact,
                        name="create_artifact",
                        description=(
                            "Publish a validated relative file from the task workspace "
                            "as a downloadable artifact."
                        ),
                    )
                )
            runtime = self._runtime_selector.select(
                capabilities=capabilities,
                tools=tools,
                environment=environment,
                model=model,
                approval_store=approval_store,
                workflow_spec=workflow_spec,
            )
            if self._artifact_storage is not None:

                async def artifact_catalog(run_id: UUID) -> list[ArtifactReference]:
                    if run_id != run.id:
                        return []
                    async with self._session_factory() as artifact_session:
                        artifacts = await SqlAlchemyArtifactRepository(
                            artifact_session
                        ).list_for_run(tenant_id=run.tenant_id, run_id=run.id)
                    return [
                        ArtifactReference(
                            artifact_id=artifact.id,
                            name=artifact.name,
                            media_type=artifact.media_type,
                            size_bytes=artifact.size_bytes,
                        )
                        for artifact in artifacts
                    ]

                async def event_history(run_id: UUID) -> list[PlatformEvent]:
                    if run_id != run.id:
                        return []
                    async with self._session_factory() as event_session:
                        return await SqlAlchemyRunEventRepository(event_session).list(
                            run_id=run.id,
                            after_sequence=0,
                        )

                runtime = ArtifactBackedRuntime(
                    runtime=runtime,
                    artifact_catalog=artifact_catalog,
                    event_history=event_history,
                )
        except asyncio.CancelledError:
            if delete_on_error:
                try:
                    await asyncio.shield(
                        self._sandbox_manager.delete(
                            lease_id=environment.lease.id,
                            scope=scope,
                        )
                    )
                except Exception:
                    logger.error(
                        "runtime_cancelled_sandbox_delete_failed",
                        extra={"run_id": str(run.id)},
                    )
            else:
                close = getattr(environment.backend, "aclose", None)
                if callable(close):
                    try:
                        await asyncio.shield(close())
                    except Exception:
                        logger.error(
                            "runtime_cancelled_recovery_detach_failed",
                            extra={"run_id": str(run.id)},
                        )
            raise
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
            if delete_on_error:
                try:
                    await self._sandbox_manager.delete(
                        lease_id=environment.lease.id,
                        scope=scope,
                    )
                except Exception:
                    logger.error(
                        "runtime_initial_sandbox_delete_failed",
                        extra={"run_id": str(run.id)},
                    )
                    close = getattr(environment.backend, "aclose", None)
                    if callable(close):
                        try:
                            await close()
                        except Exception:
                            logger.error(
                                "runtime_initial_sandbox_detach_failed",
                                extra={"run_id": str(run.id)},
                            )
                raise
            close = getattr(environment.backend, "aclose", None)
            if callable(close):
                try:
                    await close()
                except Exception:
                    logger.error(
                        "runtime_recovery_detach_failed",
                        extra={"run_id": str(run.id)},
                    )
            raise RuntimeRecoveryTransient from None

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
            knowledge_context=knowledge_context,
            memory_context=memory_context,
        )

    def _attach_usage_capture(
        self,
        *,
        model: ResolvedModel,
        run: Run,
        capabilities: PublishedRuntimeCapabilities,
    ) -> ResolvedModel:
        """C16 阶段二（纯观测面）：per-run 装配用量捕获回调到执行内核用的模型。

        未装配记录器时零改动（保持既有行为）。归属（tenant/run/employee）与
        provider-neutral alias 由此处闭包注入；捕获点本身对模型调用行为零影响。
        """

        if self._model_usage_recorder is None:
            return model
        handler = ModelUsageCaptureHandler(
            recorder=self._model_usage_recorder,
            pricing=self._model_pricing,
            tenant_id=run.tenant_id,
            run_id=run.id,
            employee_id=run.employee_id,
            model_alias=capabilities.model.alias,
        )
        return attach_usage_capture(model, handler)

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
