"""真实 PostgreSQL 下的 provisioning outbox 多副本并发门禁（C16）。

SQLite 的 SELECT FOR UPDATE 是 no-op、无真实 MVCC，单元层「并发」不算数。本门禁用
真实独立 asyncpg session 制造真并发，验证：
① 多副本并发消费同一 outbox，一条命令绝不被执行两次（真实网关变更不重复）；
② FOR UPDATE SKIP LOCKED 让不同租户的命令并行、同租户串行；
③ 对账横跨事务期间 API 并发 PUT 新 revision 时，旧结论被 CAS 丢弃而不是覆盖新 desired；
④ 副本崩溃（连接中断）后命令自动回到 pending 由其他副本重入。

需 TEST_DATABASE_URL 才运行；缺失时 skip（不假绿）。
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import perf_counter
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from alembic import command as alembic_command
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_platform.infrastructure.database.repositories.auth import UserRecord
from agent_platform.infrastructure.database.repositories.model_gateway import (
    ModelGatewayProvisioningCommandRecord,
    SqlAlchemyModelGatewayCommandStore,
    SqlAlchemyModelGatewayPolicyRepository,
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
from tests.fixtures.postgres_reset import reset_database

BACKEND_ROOT = Path(__file__).parents[3]
NOW = datetime(2026, 7, 17, 12, 0, tzinfo=UTC)
_COMPLETED = ReconcileOutcome(
    command_status=ProvisioningCommandStatus.COMPLETED,
    policy_status=ModelGatewayPolicyStatus.ACTIVE,
    clear_key_retirement=True,
)


@pytest.fixture(scope="module")
def migrated_postgres_url() -> str:
    database_url = os.getenv("TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("需要 TEST_DATABASE_URL 才运行真实 PostgreSQL 模型网关并发门禁")
    config = Config(BACKEND_ROOT / "alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    alembic_command.upgrade(config, "head")
    return database_url


@pytest_asyncio.fixture
async def session_factory(migrated_postgres_url: str) -> AsyncIterator[async_sessionmaker]:
    engine = create_async_engine(migrated_postgres_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    # 用例前清理：本文件的断言按全库计数，必须不受前序测试文件残留影响。
    await reset_database(engine)
    yield factory
    # 用例后清理：不把数据泄漏给后续测试文件。
    await reset_database(engine)
    await engine.dispose()


async def _seed_tenant(factory: async_sessionmaker) -> tuple[UUID, UUID]:
    tenant_id, user_id = uuid4(), uuid4()
    async with factory() as session:
        session.add(
            TenantRecord(id=tenant_id, name="网关并发", slug=f"gw-{tenant_id.hex}", created_at=NOW)
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


@pytest.mark.asyncio
async def test_concurrent_replicas_never_provision_the_same_command_twice(
    session_factory: async_sessionmaker,
) -> None:
    """两个副本同时消费：同一命令只能产生一次真实网关变更。"""
    tenant_id, user_id = await _seed_tenant(session_factory)
    await _put_desired(session_factory, _policy(tenant_id, user_id), expected_revision=0)
    gateway_calls: list[UUID] = []
    started = asyncio.Event()

    async def handler(claimed: ClaimedProvisioningCommand) -> ReconcileOutcome:
        gateway_calls.append(claimed.command_id)
        started.set()
        # 持锁期间让另一副本充分尝试认领同一条命令
        await asyncio.sleep(0.4)
        return _COMPLETED

    async def replica() -> bool:
        return await SqlAlchemyModelGatewayCommandStore(session_factory).process_next(
            handler, now=NOW
        )

    first, second = await asyncio.gather(replica(), replica())

    assert len(gateway_calls) == 1
    assert sorted([first, second]) == [False, True]
    async with session_factory() as session:
        command = (
            await session.execute(select(ModelGatewayProvisioningCommandRecord))
        ).scalar_one()
    assert command.status == "completed"
    assert command.attempts == 1


@pytest.mark.asyncio
async def test_skip_locked_lets_other_tenants_proceed_in_parallel(
    session_factory: async_sessionmaker,
) -> None:
    """SKIP LOCKED 的价值是「不被别的租户阻塞」——必须断言真实并行，而不是只断言都成功。

    只断言 `results == [True, True]` 抓不到退化：把 skip_locked 改成普通 FOR UPDATE
    时两个副本仍都会成功，只是被串行化了。这里断言两次对账在时间上真实重叠。
    """
    first_tenant, first_user = await _seed_tenant(session_factory)
    second_tenant, second_user = await _seed_tenant(session_factory)
    await _put_desired(session_factory, _policy(first_tenant, first_user), expected_revision=0)
    await _put_desired(session_factory, _policy(second_tenant, second_user), expected_revision=0)
    hold_seconds = 0.4
    spans: list[tuple[UUID, float, float]] = []

    async def handler(claimed: ClaimedProvisioningCommand) -> ReconcileOutcome:
        started = perf_counter()
        await asyncio.sleep(hold_seconds)
        spans.append((claimed.tenant_id, started, perf_counter()))
        return _COMPLETED

    async def replica() -> bool:
        return await SqlAlchemyModelGatewayCommandStore(session_factory).process_next(
            handler, now=NOW
        )

    results = await asyncio.gather(replica(), replica())

    assert results == [True, True]
    assert {span[0] for span in spans} == {first_tenant, second_tenant}
    # 真实并行断言：两次持锁对账的时间区间必须重叠。串行化时不可能重叠。
    first_span, second_span = sorted(spans, key=lambda span: span[1])
    assert first_span[2] > second_span[1], (
        "两个租户的对账被串行化了：SKIP LOCKED 未生效"
    )


@pytest.mark.asyncio
async def test_same_tenant_revisions_are_reconciled_strictly_in_order(
    session_factory: async_sessionmaker,
) -> None:
    tenant_id, user_id = await _seed_tenant(session_factory)
    await _put_desired(session_factory, _policy(tenant_id, user_id), expected_revision=0)
    await _put_desired(
        session_factory,
        _policy(tenant_id, user_id, revision=2, enabled=False),
        expected_revision=1,
    )
    order: list[int] = []

    async def handler(claimed: ClaimedProvisioningCommand) -> ReconcileOutcome:
        order.append(claimed.desired_revision)
        await asyncio.sleep(0.3)
        return ReconcileOutcome(
            command_status=ProvisioningCommandStatus.COMPLETED,
            policy_status=(
                ModelGatewayPolicyStatus.ACTIVE
                if claimed.policy.enabled
                else ModelGatewayPolicyStatus.DISABLED
            ),
            clear_key_retirement=True,
        )

    async def replica() -> bool:
        return await SqlAlchemyModelGatewayCommandStore(session_factory).process_next(
            handler, now=NOW
        )

    # 并发两副本：rev2 不得与 rev1 同时对账
    first_round = await asyncio.gather(replica(), replica())
    assert sorted(first_round) == [False, True]
    assert order == [1]

    await replica()

    assert order == [1, 2]
    async with session_factory() as session:
        policy = await session.get(TenantModelGatewayPolicyRecord, tenant_id)
    assert policy is not None and policy.status == "disabled"


@pytest.mark.asyncio
async def test_a_revision_superseded_during_reconcile_does_not_overwrite_new_desired(
    session_factory: async_sessionmaker,
) -> None:
    """真实并发事务下的 CAS：旧 revision 的 active 结论不得覆盖新 desired 的 pending。"""
    tenant_id, user_id = await _seed_tenant(session_factory)
    await _put_desired(session_factory, _policy(tenant_id, user_id), expected_revision=0)

    async def handler(_claimed: ClaimedProvisioningCommand) -> ReconcileOutcome:
        await _put_desired(
            session_factory,
            _policy(tenant_id, user_id, revision=2, enabled=False),
            expected_revision=1,
        )
        return _COMPLETED

    await SqlAlchemyModelGatewayCommandStore(session_factory).process_next(handler, now=NOW)

    async with session_factory() as session:
        policy = await session.get(TenantModelGatewayPolicyRecord, tenant_id)
    assert policy is not None
    assert policy.revision == 2
    assert policy.status == "pending"


@pytest.mark.asyncio
async def test_a_crashed_replica_releases_the_command_for_another_replica(
    session_factory: async_sessionmaker,
) -> None:
    """副本在对账中途崩溃：行锁释放、命令仍 pending，另一副本自然重入。"""
    tenant_id, user_id = await _seed_tenant(session_factory)
    await _put_desired(session_factory, _policy(tenant_id, user_id), expected_revision=0)

    async def crashing(_claimed: ClaimedProvisioningCommand) -> ReconcileOutcome:
        raise RuntimeError("replica crashed mid-reconcile")

    with pytest.raises(RuntimeError):
        await SqlAlchemyModelGatewayCommandStore(session_factory).process_next(
            crashing, now=NOW
        )

    async with session_factory() as session:
        command = (
            await session.execute(select(ModelGatewayProvisioningCommandRecord))
        ).scalar_one()
    assert command.status == "pending"
    assert command.attempts == 0

    assert await SqlAlchemyModelGatewayCommandStore(session_factory).process_next(
        _completing(), now=NOW + timedelta(seconds=1)
    )


def _completing():
    async def handler(_claimed: ClaimedProvisioningCommand) -> ReconcileOutcome:
        return _COMPLETED

    return handler
