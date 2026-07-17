from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Literal, cast
from uuid import UUID

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from pydantic import JsonValue, TypeAdapter
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
from agent_platform.sandbox.entities import SandboxScope
from agent_platform.sandbox.manager import SandboxManager
from agent_platform.sandbox.ports import RunExecutionEnvironment

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
    """宿主进程没有可用的内部模型网关客户端。"""

    code = "model_gateway_unavailable"


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


class PlatformModelResolver:
    """只解析 provider-neutral alias；凭据与路由留在宿主进程。"""

    def __init__(
        self,
        *,
        injected_models: Mapping[str, BaseChatModel] | None = None,
        model_factory: Callable[[str], BaseChatModel] | None = None,
        allowed_aliases: frozenset[str] = DEFAULT_MODEL_ALIASES,
    ) -> None:
        self._injected_models = dict(injected_models or {})
        self._model_factory = model_factory
        self._allowed_aliases = allowed_aliases | self._injected_models.keys()

    def resolve(self, model: PublishedModel) -> ResolvedModel:
        if model.alias not in self._allowed_aliases:
            raise ModelGatewayUnavailable("model alias is outside the platform allowlist")
        injected = self._injected_models.get(model.alias)
        if injected is not None:
            return injected
        if self._model_factory is None:
            raise ModelGatewayUnavailable("model gateway factory is unavailable")
        return self._model_factory(model.alias)


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
        model_resolver: PlatformModelResolver | None = None,
        knowledge_provider_registry: KnowledgeProviderRegistry | None = None,
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
        self._model_resolver = model_resolver or PlatformModelResolver()
        self._knowledge_provider_registry = knowledge_provider_registry
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
        model = self._model_resolver.resolve(capabilities.model)
        knowledge_context = await self._prepare_knowledge_context(
            run=run,
            capabilities=capabilities,
        )
        memory_context = await self._prepare_memory_context(
            run=run,
            capabilities=capabilities,
        )
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
        knowledge_context = await self._prepare_knowledge_context(
            run=run,
            capabilities=capabilities,
        )
        memory_context = await self._prepare_memory_context(
            run=run,
            capabilities=capabilities,
        )
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
            scope=scope,
            environment=environment,
            delete_on_error=False,
        )

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
        scope: SandboxScope,
        environment: RunExecutionEnvironment,
        delete_on_error: bool,
    ) -> PreparedRuntimeResult:
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
