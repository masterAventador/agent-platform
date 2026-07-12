from datetime import timedelta
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from deepagents.backends.protocol import SandboxBackendProtocol
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_platform.infrastructure.database.base import Base
from agent_platform.infrastructure.database.models import load_database_models
from agent_platform.platform.runs.entities import Run
from agent_platform.sandbox.entities import SandboxLease, SandboxScope
from agent_platform.sandbox.ports import RunExecutionEnvironment
from agent_platform.workers import runtime_composition as runtime_composition_module
from agent_platform.workers.runtime_composition import (
    ComposedRuntimeResolver,
    ModelProviderAdapterMissing,
    PlatformModelResolver,
    PlatformRuntimeSelector,
    PublishedModel,
    PublishedRuntimeCapabilities,
    UnsupportedModelProvider,
    UntrustedRuntimeDefinition,
    extend_runtime_definition,
)


class EmptyStorage:
    async def get(self, *, key: str) -> bytes:
        raise AssertionError(key)


class RecordingWorkspace:
    def __init__(self) -> None:
        self.writes: list[tuple[str, bytes]] = []

    async def write_file(self, *, path: str, content: bytes) -> None:
        self.writes.append((path, content))


class RecordingSandboxManager:
    def __init__(self) -> None:
        self.scope: SandboxScope | None = None
        self.ttl: timedelta | None = None
        self.workspace = RecordingWorkspace()
        self.backend = SandboxBackendProtocol()
        self.deleted: list[tuple[UUID, SandboxScope]] = []
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


class RecordingSelector:
    def __init__(self, *, fail: bool = False) -> None:
        self.selection: dict[str, object] | None = None
        self.runtime = object()
        self.fail = fail

    def select(self, **selection):
        self.selection = selection
        if self.fail:
            raise RuntimeError("selection failed")
        return self.runtime


class UnusedGateway:
    async def invoke(self, invocation, context):
        raise AssertionError((invocation, context))


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
        "model": {"provider": "openai", "name": "gpt-5"},
        "skill_ids": [],
        "tool_ids": [],
        **changes,
    }


def test_published_capabilities_parse_trusted_model_and_version_bound_ids() -> None:
    skill_id = uuid4()
    tool_id = uuid4()

    capabilities = PublishedRuntimeCapabilities.from_definition(
        autonomous_definition(
            skill_ids=[str(skill_id)],
            tool_ids=[str(tool_id)],
            capabilities={"conversation": True},
        )
    )

    assert capabilities.model == PublishedModel(provider="openai", name="gpt-5")
    assert capabilities.skill_ids == (skill_id,)
    assert capabilities.tool_ids == (tool_id,)


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
    ],
)
def test_all_invalid_published_fields_are_permanent_preparation_errors(
    definition: dict[str, object],
) -> None:
    with pytest.raises(UntrustedRuntimeDefinition):
        PublishedRuntimeCapabilities.from_definition(definition)


def test_model_resolver_is_allowlisted_and_supports_host_side_injection() -> None:
    fake_model = GenericFakeChatModel(messages=iter(["ok"]))
    resolver = PlatformModelResolver(injected_models={("openai", "gpt-5"): fake_model})

    assert resolver.resolve(PublishedModel(provider="openai", name="gpt-5")) is fake_model
    assert (
        PlatformModelResolver().resolve(
            PublishedModel(provider="anthropic", name="claude-sonnet-4-5")
        )
        == "anthropic:claude-sonnet-4-5"
    )
    with pytest.raises(UnsupportedModelProvider):
        resolver.resolve(PublishedModel(provider="untrusted", name="remote-model"))


def test_model_resolver_fails_fast_when_official_provider_adapter_is_missing() -> None:
    resolver = PlatformModelResolver(module_finder=lambda _: None)

    with pytest.raises(ModelProviderAdapterMissing, match="openai"):
        resolver.resolve(PublishedModel(provider="openai", name="gpt-5"))


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
    resolver = ComposedRuntimeResolver(
        session_factory=session_factory,
        skill_storage=EmptyStorage(),
        sandbox_manager=manager,
        gateway=UnusedGateway(),
        runtime_selector=selector,
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
    assert selector.selection["model"] == "openai:gpt-5"
    assert prepared.employee_definition["skill_paths"] == []

    await prepared.close()
    await prepared.close()
    assert len(manager.deleted) == 1


@pytest.mark.asyncio
async def test_resolver_releases_environment_when_runtime_selection_fails(
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
        runtime_selector=RecordingSelector(fail=True),
    )

    with pytest.raises(RuntimeError, match="selection failed"):
        await resolver.resolve(run, autonomous_definition())

    assert len(manager.deleted) == 1


@pytest.mark.asyncio
async def test_preparation_error_is_not_masked_when_sandbox_delete_fails(
    session_factory,
    monkeypatch,
) -> None:
    logged: list[tuple[str, dict[str, str]]] = []
    monkeypatch.setattr(
        runtime_composition_module.logger,
        "error",
        lambda message, *, extra: logged.append((message, extra)),
    )
    run = Run.create(
        tenant_id=uuid4(),
        employee_id=uuid4(),
        employee_version=1,
        created_by=uuid4(),
        input_data={},
    )
    manager = RecordingSandboxManager()
    manager.delete_error = ValueError("provider secret must not be logged")
    resolver = ComposedRuntimeResolver(
        session_factory=session_factory,
        skill_storage=EmptyStorage(),
        sandbox_manager=manager,
        gateway=UnusedGateway(),
        runtime_selector=RecordingSelector(fail=True),
    )

    with pytest.raises(RuntimeError, match="selection failed"):
        await resolver.resolve(run, autonomous_definition())

    assert len(manager.deleted) == 1
    assert logged == [("runtime_preparation_cleanup_failed", {"run_id": str(run.id)})]
    assert "provider secret" not in str(logged)


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

    selector.select(
        capabilities=autonomous, tools=[], environment=environment, model="openai:gpt-5"
    )
    selector.select(capabilities=workflow, tools=[], environment=environment, model="openai:gpt-5")

    assert calls == [
        ("deep-agent", environment, "openai:gpt-5"),
        (workflow_id, 3, environment, "openai:gpt-5"),
    ]
