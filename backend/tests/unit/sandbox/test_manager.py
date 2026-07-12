from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from agent_platform.sandbox.entities import SandboxLease, SandboxLeaseStatus, SandboxScope
from agent_platform.sandbox.errors import (
    SandboxLeaseBusy,
    SandboxLeaseNotFound,
    SandboxProviderNotConfigured,
)
from agent_platform.sandbox.manager import SandboxManager
from agent_platform.sandbox.ports import (
    ProviderSandbox,
    SandboxAcquireRequest,
    SandboxLeaseRepository,
)


class MemoryLeaseRepository(SandboxLeaseRepository):
    def __init__(self) -> None:
        self.leases: dict[UUID, SandboxLease] = {}

    async def add(self, lease: SandboxLease) -> None:
        self.leases[lease.id] = lease

    async def update(self, lease: SandboxLease) -> None:
        self.leases[lease.id] = lease

    async def get(self, *, tenant_id: UUID, lease_id: UUID) -> SandboxLease | None:
        lease = self.leases.get(lease_id)
        return lease if lease is not None and lease.tenant_id == tenant_id else None

    async def get_by_scope(self, *, scope: SandboxScope, provider: str) -> SandboxLease | None:
        return next(
            (
                lease
                for lease in self.leases.values()
                if lease.scope == scope and lease.provider == provider
            ),
            None,
        )

    async def list_expired(self, *, now: datetime, limit: int) -> list[SandboxLease]:
        return [
            lease
            for lease in self.leases.values()
            if lease.status
            in {
                SandboxLeaseStatus.PROVISIONING,
                SandboxLeaseStatus.ACTIVE,
                SandboxLeaseStatus.DELETING,
                SandboxLeaseStatus.ERROR,
            }
            and lease.expires_at <= now
        ][:limit]


class MemoryUnitOfWork:
    def __init__(
        self,
        repository: MemoryLeaseRepository,
        commits: list[dict[UUID, SandboxLease]],
    ) -> None:
        self.leases = repository
        self._repository = repository
        self._commits = commits

    async def __aenter__(self) -> MemoryUnitOfWork:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object | None,
    ) -> None:
        del exc_type, exc_value, traceback

    async def commit(self) -> None:
        self._commits.append(dict(self._repository.leases))


class MemoryUnitOfWorkFactory:
    def __init__(self, repository: MemoryLeaseRepository) -> None:
        self.repository = repository
        self.commits: list[dict[UUID, SandboxLease]] = []

    def __call__(self) -> MemoryUnitOfWork:
        return MemoryUnitOfWork(self.repository, self.commits)


class FakeWorkspace:
    async def write_file(self, *, path: str, content: bytes) -> None:
        del path, content


@dataclass(frozen=True)
class FakeBackend:
    sandbox_id: str


class FakeProvider:
    name = "fake"

    def __init__(self) -> None:
        self.acquire_calls = 0
        self.reconnect_calls: list[str] = []
        self.delete_calls: list[str] = []
        self.acquire_error: Exception | None = None
        self.delete_error: Exception | None = None

    async def acquire(self, request: SandboxAcquireRequest) -> ProviderSandbox:
        del request
        self.acquire_calls += 1
        if self.acquire_error is not None:
            raise self.acquire_error
        sandbox_id = f"sandbox-{self.acquire_calls}"
        return ProviderSandbox(
            sandbox_id=sandbox_id,
            workspace=FakeWorkspace(),
            backend=FakeBackend(sandbox_id),
        )

    async def reconnect(self, *, sandbox_id: str, lease_id: UUID) -> ProviderSandbox:
        del lease_id
        self.reconnect_calls.append(sandbox_id)
        return ProviderSandbox(
            sandbox_id=sandbox_id,
            workspace=FakeWorkspace(),
            backend=FakeBackend(sandbox_id),
        )

    async def delete(self, *, sandbox_id: str, lease_id: UUID) -> None:
        del lease_id
        self.delete_calls.append(sandbox_id)
        if self.delete_error is not None:
            raise self.delete_error


class FakeBackendValidator:
    def validate(self, backend: object) -> None:
        if not isinstance(backend, FakeBackend):
            raise TypeError("unsupported sandbox backend")


@pytest.fixture
def scope() -> SandboxScope:
    return SandboxScope(
        tenant_id=uuid4(),
        user_id=uuid4(),
        run_id=uuid4(),
        thread_id="thread-1",
    )


@pytest.fixture
def dependencies() -> tuple[SandboxManager, MemoryLeaseRepository, FakeProvider]:
    repository = MemoryLeaseRepository()
    unit_of_work_factory = MemoryUnitOfWorkFactory(repository)
    provider = FakeProvider()
    manager = SandboxManager(
        unit_of_work_factory=unit_of_work_factory,
        providers={provider.name: provider},
        provider_name=provider.name,
        backend_validator=FakeBackendValidator(),
    )
    return manager, repository, provider


@pytest.mark.asyncio
async def test_acquire_persists_identity_ttl_and_reuses_active_sandbox(
    scope: SandboxScope,
    dependencies: tuple[SandboxManager, MemoryLeaseRepository, FakeProvider],
) -> None:
    manager, repository, provider = dependencies
    now = datetime(2026, 7, 13, tzinfo=UTC)

    first = await manager.acquire(scope=scope, ttl=timedelta(minutes=30), now=now)
    second = await manager.acquire(scope=scope, ttl=timedelta(minutes=30), now=now)

    assert first.lease.id == second.lease.id
    assert first.lease.scope == scope
    assert first.lease.provider == "fake"
    assert first.lease.status is SandboxLeaseStatus.ACTIVE
    assert first.lease.expires_at == now + timedelta(minutes=30)
    assert provider.acquire_calls == 1
    assert provider.reconnect_calls == [first.lease.sandbox_id]
    assert repository.leases[first.lease.id] == first.lease


@pytest.mark.asyncio
async def test_reconnect_requires_the_complete_trusted_scope(
    scope: SandboxScope,
    dependencies: tuple[SandboxManager, MemoryLeaseRepository, FakeProvider],
) -> None:
    manager, _, provider = dependencies
    environment = await manager.acquire(scope=scope, ttl=timedelta(minutes=30))
    another_tenant = SandboxScope(
        tenant_id=uuid4(),
        user_id=scope.user_id,
        run_id=scope.run_id,
        thread_id=scope.thread_id,
    )

    with pytest.raises(SandboxLeaseNotFound):
        await manager.reconnect(lease_id=environment.lease.id, scope=another_tenant)

    assert provider.reconnect_calls == []


@pytest.mark.asyncio
async def test_delete_is_idempotent(
    scope: SandboxScope,
    dependencies: tuple[SandboxManager, MemoryLeaseRepository, FakeProvider],
) -> None:
    manager, repository, provider = dependencies
    environment = await manager.acquire(scope=scope, ttl=timedelta(minutes=30))

    deleted = await manager.delete(lease_id=environment.lease.id, scope=scope)
    deleted_again = await manager.delete(lease_id=environment.lease.id, scope=scope)

    assert deleted.status is SandboxLeaseStatus.DELETED
    assert deleted_again == deleted
    assert provider.delete_calls == [environment.lease.sandbox_id]
    assert repository.leases[environment.lease.id] == deleted


@pytest.mark.asyncio
async def test_cleanup_deletes_expired_provider_sandbox(
    scope: SandboxScope,
    dependencies: tuple[SandboxManager, MemoryLeaseRepository, FakeProvider],
) -> None:
    manager, repository, provider = dependencies
    now = datetime(2026, 7, 13, tzinfo=UTC)
    environment = await manager.acquire(scope=scope, ttl=timedelta(seconds=1), now=now)

    cleaned = await manager.cleanup_expired(now=now + timedelta(seconds=2), limit=10)

    assert cleaned == [environment.lease.id]
    assert repository.leases[environment.lease.id].status is SandboxLeaseStatus.EXPIRED
    assert provider.delete_calls == [environment.lease.sandbox_id]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "sandbox_id", "expected_delete_calls"),
    [
        (SandboxLeaseStatus.PROVISIONING, None, []),
        (SandboxLeaseStatus.DELETING, "box-deleting", ["box-deleting"]),
        (SandboxLeaseStatus.ERROR, None, []),
        (SandboxLeaseStatus.ERROR, "box-error", ["box-error"]),
    ],
)
async def test_cleanup_recovers_every_expired_non_terminal_lease(
    scope: SandboxScope,
    dependencies: tuple[SandboxManager, MemoryLeaseRepository, FakeProvider],
    status: SandboxLeaseStatus,
    sandbox_id: str | None,
    expected_delete_calls: list[str],
) -> None:
    manager, repository, provider = dependencies
    now = datetime(2026, 7, 13, tzinfo=UTC)
    lease = replace(
        SandboxLease.create(
            scope=scope,
            provider="fake",
            ttl=timedelta(seconds=1),
            now=now,
        ),
        status=status,
        sandbox_id=sandbox_id,
    )
    await repository.add(lease)

    cleaned = await manager.cleanup_expired(now=now + timedelta(seconds=2), limit=10)

    assert cleaned == [lease.id]
    assert repository.leases[lease.id].status is SandboxLeaseStatus.EXPIRED
    assert provider.delete_calls == expected_delete_calls


@pytest.mark.asyncio
async def test_provider_acquire_failure_is_persisted_and_can_be_retried(
    scope: SandboxScope,
    dependencies: tuple[SandboxManager, MemoryLeaseRepository, FakeProvider],
) -> None:
    manager, repository, provider = dependencies
    provider.acquire_error = RuntimeError("provider unavailable")

    with pytest.raises(RuntimeError, match="provider unavailable"):
        await manager.acquire(scope=scope, ttl=timedelta(minutes=30))

    failed = next(iter(repository.leases.values()))
    assert failed.status is SandboxLeaseStatus.ERROR
    assert failed.sandbox_id is None
    assert failed.last_error == "acquire_failed"

    provider.acquire_error = None
    recovered = await manager.acquire(scope=scope, ttl=timedelta(minutes=30))
    assert recovered.lease.id == failed.id
    assert recovered.lease.status is SandboxLeaseStatus.ACTIVE


@pytest.mark.asyncio
async def test_delete_failure_is_recorded_and_retryable(
    scope: SandboxScope,
    dependencies: tuple[SandboxManager, MemoryLeaseRepository, FakeProvider],
) -> None:
    manager, repository, provider = dependencies
    environment = await manager.acquire(scope=scope, ttl=timedelta(minutes=30))
    provider.delete_error = RuntimeError("delete failed")

    with pytest.raises(RuntimeError, match="delete failed"):
        await manager.delete(lease_id=environment.lease.id, scope=scope)

    assert repository.leases[environment.lease.id].status is SandboxLeaseStatus.ERROR
    assert repository.leases[environment.lease.id].sandbox_id == environment.lease.sandbox_id

    provider.delete_error = None
    deleted = await manager.delete(lease_id=environment.lease.id, scope=scope)
    assert deleted.status is SandboxLeaseStatus.DELETED
    assert provider.delete_calls == [environment.lease.sandbox_id, environment.lease.sandbox_id]


@pytest.mark.asyncio
async def test_transitional_lease_cannot_be_acquired_twice(
    scope: SandboxScope,
    dependencies: tuple[SandboxManager, MemoryLeaseRepository, FakeProvider],
) -> None:
    manager, repository, provider = dependencies
    lease = SandboxLease.create(scope=scope, provider="fake", ttl=timedelta(minutes=30))
    await repository.add(lease)

    with pytest.raises(SandboxLeaseBusy):
        await manager.acquire(scope=scope, ttl=timedelta(minutes=30))

    assert provider.acquire_calls == 0


@pytest.mark.asyncio
async def test_expired_provisioning_lease_is_recovered_with_the_same_idempotency_key(
    scope: SandboxScope,
    dependencies: tuple[SandboxManager, MemoryLeaseRepository, FakeProvider],
) -> None:
    manager, repository, provider = dependencies
    now = datetime(2026, 7, 13, tzinfo=UTC)
    stale = SandboxLease.create(
        scope=scope,
        provider="fake",
        ttl=timedelta(seconds=1),
        now=now,
    )
    await repository.add(stale)

    recovered = await manager.acquire(
        scope=scope,
        ttl=timedelta(minutes=30),
        now=now + timedelta(seconds=2),
    )

    assert recovered.lease.id == stale.id
    assert recovered.lease.status is SandboxLeaseStatus.ACTIVE
    assert provider.acquire_calls == 1


@pytest.mark.asyncio
async def test_backend_capability_is_validated_before_environment_is_returned(
    scope: SandboxScope,
) -> None:
    repository = MemoryLeaseRepository()

    class InvalidBackendProvider(FakeProvider):
        async def acquire(self, request: SandboxAcquireRequest) -> ProviderSandbox:
            provisioned = await super().acquire(request)
            return ProviderSandbox(
                sandbox_id=provisioned.sandbox_id,
                workspace=provisioned.workspace,
                backend=object(),
            )

    provider = InvalidBackendProvider()
    manager = SandboxManager(
        unit_of_work_factory=MemoryUnitOfWorkFactory(repository),
        providers={provider.name: provider},
        provider_name=provider.name,
        backend_validator=FakeBackendValidator(),
    )

    with pytest.raises(TypeError, match="unsupported sandbox backend"):
        await manager.acquire(scope=scope, ttl=timedelta(minutes=30))

    failed = next(iter(repository.leases.values()))
    assert failed.status is SandboxLeaseStatus.ERROR
    assert provider.delete_calls == ["sandbox-1"]


@pytest.mark.asyncio
async def test_validation_error_survives_cleanup_error_and_lease_is_stable_error(
    scope: SandboxScope,
) -> None:
    repository = MemoryLeaseRepository()

    class InvalidBackendProvider(FakeProvider):
        async def acquire(self, request: SandboxAcquireRequest) -> ProviderSandbox:
            provisioned = await super().acquire(request)
            return ProviderSandbox(
                sandbox_id=provisioned.sandbox_id,
                workspace=provisioned.workspace,
                backend=object(),
            )

    provider = InvalidBackendProvider()
    provider.delete_error = RuntimeError("secret cleanup details")
    manager = SandboxManager(
        unit_of_work_factory=MemoryUnitOfWorkFactory(repository),
        providers={provider.name: provider},
        provider_name=provider.name,
        backend_validator=FakeBackendValidator(),
    )

    with pytest.raises(TypeError, match="unsupported sandbox backend"):
        await manager.acquire(scope=scope, ttl=timedelta(minutes=30))

    failed = next(iter(repository.leases.values()))
    assert failed.status is SandboxLeaseStatus.ERROR
    assert failed.last_error == "backend_validation_cleanup_failed"
    assert failed.sandbox_id == "sandbox-1"
    assert "secret" not in failed.last_error


@pytest.mark.asyncio
async def test_delete_resolves_provider_before_marking_lease_deleting(
    scope: SandboxScope,
    dependencies: tuple[SandboxManager, MemoryLeaseRepository, FakeProvider],
) -> None:
    manager, repository, _ = dependencies
    environment = await manager.acquire(scope=scope, ttl=timedelta(minutes=30))
    manager_without_provider = SandboxManager(
        unit_of_work_factory=MemoryUnitOfWorkFactory(repository),
        providers={},
        provider_name="fake",
        backend_validator=FakeBackendValidator(),
    )

    with pytest.raises(SandboxProviderNotConfigured):
        await manager_without_provider.delete(lease_id=environment.lease.id, scope=scope)

    assert repository.leases[environment.lease.id].status is SandboxLeaseStatus.ACTIVE


@pytest.mark.asyncio
async def test_expired_error_lease_is_deleted_instead_of_reconnected(
    scope: SandboxScope,
    dependencies: tuple[SandboxManager, MemoryLeaseRepository, FakeProvider],
) -> None:
    manager, repository, provider = dependencies
    now = datetime(2026, 7, 13, tzinfo=UTC)
    active = await manager.acquire(scope=scope, ttl=timedelta(seconds=1), now=now)
    await repository.update(active.lease.mark_error("delete_failed", now=now))

    replacement = await manager.acquire(
        scope=scope,
        ttl=timedelta(minutes=30),
        now=now + timedelta(seconds=2),
    )

    assert provider.reconnect_calls == []
    assert provider.delete_calls == [active.lease.sandbox_id]
    assert provider.acquire_calls == 2
    assert replacement.lease.sandbox_id == "sandbox-2"


@pytest.mark.asyncio
async def test_reconnect_failure_marks_lease_error(
    scope: SandboxScope,
) -> None:
    repository = MemoryLeaseRepository()

    class ReconnectFailingProvider(FakeProvider):
        async def reconnect(self, *, sandbox_id: str, lease_id: UUID) -> ProviderSandbox:
            del lease_id
            self.reconnect_calls.append(sandbox_id)
            raise RuntimeError("provider reconnect secret")

    provider = ReconnectFailingProvider()
    manager = SandboxManager(
        unit_of_work_factory=MemoryUnitOfWorkFactory(repository),
        providers={provider.name: provider},
        provider_name=provider.name,
        backend_validator=FakeBackendValidator(),
    )
    environment = await manager.acquire(scope=scope, ttl=timedelta(minutes=30))

    with pytest.raises(RuntimeError, match="provider reconnect secret"):
        await manager.reconnect(lease_id=environment.lease.id, scope=scope)

    failed = repository.leases[environment.lease.id]
    assert failed.status is SandboxLeaseStatus.ERROR
    assert failed.last_error == "reconnect_failed"


@pytest.mark.asyncio
async def test_successful_reconnect_recovers_error_lease_to_active(
    scope: SandboxScope,
    dependencies: tuple[SandboxManager, MemoryLeaseRepository, FakeProvider],
) -> None:
    manager, repository, provider = dependencies
    environment = await manager.acquire(scope=scope, ttl=timedelta(minutes=30))
    await repository.update(environment.lease.mark_error("reconnect_failed"))

    recovered = await manager.acquire(scope=scope, ttl=timedelta(minutes=30))

    assert recovered.lease.status is SandboxLeaseStatus.ACTIVE
    assert recovered.lease.last_error is None
    assert repository.leases[environment.lease.id] == recovered.lease
    assert provider.reconnect_calls == [environment.lease.sandbox_id]


@pytest.mark.asyncio
async def test_lifecycle_state_is_committed_before_each_external_provider_call(
    scope: SandboxScope,
) -> None:
    repository = MemoryLeaseRepository()
    unit_of_work_factory = MemoryUnitOfWorkFactory(repository)

    class DurabilityCheckingProvider(FakeProvider):
        async def acquire(self, request: SandboxAcquireRequest) -> ProviderSandbox:
            assert next(iter(unit_of_work_factory.commits[-1].values())).status is (
                SandboxLeaseStatus.PROVISIONING
            )
            return await super().acquire(request)

        async def delete(self, *, sandbox_id: str, lease_id: UUID) -> None:
            assert unit_of_work_factory.commits[-1][environment.lease.id].status is (
                SandboxLeaseStatus.DELETING
            )
            await super().delete(sandbox_id=sandbox_id, lease_id=lease_id)

    provider = DurabilityCheckingProvider()
    manager = SandboxManager(
        unit_of_work_factory=unit_of_work_factory,
        providers={provider.name: provider},
        provider_name=provider.name,
        backend_validator=FakeBackendValidator(),
    )

    environment = await manager.acquire(scope=scope, ttl=timedelta(minutes=30))
    await manager.delete(lease_id=environment.lease.id, scope=scope)

    assert [snapshot[environment.lease.id].status for snapshot in unit_of_work_factory.commits] == [
        SandboxLeaseStatus.PROVISIONING,
        SandboxLeaseStatus.ACTIVE,
        SandboxLeaseStatus.DELETING,
        SandboxLeaseStatus.DELETED,
    ]
