import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from deepagents.backends.protocol import SandboxBackendProtocol
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_platform.infrastructure.database.base import Base
from agent_platform.infrastructure.database.models import load_database_models
from agent_platform.infrastructure.database.repositories.knowledge import KnowledgeBaseRecord
from agent_platform.platform.knowledge.errors import (
    InvalidKnowledgeProviderResponse,
    KnowledgeProviderRequestRejected,
    KnowledgeProviderUnavailable,
)
from agent_platform.platform.knowledge.models import KnowledgeCitation, KnowledgeSearchResult
from agent_platform.platform.knowledge.registry import KnowledgeProviderRegistry
from agent_platform.platform.knowledge.retrieval import (
    KnowledgeMetadataCondition,
    KnowledgeMetadataFilterCondition,
    KnowledgeRetrievalConfig,
)
from agent_platform.platform.model_gateway.errors import (
    CorruptModelGatewayPolicy,
    ModelGatewayCredentialNotReady,
    ModelGatewayCredentialUnavailable,
    ModelGatewayPolicyPersistenceError,
)
from agent_platform.platform.runs.entities import Run
from agent_platform.runtimes.recovery import RuntimeRecoveryTransient
from agent_platform.sandbox.entities import SandboxLease, SandboxScope
from agent_platform.sandbox.ports import RunExecutionEnvironment
from agent_platform.workers import runtime_composition as runtime_composition_module
from agent_platform.workers.runtime_composition import (
    ComposedRuntimeResolver,
    InvalidKnowledgeRuntimeResponse,
    KnowledgeRuntimeNotConfigured,
    KnowledgeRuntimeRequestRejected,
    ModelGatewayUnavailable,
    PermanentRuntimePreparationError,
    PlatformModelResolver,
    PlatformRuntimeSelector,
    PublishedModel,
    PublishedRuntimeCapabilities,
    TransientRuntimePreparationError,
    UntrustedRuntimeDefinition,
    extend_runtime_definition,
)


class EmptyArtifactStorage:
    async def put(self, *, key: str, content: bytes, media_type: str) -> None:
        del key, content, media_type

    async def get(self, *, key: str) -> bytes:
        raise KeyError(key)

    async def delete(self, *, key: str) -> None:
        del key


class EmptyStorage:
    async def get(self, *, key: str) -> bytes:
        raise AssertionError(key)


class RecordingWorkspace:
    def __init__(self) -> None:
        self.writes: list[tuple[str, bytes]] = []

    async def write_file(self, *, path: str, content: bytes) -> None:
        self.writes.append((path, content))


class RecordingBackend(SandboxBackendProtocol):
    def __init__(self) -> None:
        self.detach_calls = 0

    async def aclose(self) -> None:
        self.detach_calls += 1


class RecordingSandboxManager:
    def __init__(self) -> None:
        self.scope: SandboxScope | None = None
        self.ttl: timedelta | None = None
        self.workspace = RecordingWorkspace()
        self.backend = RecordingBackend()
        self.deleted: list[tuple[UUID, SandboxScope]] = []
        self.reconnected: list[SandboxScope] = []
        self.delete_error: Exception | None = None

    async def acquire(self, *, scope: SandboxScope, ttl: timedelta):
        self.scope = scope
        self.ttl = ttl
        lease = SandboxLease.create(scope=scope, provider="test", ttl=ttl).activate("box")
        return RunExecutionEnvironment(
            lease=lease,
            workspace=self.workspace,
            backend=self.backend,
        )

    async def delete(self, *, lease_id: UUID, scope: SandboxScope):
        self.deleted.append((lease_id, scope))
        if self.delete_error is not None:
            raise self.delete_error

    async def reconnect_active(
        self,
        *,
        scope: SandboxScope,
        ttl: timedelta,
    ):
        del ttl
        self.reconnected.append(scope)
        lease = SandboxLease.create(scope=scope, provider="test", ttl=timedelta(hours=1))
        return RunExecutionEnvironment(
            lease=lease.activate("box"),
            workspace=self.workspace,
            backend=self.backend,
        )


class RecordingSelector:
    def __init__(
        self,
        *,
        fail: bool = False,
        error: Exception | None = None,
    ) -> None:
        self.selection: dict[str, object] | None = None
        self.runtime = object()
        self.fail = fail
        self.error = error

    def select(self, **selection):
        self.selection = selection
        if self.error is not None:
            raise self.error
        if self.fail:
            raise RuntimeError("selection failed")
        return self.runtime


class UnusedGateway:
    async def invoke(self, invocation, context):
        raise AssertionError((invocation, context))


class RecordingKnowledgeProvider:
    provider_name = "fake-knowledge"

    def __init__(self) -> None:
        self.retrieve_calls: list[tuple[str, list[str], KnowledgeRetrievalConfig | None]] = []

    async def retrieve(
        self,
        *,
        question: str,
        dataset_ids: list[str],
        options: KnowledgeRetrievalConfig | None = None,
    ) -> KnowledgeSearchResult:
        self.retrieve_calls.append((question, dataset_ids, options))
        return KnowledgeSearchResult(
            total=2,
            citations=[
                KnowledgeCitation(
                    chunk_id="allowed-chunk",
                    document_id="doc-1",
                    document_name="handbook.pdf",
                    dataset_id=dataset_ids[0],
                    content="年假为十天",
                    score=0.91,
                    metadata={"department": "HR"},
                ),
                KnowledgeCitation(
                    chunk_id="foreign-chunk",
                    document_id="doc-foreign",
                    document_name="foreign.pdf",
                    dataset_id="foreign-dataset",
                    content="不得泄露的其他租户片段",
                    score=0.99,
                    metadata={},
                ),
            ],
        )


class FailingKnowledgeProvider:
    provider_name = "fake-knowledge"

    def __init__(self, error: Exception) -> None:
        self.error = error

    async def retrieve(
        self,
        *,
        question: str,
        dataset_ids: list[str],
        options: KnowledgeRetrievalConfig | None = None,
    ) -> KnowledgeSearchResult:
        del question, dataset_ids, options
        raise self.error


async def seed_knowledge_base(
    session_factory,
    *,
    run: Run,
    knowledge_base_id: UUID,
    provider_id: str,
) -> None:
    async with session_factory() as session:
        session.add(
            KnowledgeBaseRecord(
                id=knowledge_base_id,
                tenant_id=run.tenant_id,
                name="制度库",
                description="员工制度",
                provider="fake-knowledge",
                provider_id=provider_id,
                created_by=run.created_by,
                created_at=datetime.now(UTC),
            )
        )
        await session.commit()


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    load_database_models()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


def autonomous_definition(**changes: object) -> dict[str, object]:
    return {
        "work_mode": "autonomous",
        "model": {"kind": "gateway_alias", "alias": "general-purpose"},
        "skill_ids": [],
        "tool_ids": [],
        **changes,
    }


def injected_model_resolver() -> tuple[PlatformModelResolver, GenericFakeChatModel]:
    model = GenericFakeChatModel(messages=iter(["ok"]))
    return (
        PlatformModelResolver(injected_models={"general-purpose": model}),
        model,
    )


class FakeWorkflowSpecLoader:
    def __init__(self, graph: dict[str, object] | None) -> None:
        from agent_platform.platform.workflows.graph_spec import parse_workflow_graph

        self._spec = parse_workflow_graph(graph) if graph is not None else None
        self.calls: list[tuple[UUID, UUID, int]] = []

    async def load(self, *, tenant_id: UUID, workflow_id: UUID, version: int):
        self.calls.append((tenant_id, workflow_id, version))
        return self._spec


@pytest.mark.asyncio
async def test_composed_resolver_runs_registered_workflow_to_completion(
    session_factory,
) -> None:
    """流程员工：定义 → 加载注册图 → LangGraph 编排跑到终态（真实执行内核）。"""

    from langgraph.checkpoint.memory import InMemorySaver

    from agent_platform.platform.runs.entities import RunStatus
    from agent_platform.runtimes.base import RuntimeStartRequest

    run = Run.create(
        tenant_id=uuid4(),
        employee_id=uuid4(),
        employee_version=1,
        created_by=uuid4(),
        input_data={"topic": "报销"},
    )
    workflow_id = uuid4()
    definition = autonomous_definition(
        work_mode="workflow",
        workflow_id=str(workflow_id),
        workflow_version=2,
    )
    loader = FakeWorkflowSpecLoader(
        {
            "entrypoint": "answer",
            "nodes": [
                {"name": "answer", "type": "agent", "config": {"prompt": "答复"}, "next": None}
            ],
        }
    )
    manager = RecordingSandboxManager()
    model_resolver, _ = injected_model_resolver()
    resolver = ComposedRuntimeResolver(
        session_factory=session_factory,
        skill_storage=EmptyStorage(),
        sandbox_manager=manager,
        gateway=UnusedGateway(),
        runtime_selector=PlatformRuntimeSelector(checkpointer=InMemorySaver()),
        workflow_spec_loader=loader,
        model_resolver=model_resolver,
    )

    prepared = await resolver.resolve(run, definition)
    assert loader.calls == [(run.tenant_id, workflow_id, 2)]

    state = await prepared.runtime.start(
        RuntimeStartRequest(
            run_id=run.id,
            tenant_id=run.tenant_id,
            user_id=run.created_by,
            employee_id=run.employee_id,
            thread_id=run.thread_id,
            employee_definition=prepared.employee_definition,
            input_data=run.input_data,
        )
    )
    assert state.status is RunStatus.COMPLETED
    assert state.data["output"] == "ok"
    await prepared.close()


@pytest.mark.asyncio
async def test_composed_resolver_fails_closed_for_unregistered_workflow(
    session_factory,
) -> None:
    from agent_platform.workers.runtime_composition import WorkflowNotRegistered

    run = Run.create(
        tenant_id=uuid4(),
        employee_id=uuid4(),
        employee_version=1,
        created_by=uuid4(),
        input_data={},
    )
    definition = autonomous_definition(
        work_mode="workflow",
        workflow_id=str(uuid4()),
        workflow_version=1,
    )
    manager = RecordingSandboxManager()
    model_resolver, _ = injected_model_resolver()
    resolver = ComposedRuntimeResolver(
        session_factory=session_factory,
        skill_storage=EmptyStorage(),
        sandbox_manager=manager,
        gateway=UnusedGateway(),
        runtime_selector=PlatformRuntimeSelector(),
        workflow_spec_loader=FakeWorkflowSpecLoader(None),
        model_resolver=model_resolver,
    )

    with pytest.raises(WorkflowNotRegistered):
        await resolver.resolve(run, definition)
    # 未命中注册表时在获取沙盒前失败，不应申请沙盒。
    assert manager.scope is None


def test_published_capabilities_parse_trusted_model_and_version_bound_ids() -> None:
    skill_id = uuid4()
    tool_id = uuid4()
    knowledge_base_id = uuid4()

    capabilities = PublishedRuntimeCapabilities.from_definition(
        autonomous_definition(
            skill_ids=[str(skill_id)],
            tool_ids=[str(tool_id)],
            knowledge_base_ids=[str(knowledge_base_id)],
            capabilities={"conversation": True},
        )
    )

    assert capabilities.model == PublishedModel(kind="gateway_alias", alias="general-purpose")
    assert capabilities.skill_ids == (skill_id,)
    assert capabilities.tool_ids == (tool_id,)
    assert capabilities.knowledge_base_ids == (knowledge_base_id,)


def test_workflow_without_published_identity_fails_closed() -> None:
    with pytest.raises(UntrustedRuntimeDefinition, match="workflow_id"):
        PublishedRuntimeCapabilities.from_definition(
            {
                **autonomous_definition(),
                "work_mode": "workflow",
            }
        )


@pytest.mark.parametrize(
    "definition",
    [
        {"work_mode": "autonomous"},
        {
            **autonomous_definition(),
            "work_mode": "workflow",
            "workflow_id": "not-a-uuid",
            "workflow_version": 1,
        },
        {**autonomous_definition(), "skill_ids": "not-a-list"},
        {**autonomous_definition(), "tool_ids": ["not-a-uuid"]},
        {**autonomous_definition(), "knowledge_base_ids": ["not-a-uuid"]},
    ],
)
def test_all_invalid_published_fields_are_permanent_preparation_errors(
    definition: dict[str, object],
) -> None:
    with pytest.raises(UntrustedRuntimeDefinition):
        PublishedRuntimeCapabilities.from_definition(definition)


@pytest.mark.asyncio
async def test_model_resolver_supports_provider_neutral_host_side_injection() -> None:
    fake_model = GenericFakeChatModel(messages=iter(["ok"]))
    resolver = PlatformModelResolver(injected_models={"general-purpose": fake_model})

    assert (
        await resolver.resolve(
            PublishedModel(kind="gateway_alias", alias="general-purpose"),
            tenant_id=uuid4(),
        )
        is fake_model
    )


@pytest.mark.asyncio
async def test_model_resolver_fails_fast_without_a_gateway_factory() -> None:
    resolver = PlatformModelResolver()

    with pytest.raises(ModelGatewayUnavailable, match="gateway"):
        await resolver.resolve(
            PublishedModel(kind="gateway_alias", alias="general-purpose"),
            tenant_id=uuid4(),
        )


@pytest.mark.asyncio
async def test_model_resolver_rejects_an_alias_outside_the_platform_allowlist() -> None:
    factory_calls: list[str] = []
    model = GenericFakeChatModel(messages=iter(["ok"]))
    resolver = PlatformModelResolver(
        model_factory=lambda alias, api_key: factory_calls.append(alias) or model
    )

    with pytest.raises(ModelGatewayUnavailable, match="allowlist"):
        await resolver.resolve(
            PublishedModel(kind="gateway_alias", alias="unconfigured-alias"),
            tenant_id=uuid4(),
        )

    assert factory_calls == []


def test_prepare_layer_is_the_only_source_of_runtime_skill_paths() -> None:
    definition = autonomous_definition()

    prepared = extend_runtime_definition(
        definition,
        skill_paths=["/skills/trusted/version"],
    )

    assert prepared == {**definition, "skill_paths": ["/skills/trusted/version"]}
    assert "skill_paths" not in definition


@pytest.mark.asyncio
async def test_composed_resolver_uses_one_environment_for_skills_and_runtime(
    session_factory,
) -> None:
    run = Run.create(
        tenant_id=uuid4(),
        employee_id=uuid4(),
        employee_version=1,
        created_by=uuid4(),
        input_data={"model": {"provider": "untrusted", "name": "input-override"}},
    )
    manager = RecordingSandboxManager()
    selector = RecordingSelector()
    model_resolver, model = injected_model_resolver()
    resolver = ComposedRuntimeResolver(
        session_factory=session_factory,
        skill_storage=EmptyStorage(),
        sandbox_manager=manager,
        gateway=UnusedGateway(),
        runtime_selector=selector,
        model_resolver=model_resolver,
    )

    prepared = await resolver.resolve(run, autonomous_definition())

    assert manager.scope == SandboxScope(
        tenant_id=run.tenant_id,
        user_id=run.created_by,
        run_id=run.id,
        thread_id=run.thread_id,
    )
    assert selector.selection is not None
    environment = selector.selection["environment"]
    assert isinstance(environment, RunExecutionEnvironment)
    assert environment.workspace is manager.workspace
    assert environment.backend is manager.backend
    assert selector.selection["model"] is model
    assert prepared.employee_definition["skill_paths"] == []

    await prepared.close()
    await prepared.close()
    assert len(manager.deleted) == 1


@pytest.mark.asyncio
async def test_composed_resolver_materializes_attachments_and_installs_artifact_tool(
    session_factory,
    monkeypatch,
) -> None:
    run = Run.create(
        tenant_id=uuid4(),
        employee_id=uuid4(),
        employee_version=1,
        created_by=uuid4(),
        input_data={},
    )
    manager = RecordingSandboxManager()
    selector = RecordingSelector()
    model_resolver, _ = injected_model_resolver()
    materialized: list[tuple[UUID, UUID, object]] = []

    async def record_materialize(self, *, tenant_id, run_id, workspace):
        del self
        materialized.append((tenant_id, run_id, workspace))

    monkeypatch.setattr(
        "agent_platform.platform.artifacts.services.TaskAttachmentService.materialize",
        record_materialize,
    )
    resolver = ComposedRuntimeResolver(
        session_factory=session_factory,
        skill_storage=EmptyStorage(),
        artifact_storage=EmptyArtifactStorage(),
        sandbox_manager=manager,
        gateway=UnusedGateway(),
        runtime_selector=selector,
        model_resolver=model_resolver,
    )

    await resolver.resolve(run, autonomous_definition())

    assert materialized == [(run.tenant_id, run.id, manager.workspace)]
    assert selector.selection is not None
    assert "create_artifact" in {tool.name for tool in selector.selection["tools"]}


@pytest.mark.asyncio
async def test_composed_resolver_retrieves_bound_knowledge_and_filters_provider_overreach(
    session_factory,
) -> None:
    knowledge_base_id = uuid4()
    provider_dataset_id = "dataset-allowed"
    run = Run.create(
        tenant_id=uuid4(),
        employee_id=uuid4(),
        employee_version=1,
        created_by=uuid4(),
        input_data={"question": "年假有几天"},
    )
    async with session_factory() as session:
        session.add(
            KnowledgeBaseRecord(
                id=knowledge_base_id,
                tenant_id=run.tenant_id,
                name="制度库",
                description="员工制度",
                provider="fake-knowledge",
                provider_id=provider_dataset_id,
                created_by=run.created_by,
                created_at=datetime.now(UTC),
            )
        )
        await session.commit()

    provider = RecordingKnowledgeProvider()
    manager = RecordingSandboxManager()
    selector = RecordingSelector()
    model_resolver, _ = injected_model_resolver()
    resolver = ComposedRuntimeResolver(
        session_factory=session_factory,
        skill_storage=EmptyStorage(),
        sandbox_manager=manager,
        gateway=UnusedGateway(),
        runtime_selector=selector,
        model_resolver=model_resolver,
        knowledge_provider_registry=KnowledgeProviderRegistry([provider]),
    )

    prepared = await resolver.resolve(
        run,
        autonomous_definition(knowledge_base_ids=[str(knowledge_base_id)]),
    )

    assert provider.retrieve_calls == [
        ("年假有几天", [provider_dataset_id], KnowledgeRetrievalConfig())
    ]
    assert prepared.knowledge_context is not None
    assert [citation.chunk_id for citation in prepared.knowledge_context.citations] == [
        "allowed-chunk"
    ]


@pytest.mark.asyncio
async def test_missing_published_knowledge_reference_fails_before_sandbox_side_effect(
    session_factory,
) -> None:
    run = Run.create(
        tenant_id=uuid4(),
        employee_id=uuid4(),
        employee_version=1,
        created_by=uuid4(),
        input_data={"question": "年假有几天"},
    )
    manager = RecordingSandboxManager()
    model_resolver, _ = injected_model_resolver()
    resolver = ComposedRuntimeResolver(
        session_factory=session_factory,
        skill_storage=EmptyStorage(),
        sandbox_manager=manager,
        gateway=UnusedGateway(),
        runtime_selector=RecordingSelector(),
        model_resolver=model_resolver,
        knowledge_provider_registry=KnowledgeProviderRegistry([RecordingKnowledgeProvider()]),
    )

    with pytest.raises(UntrustedRuntimeDefinition, match="published knowledge"):
        await resolver.resolve(run, autonomous_definition(knowledge_base_ids=[str(uuid4())]))

    assert manager.scope is None


@pytest.mark.asyncio
async def test_published_knowledge_retrieval_config_is_honored_per_version(
    session_factory,
) -> None:
    knowledge_base_id = uuid4()
    run = Run.create(
        tenant_id=uuid4(),
        employee_id=uuid4(),
        employee_version=2,
        created_by=uuid4(),
        input_data={"question": "年假有几天"},
    )
    await seed_knowledge_base(
        session_factory,
        run=run,
        knowledge_base_id=knowledge_base_id,
        provider_id="dataset-allowed",
    )
    provider = RecordingKnowledgeProvider()
    model_resolver, _ = injected_model_resolver()
    resolver = ComposedRuntimeResolver(
        session_factory=session_factory,
        skill_storage=EmptyStorage(),
        sandbox_manager=RecordingSandboxManager(),
        gateway=UnusedGateway(),
        runtime_selector=RecordingSelector(),
        model_resolver=model_resolver,
        knowledge_provider_registry=KnowledgeProviderRegistry([provider]),
    )

    await resolver.resolve(
        run,
        autonomous_definition(
            knowledge_base_ids=[str(knowledge_base_id)],
            knowledge_retrieval={
                "page_size": 3,
                "similarity_threshold": 0.5,
                "vector_similarity_weight": 0.8,
                "top_k": 64,
                "keyword": True,
                "rerank_id": "BAAI/bge-reranker-v2-m3",
                "metadata_condition": {
                    "logic": "and",
                    "conditions": [
                        {"name": "department", "comparison_operator": "=", "value": "HR"},
                    ],
                },
            },
        ),
    )

    assert provider.retrieve_calls == [
        (
            "年假有几天",
            ["dataset-allowed"],
            KnowledgeRetrievalConfig(
                page_size=3,
                similarity_threshold=0.5,
                vector_similarity_weight=0.8,
                top_k=64,
                keyword=True,
                rerank_id="BAAI/bge-reranker-v2-m3",
                metadata_condition=KnowledgeMetadataCondition(
                    logic="and",
                    conditions=[
                        KnowledgeMetadataFilterCondition(
                            name="department", comparison_operator="=", value="HR"
                        )
                    ],
                ),
            ),
        )
    ]


@pytest.mark.asyncio
async def test_invalid_published_knowledge_retrieval_config_fails_closed(
    session_factory,
) -> None:
    knowledge_base_id = uuid4()
    run = Run.create(
        tenant_id=uuid4(),
        employee_id=uuid4(),
        employee_version=1,
        created_by=uuid4(),
        input_data={"question": "年假有几天"},
    )
    await seed_knowledge_base(
        session_factory,
        run=run,
        knowledge_base_id=knowledge_base_id,
        provider_id="dataset-allowed",
    )
    manager = RecordingSandboxManager()
    model_resolver, _ = injected_model_resolver()
    resolver = ComposedRuntimeResolver(
        session_factory=session_factory,
        skill_storage=EmptyStorage(),
        sandbox_manager=manager,
        gateway=UnusedGateway(),
        runtime_selector=RecordingSelector(),
        model_resolver=model_resolver,
        knowledge_provider_registry=KnowledgeProviderRegistry([RecordingKnowledgeProvider()]),
    )

    with pytest.raises(UntrustedRuntimeDefinition):
        await resolver.resolve(
            run,
            autonomous_definition(
                knowledge_base_ids=[str(knowledge_base_id)],
                knowledge_retrieval={"page_size": 0},
            ),
        )

    assert manager.scope is None


@pytest.mark.asyncio
async def test_unregistered_knowledge_provider_is_a_permanent_configuration_error(
    session_factory,
) -> None:
    knowledge_base_id = uuid4()
    run = Run.create(
        tenant_id=uuid4(),
        employee_id=uuid4(),
        employee_version=1,
        created_by=uuid4(),
        input_data={"question": "年假有几天"},
    )
    await seed_knowledge_base(
        session_factory,
        run=run,
        knowledge_base_id=knowledge_base_id,
        provider_id="dataset-allowed",
    )

    class OtherProvider(RecordingKnowledgeProvider):
        provider_name = "another-provider"

    manager = RecordingSandboxManager()
    model_resolver, _ = injected_model_resolver()
    resolver = ComposedRuntimeResolver(
        session_factory=session_factory,
        skill_storage=EmptyStorage(),
        sandbox_manager=manager,
        gateway=UnusedGateway(),
        runtime_selector=RecordingSelector(),
        model_resolver=model_resolver,
        knowledge_provider_registry=KnowledgeProviderRegistry([OtherProvider()]),
    )

    with pytest.raises(KnowledgeRuntimeNotConfigured) as captured:
        await resolver.resolve(
            run,
            autonomous_definition(knowledge_base_ids=[str(knowledge_base_id)]),
        )

    assert isinstance(captured.value, PermanentRuntimePreparationError)
    assert captured.value.code == "knowledge_provider_not_configured"
    assert manager.scope is None


@pytest.mark.asyncio
async def test_knowledge_provider_outage_is_transient_and_never_a_permanent_definition_error(
    session_factory,
) -> None:
    knowledge_base_id = uuid4()
    run = Run.create(
        tenant_id=uuid4(),
        employee_id=uuid4(),
        employee_version=1,
        created_by=uuid4(),
        input_data={"question": "年假有几天"},
    )
    await seed_knowledge_base(
        session_factory,
        run=run,
        knowledge_base_id=knowledge_base_id,
        provider_id="dataset-allowed",
    )
    manager = RecordingSandboxManager()
    model_resolver, _ = injected_model_resolver()
    resolver = ComposedRuntimeResolver(
        session_factory=session_factory,
        skill_storage=EmptyStorage(),
        sandbox_manager=manager,
        gateway=UnusedGateway(),
        runtime_selector=RecordingSelector(),
        model_resolver=model_resolver,
        knowledge_provider_registry=KnowledgeProviderRegistry(
            [
                FailingKnowledgeProvider(
                    KnowledgeProviderUnavailable("connection refused to 10.0.0.9:9380")
                )
            ]
        ),
    )

    with pytest.raises(TransientRuntimePreparationError) as captured:
        await resolver.resolve(
            run,
            autonomous_definition(knowledge_base_ids=[str(knowledge_base_id)]),
        )

    assert not isinstance(captured.value, PermanentRuntimePreparationError)
    assert "10.0.0.9" not in str(captured.value)
    assert manager.scope is None


@pytest.mark.asyncio
async def test_malformed_knowledge_provider_response_fails_with_a_stable_controlled_code(
    session_factory,
) -> None:
    knowledge_base_id = uuid4()
    run = Run.create(
        tenant_id=uuid4(),
        employee_id=uuid4(),
        employee_version=1,
        created_by=uuid4(),
        input_data={"question": "年假有几天"},
    )
    await seed_knowledge_base(
        session_factory,
        run=run,
        knowledge_base_id=knowledge_base_id,
        provider_id="dataset-allowed",
    )
    manager = RecordingSandboxManager()
    model_resolver, _ = injected_model_resolver()
    resolver = ComposedRuntimeResolver(
        session_factory=session_factory,
        skill_storage=EmptyStorage(),
        sandbox_manager=manager,
        gateway=UnusedGateway(),
        runtime_selector=RecordingSelector(),
        model_resolver=model_resolver,
        knowledge_provider_registry=KnowledgeProviderRegistry(
            [
                FailingKnowledgeProvider(
                    InvalidKnowledgeProviderResponse("chunks 字段缺失: raw=<html>upstream</html>")
                )
            ]
        ),
    )

    with pytest.raises(InvalidKnowledgeRuntimeResponse) as captured:
        await resolver.resolve(
            run,
            autonomous_definition(knowledge_base_ids=[str(knowledge_base_id)]),
        )

    assert isinstance(captured.value, PermanentRuntimePreparationError)
    assert captured.value.code == "invalid_knowledge_provider_response"
    assert "<html>" not in str(captured.value)
    assert manager.scope is None


@pytest.mark.asyncio
async def test_rejected_knowledge_provider_request_is_permanent_with_stable_code(
    session_factory,
) -> None:
    knowledge_base_id = uuid4()
    run = Run.create(
        tenant_id=uuid4(),
        employee_id=uuid4(),
        employee_version=1,
        created_by=uuid4(),
        input_data={"question": "年假有几天"},
    )
    await seed_knowledge_base(
        session_factory,
        run=run,
        knowledge_base_id=knowledge_base_id,
        provider_id="dataset-allowed",
    )
    manager = RecordingSandboxManager()
    model_resolver, _ = injected_model_resolver()
    resolver = ComposedRuntimeResolver(
        session_factory=session_factory,
        skill_storage=EmptyStorage(),
        sandbox_manager=manager,
        gateway=UnusedGateway(),
        runtime_selector=RecordingSelector(),
        model_resolver=model_resolver,
        knowledge_provider_registry=KnowledgeProviderRegistry(
            [
                FailingKnowledgeProvider(
                    KnowledgeProviderRequestRejected(
                        "知识供应商拒绝了请求（HTTP 401）api-key=sk-secret"
                    )
                )
            ]
        ),
    )

    with pytest.raises(KnowledgeRuntimeRequestRejected) as captured:
        await resolver.resolve(
            run,
            autonomous_definition(knowledge_base_ids=[str(knowledge_base_id)]),
        )

    assert isinstance(captured.value, PermanentRuntimePreparationError)
    assert not isinstance(captured.value, TransientRuntimePreparationError)
    assert captured.value.code == "knowledge_provider_rejected"
    assert "sk-secret" not in str(captured.value)
    assert manager.scope is None


@pytest.mark.asyncio
async def test_composed_resolver_recovers_the_existing_sandbox_without_acquiring_another(
    session_factory,
) -> None:
    run = Run.create(
        tenant_id=uuid4(),
        employee_id=uuid4(),
        employee_version=1,
        created_by=uuid4(),
        input_data={},
    )
    manager = RecordingSandboxManager()
    selector = RecordingSelector()
    model_resolver, _ = injected_model_resolver()
    resolver = ComposedRuntimeResolver(
        session_factory=session_factory,
        skill_storage=EmptyStorage(),
        sandbox_manager=manager,
        gateway=UnusedGateway(),
        runtime_selector=selector,
        model_resolver=model_resolver,
    )

    prepared = await resolver.recover(run, autonomous_definition())

    scope = SandboxScope(
        tenant_id=run.tenant_id,
        user_id=run.created_by,
        run_id=run.id,
        thread_id=run.thread_id,
    )
    assert manager.scope is None
    assert manager.reconnected == [scope]
    assert prepared.environment.workspace is manager.workspace


@pytest.mark.asyncio
async def test_initial_transient_preparation_deletes_new_environment(
    session_factory,
) -> None:
    run = Run.create(
        tenant_id=uuid4(),
        employee_id=uuid4(),
        employee_version=1,
        created_by=uuid4(),
        input_data={},
    )
    manager = RecordingSandboxManager()
    model_resolver, _ = injected_model_resolver()
    resolver = ComposedRuntimeResolver(
        session_factory=session_factory,
        skill_storage=EmptyStorage(),
        sandbox_manager=manager,
        gateway=UnusedGateway(),
        runtime_selector=RecordingSelector(fail=True),
        model_resolver=model_resolver,
    )

    with pytest.raises(RuntimeError, match="selection failed"):
        await resolver.resolve(run, autonomous_definition())

    assert len(manager.deleted) == 1
    assert manager.backend.detach_calls == 0


@pytest.mark.asyncio
async def test_initial_permanent_preparation_defers_delete_until_failure_is_persisted(
    session_factory,
) -> None:
    run = Run.create(
        tenant_id=uuid4(),
        employee_id=uuid4(),
        employee_version=1,
        created_by=uuid4(),
        input_data={},
    )
    manager = RecordingSandboxManager()
    model_resolver, _ = injected_model_resolver()
    resolver = ComposedRuntimeResolver(
        session_factory=session_factory,
        skill_storage=EmptyStorage(),
        sandbox_manager=manager,
        gateway=UnusedGateway(),
        runtime_selector=RecordingSelector(error=UntrustedRuntimeDefinition("permanent")),
        model_resolver=model_resolver,
    )

    with pytest.raises(UntrustedRuntimeDefinition) as captured:
        await resolver.resolve(run, autonomous_definition())

    assert manager.deleted == []
    await captured.value.cleanup_after_failure()
    assert len(manager.deleted) == 1


@pytest.mark.asyncio
async def test_transient_recovery_composition_detaches_without_deleting_and_can_retry(
    session_factory,
) -> None:
    run = Run.create(
        tenant_id=uuid4(),
        employee_id=uuid4(),
        employee_version=1,
        created_by=uuid4(),
        input_data={},
    )
    manager = RecordingSandboxManager()
    model_resolver, _ = injected_model_resolver()
    resolver = ComposedRuntimeResolver(
        session_factory=session_factory,
        skill_storage=EmptyStorage(),
        sandbox_manager=manager,
        gateway=UnusedGateway(),
        runtime_selector=RecordingSelector(fail=True),
        model_resolver=model_resolver,
    )

    with pytest.raises(RuntimeRecoveryTransient):
        await resolver.recover(run, autonomous_definition())

    assert manager.deleted == []
    assert manager.backend.detach_calls == 1

    resolver._runtime_selector.fail = False
    prepared = await resolver.recover(run, autonomous_definition())
    assert prepared.runtime is resolver._runtime_selector.runtime
    assert manager.deleted == []


@pytest.mark.asyncio
async def test_attachment_materialization_error_deletes_new_sandbox(
    session_factory,
    monkeypatch,
) -> None:
    run = Run.create(
        tenant_id=uuid4(),
        employee_id=uuid4(),
        employee_version=1,
        created_by=uuid4(),
        input_data={},
    )
    manager = RecordingSandboxManager()
    model_resolver, _ = injected_model_resolver()

    async def fail_materialization(self, *, tenant_id, run_id, workspace):
        del self, tenant_id, run_id, workspace
        raise RuntimeError("materialization failed")

    monkeypatch.setattr(
        "agent_platform.platform.artifacts.services.TaskAttachmentService.materialize",
        fail_materialization,
    )
    resolver = ComposedRuntimeResolver(
        session_factory=session_factory,
        skill_storage=EmptyStorage(),
        artifact_storage=EmptyArtifactStorage(),
        sandbox_manager=manager,
        gateway=UnusedGateway(),
        runtime_selector=RecordingSelector(),
        model_resolver=model_resolver,
    )

    with pytest.raises(RuntimeError, match="materialization failed"):
        await resolver.resolve(run, autonomous_definition())

    assert len(manager.deleted) == 1
    assert manager.backend.detach_calls == 0


@pytest.mark.asyncio
async def test_attachment_materialization_cancellation_deletes_new_sandbox(
    session_factory,
    monkeypatch,
) -> None:
    run = Run.create(
        tenant_id=uuid4(),
        employee_id=uuid4(),
        employee_version=1,
        created_by=uuid4(),
        input_data={},
    )
    manager = RecordingSandboxManager()
    model_resolver, _ = injected_model_resolver()

    async def cancel_materialization(self, *, tenant_id, run_id, workspace):
        del self, tenant_id, run_id, workspace
        raise asyncio.CancelledError

    monkeypatch.setattr(
        "agent_platform.platform.artifacts.services.TaskAttachmentService.materialize",
        cancel_materialization,
    )
    resolver = ComposedRuntimeResolver(
        session_factory=session_factory,
        skill_storage=EmptyStorage(),
        artifact_storage=EmptyArtifactStorage(),
        sandbox_manager=manager,
        gateway=UnusedGateway(),
        runtime_selector=RecordingSelector(),
        model_resolver=model_resolver,
    )

    with pytest.raises(asyncio.CancelledError):
        await resolver.resolve(run, autonomous_definition())

    assert len(manager.deleted) == 1
    assert manager.backend.detach_calls == 0


@pytest.mark.asyncio
async def test_untrusted_definition_is_rejected_before_sandbox_side_effect(
    session_factory,
) -> None:
    run = Run.create(
        tenant_id=uuid4(),
        employee_id=uuid4(),
        employee_version=1,
        created_by=uuid4(),
        input_data={},
    )
    manager = RecordingSandboxManager()
    resolver = ComposedRuntimeResolver(
        session_factory=session_factory,
        skill_storage=EmptyStorage(),
        sandbox_manager=manager,
        gateway=UnusedGateway(),
        runtime_selector=RecordingSelector(),
    )

    with pytest.raises(UntrustedRuntimeDefinition):
        await resolver.resolve(
            run,
            autonomous_definition(skill_paths=["/host/untrusted"]),
        )

    assert manager.scope is None


def test_selector_routes_only_published_workflow_references() -> None:
    from agent_platform.platform.workflows.graph_spec import parse_workflow_graph
    from agent_platform.runtimes.langgraph import LangGraphRuntime
    from agent_platform.workers.runtime_composition import WorkflowNotRegistered

    calls: list[tuple[object, ...]] = []
    selector = PlatformRuntimeSelector(
        autonomous_factory=lambda tools, environment, model: (
            calls.append(("deep-agent", environment, model)) or object()
        ),
        # 子智能体工厂无副作用即可（本用例不跑图，只验证路由与构建类型）。
        subagent_runner_factory=lambda **kwargs: (lambda **inner: None),
    )
    manager = RecordingSandboxManager()
    scope = SandboxScope(tenant_id=uuid4(), user_id=uuid4(), run_id=uuid4(), thread_id="thread")
    lease = SandboxLease.create(scope=scope, provider="test", ttl=timedelta(hours=1))
    environment = RunExecutionEnvironment(
        lease=lease.activate("box"),
        workspace=manager.workspace,
        backend=manager.backend,
    )
    autonomous = PublishedRuntimeCapabilities.from_definition(autonomous_definition())
    workflow_id = uuid4()
    workflow = PublishedRuntimeCapabilities.from_definition(
        {
            **autonomous_definition(),
            "work_mode": "workflow",
            "workflow_id": str(workflow_id),
            "workflow_version": 3,
        }
    )
    model = GenericFakeChatModel(messages=iter(["ok"]))
    spec = parse_workflow_graph(
        {
            "entrypoint": "a",
            "nodes": [{"name": "a", "type": "agent", "config": {"prompt": "hi"}, "next": None}],
        }
    )

    selector.select(capabilities=autonomous, tools=[], environment=environment, model=model)
    workflow_runtime = selector.select(
        capabilities=workflow,
        tools=[],
        environment=environment,
        model=model,
        workflow_spec=spec,
    )

    assert calls == [("deep-agent", environment, model)]
    assert isinstance(workflow_runtime, LangGraphRuntime)

    # 引用工作流但未命中注册表（无 spec）→ 失败关闭。
    with pytest.raises(WorkflowNotRegistered):
        selector.select(
            capabilities=workflow,
            tools=[],
            environment=environment,
            model=model,
            workflow_spec=None,
        )


def test_default_autonomous_selector_injects_durable_checkpointer(monkeypatch) -> None:
    calls: list[tuple[object, ...]] = []
    checkpointer = object()
    monkeypatch.setattr(
        runtime_composition_module,
        "create_deep_agent_runtime",
        lambda tools, environment, model, durable_checkpointer, approval_store: (
            calls.append((environment, model, durable_checkpointer)) or object()
        ),
    )
    selector = PlatformRuntimeSelector(
        checkpointer=checkpointer,
    )
    manager = RecordingSandboxManager()
    scope = SandboxScope(
        tenant_id=uuid4(),
        user_id=uuid4(),
        run_id=uuid4(),
        thread_id="thread",
    )
    environment = RunExecutionEnvironment(
        lease=SandboxLease.create(
            scope=scope,
            provider="test",
            ttl=timedelta(hours=1),
        ).activate("box"),
        workspace=manager.workspace,
        backend=manager.backend,
    )
    model = GenericFakeChatModel(messages=iter(["ok"]))

    selector.select(
        capabilities=PublishedRuntimeCapabilities.from_definition(autonomous_definition()),
        tools=[],
        environment=environment,
        model=model,
    )

    assert calls == [(environment, model, checkpointer)]


@pytest.mark.asyncio
async def test_disabled_or_upstream_missing_tools_still_compose_for_call_time_denial(
    session_factory,
) -> None:
    """C09：禁用/上游移除只在调用点由 Gateway 拒绝，不使整个任务无法启动。"""
    from agent_platform.infrastructure.database.repositories.tools import (
        SqlAlchemyToolRepository,
    )
    from agent_platform.platform.tools.entities import (
        McpServer,
        McpTransport,
        Tool,
        ToolRiskLevel,
    )

    run = Run.create(
        tenant_id=uuid4(),
        employee_id=uuid4(),
        employee_version=1,
        created_by=uuid4(),
        input_data={},
    )
    server = McpServer.create(
        tenant_id=run.tenant_id,
        created_by=run.created_by,
        name="composition-mcp",
        transport=McpTransport.STREAMABLE_HTTP,
        endpoint="https://mcp.example.com/api",
        command=None,
        args=[],
        secret_reference=None,
        enabled=True,
    )
    disabled_tool = Tool.create(
        tenant_id=run.tenant_id,
        server_id=server.id,
        name="disabled_tool",
        description="",
        input_schema={"type": "object"},
        risk_level=ToolRiskLevel.READ,
        enabled=False,
    )
    missing_tool = Tool.create(
        tenant_id=run.tenant_id,
        server_id=server.id,
        name="missing_tool",
        description="",
        input_schema={"type": "object"},
        risk_level=ToolRiskLevel.READ,
        enabled=True,
    ).mark_upstream_missing(missing=True, at=__import__("datetime").datetime.now(
        __import__("datetime").UTC
    ))
    async with session_factory() as session:
        repository = SqlAlchemyToolRepository(session)
        await repository.add_server(server)
        await repository.add_tool(disabled_tool)
        await repository.add_tool(missing_tool)
        await session.commit()

    manager = RecordingSandboxManager()
    selector = RecordingSelector()
    model_resolver, _ = injected_model_resolver()
    resolver = ComposedRuntimeResolver(
        session_factory=session_factory,
        skill_storage=EmptyStorage(),
        sandbox_manager=manager,
        gateway=UnusedGateway(),
        runtime_selector=selector,
        model_resolver=model_resolver,
    )

    await resolver.resolve(
        run,
        autonomous_definition(
            tool_ids=[str(disabled_tool.id), str(missing_tool.id)],
        ),
    )

    assert selector.selection is not None
    tool_names = {tool.name for tool in selector.selection["tools"]}
    assert {"disabled_tool", "missing_tool"}.issubset(tool_names)


@pytest.mark.asyncio
async def test_deleted_tool_reference_still_fails_closed(session_factory) -> None:
    run = Run.create(
        tenant_id=uuid4(),
        employee_id=uuid4(),
        employee_version=1,
        created_by=uuid4(),
        input_data={},
    )
    manager = RecordingSandboxManager()
    selector = RecordingSelector()
    model_resolver, _ = injected_model_resolver()
    resolver = ComposedRuntimeResolver(
        session_factory=session_factory,
        skill_storage=EmptyStorage(),
        sandbox_manager=manager,
        gateway=UnusedGateway(),
        runtime_selector=selector,
        model_resolver=model_resolver,
    )

    with pytest.raises(UntrustedRuntimeDefinition):
        await resolver.resolve(
            run,
            autonomous_definition(tool_ids=[str(uuid4())]),
        )


@pytest.mark.asyncio
async def test_model_resolver_uses_the_tenant_attributable_key_not_a_shared_one() -> None:
    """C16：Worker 必须按租户解析凭据，网关调用因此可归因到企业。"""
    issued: list[tuple[str, str]] = []
    model = GenericFakeChatModel(messages=iter(["ok"]))

    class FakeCredentials:
        async def resolve(self, *, tenant_id: UUID, alias: str) -> SecretStr:
            return SecretStr(f"sk-tenant-{tenant_id}")

    def factory(alias: str, api_key: SecretStr) -> GenericFakeChatModel:
        issued.append((alias, api_key.get_secret_value()))
        return model

    resolver = PlatformModelResolver(
        model_factory=factory,
        tenant_credentials=FakeCredentials(),
    )
    tenant_id = uuid4()

    resolved = await resolver.resolve(
        PublishedModel(kind="gateway_alias", alias="general-purpose"),
        tenant_id=tenant_id,
    )

    assert resolved is model
    assert issued == [("general-purpose", f"sk-tenant-{tenant_id}")]


@pytest.mark.asyncio
async def test_model_resolver_fails_closed_when_the_tenant_has_no_credential() -> None:
    """撤销/未对账的租户：绝不回退共享 Key，必须是永久性的受控准备失败。"""
    factory_calls: list[str] = []

    class RevokedCredentials:
        async def resolve(self, *, tenant_id: UUID, alias: str) -> SecretStr:
            raise ModelGatewayCredentialUnavailable("model_gateway_disabled")

    resolver = PlatformModelResolver(
        model_factory=lambda alias, api_key: factory_calls.append(alias),
        tenant_credentials=RevokedCredentials(),
    )

    with pytest.raises(ModelGatewayUnavailable) as captured:
        await resolver.resolve(
            PublishedModel(kind="gateway_alias", alias="general-purpose"),
            tenant_id=uuid4(),
        )

    assert isinstance(captured.value, PermanentRuntimePreparationError)
    assert factory_calls == []


@pytest.mark.asyncio
async def test_model_resolver_requires_tenant_credentials_in_production_assembly() -> None:
    """没有装配租户凭据解析器时必须失败关闭，不得静默退回无归因调用。"""
    resolver = PlatformModelResolver(model_factory=lambda alias, api_key: None)

    with pytest.raises(ModelGatewayUnavailable):
        await resolver.resolve(
            PublishedModel(kind="gateway_alias", alias="general-purpose"),
            tenant_id=uuid4(),
        )


@pytest.mark.asyncio
async def test_provisioning_in_progress_is_transient_not_a_permanent_definition_error() -> None:
    """S2：pending 是秒级自愈的瞬态，必须交队列重投。

    断言归类而不只是异常类型——只断言 `raises(ModelGatewayUnavailable)` 抓不到
    「它 IS-A Permanent 因而让 Run 永久失败」这个真实缺陷。
    """

    class NotReadyCredentials:
        async def resolve(self, *, tenant_id: UUID, alias: str) -> SecretStr:
            raise ModelGatewayCredentialNotReady("model_gateway_provisioning_in_progress")

    resolver = PlatformModelResolver(
        model_factory=lambda alias, api_key: None,
        tenant_credentials=NotReadyCredentials(),
    )

    with pytest.raises(TransientRuntimePreparationError) as captured:
        await resolver.resolve(
            PublishedModel(kind="gateway_alias", alias="general-purpose"),
            tenant_id=uuid4(),
        )

    assert not isinstance(captured.value, PermanentRuntimePreparationError)


@pytest.mark.asyncio
async def test_configuration_defects_stay_permanent_and_are_not_retried_forever() -> None:
    """撤销/越权/对账确定失败属配置缺陷：重投永远不会好，必须永久失败。"""
    for code in (
        "model_gateway_disabled",
        "model_gateway_alias_not_allowed",
        "model_gateway_policy_not_provisioned",
        "model_gateway_provisioning_failed",
    ):

        class RejectingCredentials:
            def __init__(self, rejection: str) -> None:
                self._rejection = rejection

            async def resolve(self, *, tenant_id: UUID, alias: str) -> SecretStr:
                raise ModelGatewayCredentialUnavailable(self._rejection)

        resolver = PlatformModelResolver(
            model_factory=lambda alias, api_key: None,
            tenant_credentials=RejectingCredentials(code),
        )

        with pytest.raises(PermanentRuntimePreparationError) as captured:
            await resolver.resolve(
                PublishedModel(kind="gateway_alias", alias="general-purpose"),
                tenant_id=uuid4(),
            )
        assert captured.value.code == code


@pytest.mark.asyncio
async def test_database_jitter_during_credential_resolution_is_transient() -> None:
    """M1：凭据解析的 DB 抖动此前裸逃逸出模型解析层，既非瞬态也非永久。"""

    class FlakyCredentials:
        async def resolve(self, *, tenant_id: UUID, alias: str) -> SecretStr:
            raise ModelGatewayPolicyPersistenceError()

    resolver = PlatformModelResolver(
        model_factory=lambda alias, api_key: None,
        tenant_credentials=FlakyCredentials(),
    )

    with pytest.raises(TransientRuntimePreparationError):
        await resolver.resolve(
            PublishedModel(kind="gateway_alias", alias="general-purpose"),
            tenant_id=uuid4(),
        )


@pytest.mark.asyncio
async def test_corrupt_persisted_policy_is_permanent_not_retried() -> None:
    """数据损坏重投永远不会好：必须与 DB 抖动区分开。"""

    class CorruptCredentials:
        async def resolve(self, *, tenant_id: UUID, alias: str) -> SecretStr:
            raise CorruptModelGatewayPolicy()

    resolver = PlatformModelResolver(
        model_factory=lambda alias, api_key: None,
        tenant_credentials=CorruptCredentials(),
    )

    with pytest.raises(PermanentRuntimePreparationError):
        await resolver.resolve(
            PublishedModel(kind="gateway_alias", alias="general-purpose"),
            tenant_id=uuid4(),
        )


@pytest.mark.asyncio
async def test_composed_resolver_attaches_usage_capture_when_recorder_configured(
    session_factory,
) -> None:
    """C16 阶段二：装配了用量记录器时，交给执行内核的模型必须带上用量捕获回调，
    且不污染原（可能跨 run 复用的）注入模型实例，归属指向本 run。"""

    from agent_platform.platform.model_gateway.usage import ModelUsageRecord
    from agent_platform.workers.model_usage_capture import ModelUsageCaptureHandler

    class _CollectingRecorder:
        def __init__(self) -> None:
            self.records: list[ModelUsageRecord] = []

        async def record(self, record: ModelUsageRecord) -> None:
            self.records.append(record)

    run = Run.create(
        tenant_id=uuid4(),
        employee_id=uuid4(),
        employee_version=1,
        created_by=uuid4(),
        input_data={},
    )
    manager = RecordingSandboxManager()
    selector = RecordingSelector()
    model_resolver, model = injected_model_resolver()
    recorder = _CollectingRecorder()
    resolver = ComposedRuntimeResolver(
        session_factory=session_factory,
        skill_storage=EmptyStorage(),
        sandbox_manager=manager,
        gateway=UnusedGateway(),
        runtime_selector=selector,
        model_resolver=model_resolver,
        model_usage_recorder=recorder,
    )

    prepared = await resolver.resolve(run, autonomous_definition())

    selected_model = selector.selection["model"]
    # 不是原注入实例（用 model_copy 避免跨 run callback 累积）
    assert selected_model is not model
    assert model.callbacks is None
    handlers = [
        cb for cb in (selected_model.callbacks or []) if isinstance(cb, ModelUsageCaptureHandler)
    ]
    assert len(handlers) == 1
    # 归属指向本 run（内部私有属性，仅测试断言）
    assert handlers[0]._run_id == run.id
    assert handlers[0]._tenant_id == run.tenant_id
    assert handlers[0]._employee_id == run.employee_id
    assert handlers[0]._alias == "general-purpose"

    await prepared.close()
