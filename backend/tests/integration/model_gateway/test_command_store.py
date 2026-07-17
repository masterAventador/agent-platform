"""Provisioning outbox 认领与结算的持久化契约（C16 阶段一）。

本层用 SQLite 验证与方言无关的语义（认领顺序、退避过滤、按租户串行、结果落库、
revision CAS、Key 首次签发、保留清扫）。真实多副本并发独占由
``test_postgres_provisioning_concurrency.py`` 在真实 PostgreSQL 上门禁。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_platform.infrastructure.database.base import Base
from agent_platform.infrastructure.database.models import load_database_models
from agent_platform.infrastructure.database.repositories.auth import UserRecord
from agent_platform.infrastructure.database.repositories.model_gateway import (
    ModelGatewayProvisioningCommandRecord,
    SqlAlchemyModelGatewayCommandStore,
    SqlAlchemyModelGatewayKeyRepository,
    SqlAlchemyModelGatewayPolicyRepository,
    TenantModelGatewayKeyRecord,
    TenantModelGatewayPolicyRecord,
)
from agent_platform.infrastructure.database.repositories.tenants import TenantRecord
from agent_platform.platform.model_gateway.entities import (
    ModelGatewayPolicyStatus,
    TenantModelGatewayPolicy,
)
from agent_platform.platform.model_gateway.ports import (
    ClaimedProvisioningCommand,
    ModelGatewayProvisioningAction,
    ProvisioningCommandStatus,
    ReconcileOutcome,
)

NOW = datetime(2026, 7, 17, 12, 0, tzinfo=UTC)


@pytest_asyncio.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    load_database_models()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _seed_tenant(factory: async_sessionmaker) -> tuple[UUID, UUID]:
    tenant_id, user_id = uuid4(), uuid4()
    async with factory() as session:
        session.add(
            TenantRecord(id=tenant_id, name="网关租户", slug=f"gw-{tenant_id.hex}", created_at=NOW)
        )
        session.add(
            UserRecord(
                id=user_id,
                email=f"owner-{user_id.hex}@example.com",
                password_hash="x",
                created_at=NOW,
            )
        )
        await session.commit()
    return tenant_id, user_id


def _policy(
    tenant_id: UUID,
    user_id: UUID,
    *,
    revision: int = 1,
    enabled: bool = True,
) -> TenantModelGatewayPolicy:
    return TenantModelGatewayPolicy.create_desired(
        tenant_id=tenant_id,
        enabled=enabled,
        allowed_aliases={"general-purpose"},
        budget_microusd=1_000_000,
        budget_period="monthly",
        rpm_limit=60,
        tpm_limit=100_000,
        max_parallel_requests=4,
        revision=revision,
        updated_by=user_id,
        now=NOW,
    )


async def _put_desired(
    factory: async_sessionmaker,
    policy: TenantModelGatewayPolicy,
    *,
    expected_revision: int,
) -> None:
    async with factory() as session:
        await SqlAlchemyModelGatewayPolicyRepository(session).save_desired(
            policy,
            expected_revision=expected_revision,
            action=ModelGatewayProvisioningAction.RECONCILE,
        )
        await session.commit()


class _Recorder:
    def __init__(self, outcome: ReconcileOutcome) -> None:
        self._outcome = outcome
        self.claims: list[ClaimedProvisioningCommand] = []

    async def __call__(self, claimed: ClaimedProvisioningCommand) -> ReconcileOutcome:
        self.claims.append(claimed)
        return self._outcome


_COMPLETED = ReconcileOutcome(
    command_status=ProvisioningCommandStatus.COMPLETED,
    policy_status=ModelGatewayPolicyStatus.ACTIVE,
    key_provisioned=True,
    clear_key_retirement=True,
)


@pytest.mark.asyncio
async def test_claim_issues_the_first_key_version_and_hands_the_desired_state_over(
    session_factory: async_sessionmaker,
) -> None:
    tenant_id, user_id = await _seed_tenant(session_factory)
    await _put_desired(session_factory, _policy(tenant_id, user_id), expected_revision=0)
    store = SqlAlchemyModelGatewayCommandStore(session_factory)
    handler = _Recorder(_COMPLETED)

    assert await store.process_next(handler, now=NOW) is True

    claimed = handler.claims[0]
    assert claimed.tenant_id == tenant_id
    assert claimed.desired_revision == 1
    assert claimed.attempts == 0
    assert claimed.policy.enabled is True
    assert claimed.key.key_version == 1
    assert claimed.key.retired_key_version is None
    async with session_factory() as session:
        key = await session.get(TenantModelGatewayKeyRecord, tenant_id)
        command = (
            await session.execute(select(ModelGatewayProvisioningCommandRecord))
        ).scalar_one()
        policy = await session.get(TenantModelGatewayPolicyRecord, tenant_id)
    assert key is not None and key.key_version == 1
    assert command.status == "completed"
    assert command.processed_at is not None
    assert policy is not None and policy.status == "active"


@pytest.mark.asyncio
async def test_an_empty_outbox_is_reported_without_side_effects(
    session_factory: async_sessionmaker,
) -> None:
    store = SqlAlchemyModelGatewayCommandStore(session_factory)
    handler = _Recorder(_COMPLETED)

    assert await store.process_next(handler, now=NOW) is False
    assert handler.claims == []


@pytest.mark.asyncio
async def test_commands_are_claimed_in_creation_order(
    session_factory: async_sessionmaker,
) -> None:
    first_tenant, first_user = await _seed_tenant(session_factory)
    second_tenant, second_user = await _seed_tenant(session_factory)
    await _put_desired(session_factory, _policy(first_tenant, first_user), expected_revision=0)
    later = _policy(second_tenant, second_user)
    async with session_factory() as session:
        await SqlAlchemyModelGatewayPolicyRepository(session).save_desired(
            later, expected_revision=0, action=ModelGatewayProvisioningAction.RECONCILE
        )
        await session.commit()
    async with session_factory() as session:
        command = (
            await session.execute(
                select(ModelGatewayProvisioningCommandRecord).where(
                    ModelGatewayProvisioningCommandRecord.tenant_id == second_tenant
                )
            )
        ).scalar_one()
        command.created_at = NOW + timedelta(seconds=5)
        await session.commit()
    store = SqlAlchemyModelGatewayCommandStore(session_factory)
    handler = _Recorder(_COMPLETED)

    await store.process_next(handler, now=NOW + timedelta(minutes=1))
    await store.process_next(handler, now=NOW + timedelta(minutes=1))

    assert [claim.tenant_id for claim in handler.claims] == [first_tenant, second_tenant]


@pytest.mark.asyncio
async def test_backed_off_commands_are_not_claimed_before_their_next_attempt(
    session_factory: async_sessionmaker,
) -> None:
    tenant_id, user_id = await _seed_tenant(session_factory)
    await _put_desired(session_factory, _policy(tenant_id, user_id), expected_revision=0)
    store = SqlAlchemyModelGatewayCommandStore(session_factory)
    retry = ReconcileOutcome(
        command_status=ProvisioningCommandStatus.PENDING,
        error_code="provisioning_transient",
        next_attempt_at=NOW + timedelta(seconds=60),
    )

    assert await store.process_next(_Recorder(retry), now=NOW) is True
    # 退避未到期：不得再次认领，否则网关不可用时会变成热轮询
    assert await store.process_next(_Recorder(retry), now=NOW + timedelta(seconds=30)) is False
    assert await store.process_next(_Recorder(retry), now=NOW + timedelta(seconds=61)) is True

    async with session_factory() as session:
        command = (
            await session.execute(select(ModelGatewayProvisioningCommandRecord))
        ).scalar_one()
        policy = await session.get(TenantModelGatewayPolicyRecord, tenant_id)
    assert command.status == "pending"
    assert command.attempts == 2
    assert command.last_error_code == "provisioning_transient"
    # 瞬态重试期间策略状态不得被推进成 error
    assert policy is not None and policy.status == "pending"


@pytest.mark.asyncio
async def test_a_failed_outcome_settles_the_command_and_marks_the_policy_error(
    session_factory: async_sessionmaker,
) -> None:
    tenant_id, user_id = await _seed_tenant(session_factory)
    await _put_desired(session_factory, _policy(tenant_id, user_id), expected_revision=0)
    store = SqlAlchemyModelGatewayCommandStore(session_factory)
    failed = ReconcileOutcome(
        command_status=ProvisioningCommandStatus.FAILED,
        policy_status=ModelGatewayPolicyStatus.ERROR,
        error_code="provisioning_outcome_unknown",
    )

    await store.process_next(_Recorder(failed), now=NOW)

    async with session_factory() as session:
        command = (
            await session.execute(select(ModelGatewayProvisioningCommandRecord))
        ).scalar_one()
        policy = await session.get(TenantModelGatewayPolicyRecord, tenant_id)
    assert command.status == "failed"
    assert command.last_error_code == "provisioning_outcome_unknown"
    assert policy is not None and policy.status == "error"
    # 已结算的失败命令不得再被认领（禁止自动重放）
    assert await store.process_next(_Recorder(failed), now=NOW + timedelta(hours=1)) is False


@pytest.mark.asyncio
async def test_only_one_pending_command_per_tenant_is_claimable_at_a_time(
    session_factory: async_sessionmaker,
) -> None:
    """同租户的两条命令不得并发对账，否则 enabled/disabled 交错会产生不确定终态。"""
    tenant_id, user_id = await _seed_tenant(session_factory)
    await _put_desired(session_factory, _policy(tenant_id, user_id), expected_revision=0)
    await _put_desired(
        session_factory,
        _policy(tenant_id, user_id, revision=2, enabled=False),
        expected_revision=1,
    )
    store = SqlAlchemyModelGatewayCommandStore(session_factory)
    retry = ReconcileOutcome(
        command_status=ProvisioningCommandStatus.PENDING,
        error_code="provisioning_transient",
        next_attempt_at=NOW + timedelta(seconds=60),
    )
    handler = _Recorder(retry)

    await store.process_next(handler, now=NOW)
    # rev1 仍 pending（退避中）：rev2 不得抢先
    assert await store.process_next(handler, now=NOW + timedelta(seconds=1)) is False
    assert [claim.desired_revision for claim in handler.claims] == [1]


@pytest.mark.asyncio
async def test_a_superseded_revision_settles_without_touching_the_newer_policy_status(
    session_factory: async_sessionmaker,
) -> None:
    """对账期间 desired 已前进：绝不能把旧 revision 的结果写成新 desired 的状态。"""
    tenant_id, user_id = await _seed_tenant(session_factory)
    await _put_desired(session_factory, _policy(tenant_id, user_id), expected_revision=0)
    store = SqlAlchemyModelGatewayCommandStore(session_factory)

    async def handler(claimed: ClaimedProvisioningCommand) -> ReconcileOutcome:
        # 模拟对账进行中 API 写入了新的 desired revision（enabled=false）
        await _put_desired(
            session_factory,
            _policy(tenant_id, user_id, revision=2, enabled=False),
            expected_revision=1,
        )
        return _COMPLETED

    await store.process_next(handler, now=NOW)

    async with session_factory() as session:
        policy = await session.get(TenantModelGatewayPolicyRecord, tenant_id)
        settled = (
            await session.execute(
                select(ModelGatewayProvisioningCommandRecord).where(
                    ModelGatewayProvisioningCommandRecord.desired_revision == 1
                )
            )
        ).scalar_one()
    assert policy is not None
    assert policy.revision == 2
    # rev1 的 active 结论必须被丢弃：新 desired 是 disabled，仍待对账
    assert policy.status == "pending"
    assert settled.status == "completed"


@pytest.mark.asyncio
async def test_settled_commands_are_pruned_and_pending_ones_are_kept(
    session_factory: async_sessionmaker,
) -> None:
    tenant_id, user_id = await _seed_tenant(session_factory)
    other_tenant, other_user = await _seed_tenant(session_factory)
    await _put_desired(session_factory, _policy(tenant_id, user_id), expected_revision=0)
    await _put_desired(session_factory, _policy(other_tenant, other_user), expected_revision=0)
    store = SqlAlchemyModelGatewayCommandStore(session_factory)
    await store.process_next(_Recorder(_COMPLETED), now=NOW)

    pruned = await store.prune_settled(older_than=NOW + timedelta(days=7), limit=100)

    async with session_factory() as session:
        remaining = (
            (await session.execute(select(ModelGatewayProvisioningCommandRecord))).scalars().all()
        )
    assert pruned == 1
    assert [record.tenant_id for record in remaining] == [other_tenant]


@pytest.mark.asyncio
async def test_prune_respects_the_retention_window_and_the_batch_limit(
    session_factory: async_sessionmaker,
) -> None:
    tenant_id, user_id = await _seed_tenant(session_factory)
    await _put_desired(session_factory, _policy(tenant_id, user_id), expected_revision=0)
    store = SqlAlchemyModelGatewayCommandStore(session_factory)
    await store.process_next(_Recorder(_COMPLETED), now=NOW)

    assert await store.prune_settled(older_than=NOW - timedelta(days=1), limit=100) == 0
    assert await store.prune_settled(older_than=NOW + timedelta(days=7), limit=0) == 0
    assert await store.prune_settled(older_than=NOW + timedelta(days=7), limit=100) == 1


@pytest.mark.asyncio
async def test_key_repository_reads_the_provisioned_version_for_the_worker(
    session_factory: async_sessionmaker,
) -> None:
    tenant_id, user_id = await _seed_tenant(session_factory)
    other_tenant, _ = await _seed_tenant(session_factory)
    await _put_desired(session_factory, _policy(tenant_id, user_id), expected_revision=0)
    await SqlAlchemyModelGatewayCommandStore(session_factory).process_next(
        _Recorder(_COMPLETED), now=NOW
    )

    async with session_factory() as session:
        repository = SqlAlchemyModelGatewayKeyRepository(session)
        key = await repository.get(tenant_id)
        missing = await repository.get(other_tenant)

    assert key is not None
    assert key.key_version == 1
    assert key.retired_key_version is None
    # 租户隔离：未签发租户不得读到别人的 Key 版本
    assert missing is None


@pytest.mark.asyncio
async def test_a_completed_enabled_reconcile_marks_the_key_provisioned(
    session_factory: async_sessionmaker,
) -> None:
    """provisioned_key_version 只由真实对账成功写入——它是网关侧存在性的唯一真相源。"""
    tenant_id, user_id = await _seed_tenant(session_factory)
    await _put_desired(session_factory, _policy(tenant_id, user_id), expected_revision=0)
    store = SqlAlchemyModelGatewayCommandStore(session_factory)

    async with session_factory() as session:
        before = await session.get(TenantModelGatewayKeyRecord, tenant_id)
    assert before is None

    await store.process_next(_Recorder(_COMPLETED), now=NOW)

    async with session_factory() as session:
        key = await session.get(TenantModelGatewayKeyRecord, tenant_id)
    assert key is not None
    assert key.provisioned_key_version == 1


@pytest.mark.asyncio
async def test_the_claim_hands_over_an_unprovisioned_key_on_the_first_reconcile(
    session_factory: async_sessionmaker,
) -> None:
    tenant_id, user_id = await _seed_tenant(session_factory)
    await _put_desired(session_factory, _policy(tenant_id, user_id), expected_revision=0)
    handler = _Recorder(_COMPLETED)

    await SqlAlchemyModelGatewayCommandStore(session_factory).process_next(handler, now=NOW)

    assert handler.claims[0].key.provisioned_key_version is None


@pytest.mark.asyncio
async def test_a_failed_reconcile_never_marks_the_key_provisioned(
    session_factory: async_sessionmaker,
) -> None:
    tenant_id, user_id = await _seed_tenant(session_factory)
    await _put_desired(session_factory, _policy(tenant_id, user_id), expected_revision=0)
    failed = ReconcileOutcome(
        command_status=ProvisioningCommandStatus.FAILED,
        policy_status=ModelGatewayPolicyStatus.ERROR,
        error_code="provisioning_rejected",
    )

    await SqlAlchemyModelGatewayCommandStore(session_factory).process_next(
        _Recorder(failed), now=NOW
    )

    async with session_factory() as session:
        key = await session.get(TenantModelGatewayKeyRecord, tenant_id)
    assert key is not None
    assert key.provisioned_key_version is None


@pytest.mark.asyncio
async def test_a_completed_disabled_reconcile_clears_the_provisioned_version(
    session_factory: async_sessionmaker,
) -> None:
    """撤销对账完成后网关侧 Key 已被阻断。

    若不清空 provisioned，再启用的窗口里 Worker 会拿一个 blocked Key 去撞 401。
    """
    tenant_id, user_id = await _seed_tenant(session_factory)
    await _put_desired(session_factory, _policy(tenant_id, user_id), expected_revision=0)
    store = SqlAlchemyModelGatewayCommandStore(session_factory)
    await store.process_next(_Recorder(_COMPLETED), now=NOW)
    await _put_desired(
        session_factory,
        _policy(tenant_id, user_id, revision=2, enabled=False),
        expected_revision=1,
    )

    await store.process_next(
        _Recorder(
            ReconcileOutcome(
                command_status=ProvisioningCommandStatus.COMPLETED,
                policy_status=ModelGatewayPolicyStatus.DISABLED,
                key_provisioned=False,
                clear_key_retirement=True,
            )
        ),
        now=NOW,
    )

    async with session_factory() as session:
        key = await session.get(TenantModelGatewayKeyRecord, tenant_id)
    assert key is not None
    assert key.provisioned_key_version is None


@pytest.mark.asyncio
async def test_rotation_preserves_the_provisioned_version_until_reconcile(
    session_factory: async_sessionmaker,
) -> None:
    """轮换只改 desired：observed 必须原地不动。

    否则轮换落库到对账完成之间会出现凭据真空期——网关上 v2 还不存在、v1 却已被声称不可用。
    """
    tenant_id, user_id = await _seed_tenant(session_factory)
    await _put_desired(session_factory, _policy(tenant_id, user_id), expected_revision=0)
    await SqlAlchemyModelGatewayCommandStore(session_factory).process_next(
        _Recorder(_COMPLETED), now=NOW
    )

    async with session_factory() as session:
        repository = SqlAlchemyModelGatewayPolicyRepository(session)
        current = await repository.get(tenant_id)
        key = await repository.get_key(tenant_id)
        assert current is not None and key is not None
        rotated = key.rotate(now=NOW)
        await repository.save_rotated_key(
            current.revise_desired(
                enabled=current.enabled,
                allowed_aliases=current.allowed_aliases,
                budget_microusd=current.budget_microusd,
                budget_period=current.budget_period,
                rpm_limit=current.rpm_limit,
                tpm_limit=current.tpm_limit,
                max_parallel_requests=current.max_parallel_requests,
                updated_by=user_id,
                now=NOW,
            ),
            key=rotated,
            expected_revision=current.revision,
            action=ModelGatewayProvisioningAction.RECONCILE,
        )
        await session.commit()

    async with session_factory() as session:
        stored = await session.get(TenantModelGatewayKeyRecord, tenant_id)
    assert stored is not None
    assert stored.key_version == 2
    assert stored.retired_key_version == 1
    # 网关上仍然只有 v1 真实存在：observed 必须保持 1
    assert stored.provisioned_key_version == 1


@pytest.mark.asyncio
async def test_a_concurrent_rotation_during_reconcile_is_not_clobbered(
    session_factory: async_sessionmaker,
) -> None:
    """对账期间发生轮换：本次对账只能落它**实际观测到**的版本，不得抹掉新的待回收记录。

    Controller 对账的是 v1；若期间 API 轮换到 v2/retired=1，本次的「已清空待回收」结论只
    适用于它自己那个快照（retired=None），绝不能把 API 刚写下的 retired=1 抹掉——抹掉
    等于 v1 在网关侧永远无人回收（孤儿 Key）。
    """
    tenant_id, user_id = await _seed_tenant(session_factory)
    await _put_desired(session_factory, _policy(tenant_id, user_id), expected_revision=0)

    async def handler(claimed: ClaimedProvisioningCommand) -> ReconcileOutcome:
        # 模拟对账进行中 API 完成了一次轮换（v1 → v2，v1 待回收）
        async with session_factory() as session:
            repository = SqlAlchemyModelGatewayPolicyRepository(session)
            current = await repository.get(tenant_id)
            key = await repository.get_key(tenant_id)
            assert current is not None and key is not None
            await repository.save_rotated_key(
                current.revise_desired(
                    enabled=current.enabled,
                    allowed_aliases=current.allowed_aliases,
                    budget_microusd=current.budget_microusd,
                    budget_period=current.budget_period,
                    rpm_limit=current.rpm_limit,
                    tpm_limit=current.tpm_limit,
                    max_parallel_requests=current.max_parallel_requests,
                    updated_by=user_id,
                    now=NOW,
                ),
                key=key.rotate(now=NOW),
                expected_revision=current.revision,
                action=ModelGatewayProvisioningAction.RECONCILE,
            )
            await session.commit()
        return _COMPLETED

    await SqlAlchemyModelGatewayCommandStore(session_factory).process_next(handler, now=NOW)

    async with session_factory() as session:
        key = await session.get(TenantModelGatewayKeyRecord, tenant_id)
    assert key is not None
    assert key.key_version == 2
    # API 写下的待回收记录必须原样保留，否则 v1 在网关侧成为孤儿
    assert key.retired_key_version == 1
    # 本次对账真实观测到的是 v1 已可用
    assert key.provisioned_key_version == 1
