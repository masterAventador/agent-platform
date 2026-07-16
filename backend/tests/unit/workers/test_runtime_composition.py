import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from deepagents.backends.protocol import SandboxBackendProtocol
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_platform.infrastructure.database.base import Base
from agent_platform.infrastructure.database.models import load_database_models
from agent_platform.infrastructure.database.repositories.knowledge import KnowledgeBaseRecord
from agent_platform.platform.knowledge.models import KnowledgeCitation, KnowledgeSearchResult
from agent_platform.platform.knowledge.registry import KnowledgeProviderRegistry
from agent_platform.platform.runs.entities import Run
from agent_platform.runtimes.recovery import RuntimeRecoveryTransient
from agent_platform.sandbox.entities import SandboxLease, SandboxScope
from agent_platform.sandbox.ports import RunExecutionEnvironment
from agent_platform.workers import runtime_composition as runtime_composition_module
from agent_platform.workers.runtime_composition import (
    ComposedRuntimeResolver,
    ModelGatewayUnavailable,
    PlatformModelResolver,
    PlatformRuntimeSelector,
    PublishedModel,
    PublishedRuntimeCapabilities,
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
        self.retrieve_calls: list[tuple[str, list[str], int, dict[str, object] | None]] = []

    async def retrieve(
        self,
        *,
        question: str,
        dataset_ids: list[str],
        page_size: int = 10,
        metadata_condition: dict[str, object] | None = None,
    ) -> KnowledgeSearchResult:
        self.retrieve_calls.append((question, dataset_ids, page_size, metadata_condition))
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


def test_model_resolver_supports_provider_neutral_host_side_injection() -> None:
    fake_model = GenericFakeChatModel(messages=iter(["ok"]))
    resolver = PlatformModelResolver(injected_models={"general-purpose": fake_model})

    assert (
        resolver.resolve(PublishedModel(kind="gateway_alias", alias="general-purpose"))
        is fake_model
    )


def test_model_resolver_fails_fast_without_a_gateway_factory() -> None:
    resolver = PlatformModelResolver()

    with pytest.raises(ModelGatewayUnavailable, match="gateway"):
        resolver.resolve(PublishedModel(kind="gateway_alias", alias="general-purpose"))


def test_model_resolver_rejects_an_alias_outside_the_platform_allowlist() -> None:
    factory_calls: list[str] = []
    model = GenericFakeChatModel(messages=iter(["ok"]))
    resolver = PlatformModelResolver(
        model_factory=lambda alias: factory_calls.append(alias) or model
    )

    with pytest.raises(ModelGatewayUnavailable, match="allowlist"):
        resolver.resolve(PublishedModel(kind="gateway_alias", alias="unconfigured-alias"))

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
        autonomous_definition(
            knowledge_base_ids=[str(knowledge_base_id)],
            knowledge_retrieval={
                "limit": 2,
                "metadata_condition": {
                    "logic": "and",
                    "conditions": [{"key": "department", "op": "eq", "value": "HR"}],
                },
            },
        ),
    )

    assert provider.retrieve_calls == [
        (
            "年假有几天",
            [provider_dataset_id],
            2,
            {
                "logic": "and",
                "conditions": [{"key": "department", "op": "eq", "value": "HR"}],
            },
        )
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
    calls: list[tuple[object, ...]] = []
    selector = PlatformRuntimeSelector(
        autonomous_factory=lambda tools, environment, model: (
            calls.append(("deep-agent", environment, model)) or object()
        ),
        workflow_factory=lambda workflow_id, version, tools, environment, model: (
            calls.append((workflow_id, version, environment, model)) or object()
        ),
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

    selector.select(capabilities=autonomous, tools=[], environment=environment, model=model)
    selector.select(capabilities=workflow, tools=[], environment=environment, model=model)

    assert calls == [
        ("deep-agent", environment, model),
        (workflow_id, 3, environment, model),
    ]


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
        workflow_factory=lambda workflow_id, version, tools, environment, model: object(),
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
