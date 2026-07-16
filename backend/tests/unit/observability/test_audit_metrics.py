"""审计指标必须经真实仓储写入路径记录，禁止直接构造终态制造覆盖假象。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import uuid4

import pytest
import pytest_asyncio
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agent_platform.config import AppSettings
from agent_platform.infrastructure.database.base import Base
from agent_platform.infrastructure.database.models import load_database_models
from agent_platform.infrastructure.database.repositories.audit import (
    AuditChainStateRecord,
    AuditEventCreate,
    AuditEventRecord,
    SqlAlchemyAuditEventRepository,
    emit_audit_event,
)
from agent_platform.observability.metrics import (
    Meter,
    OperationalMetrics,
    active_operational_metrics,
    set_operational_metrics,
)
from agent_platform.observability.telemetry import (
    InstrumentorSet,
    TelemetryProviders,
    configure_telemetry,
)


@dataclass
class RecordingInstrument:
    calls: list[tuple[float, dict[str, str]]] = field(default_factory=list)

    def add(self, value: int, attributes: dict[str, str] | None = None) -> None:
        self.calls.append((value, attributes or {}))

    def record(self, value: float, attributes: dict[str, str] | None = None) -> None:
        self.calls.append((value, attributes or {}))


class RecordingMeter:
    def __init__(self) -> None:
        self.instruments: dict[str, RecordingInstrument] = {}

    def create_counter(self, name: str, **_: object) -> RecordingInstrument:
        return self.instruments.setdefault(name, RecordingInstrument())

    def create_histogram(self, name: str, **_: object) -> RecordingInstrument:
        return self.instruments.setdefault(name, RecordingInstrument())


@pytest.fixture
def recording_metrics() -> AsyncIterator[RecordingMeter]:
    meter = RecordingMeter()
    set_operational_metrics(OperationalMetrics(cast(Meter, meter)))
    yield meter
    set_operational_metrics(None)


@pytest_asyncio.fixture
async def audit_sessions() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    load_database_models()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _counter_calls(
    meter: RecordingMeter, name: str
) -> list[tuple[float, dict[str, str]]]:
    instrument = meter.instruments.get(name)
    return instrument.calls if instrument is not None else []


def _seed_sequence_conflict(session: AsyncSession, tenant_id) -> None:  # type: ignore[no-untyped-def]
    occurred_at = datetime.now(UTC)
    session.add(
        AuditEventRecord(
            id=uuid4(),
            tenant_id=tenant_id,
            actor_user_id=None,
            sequence=1,
            action="seed.conflict",
            resource_type="test",
            resource_id=None,
            outcome="succeeded",
            occurred_at=occurred_at,
            correlation_id=None,
            previous_hash=None,
            event_hash="0" * 64,
            metadata_json={},
        )
    )
    session.add(
        AuditChainStateRecord(
            tenant_id=tenant_id,
            head_sequence=0,
            head_hash="0" * 64,
            retained_from_sequence=1,
            retention_previous_hash=None,
            updated_at=occurred_at,
        )
    )


@pytest.mark.asyncio
async def test_unique_conflict_on_real_write_path_increments_audit_failed_counter(
    recording_metrics: RecordingMeter,
    audit_sessions: async_sessionmaker[AsyncSession],
) -> None:
    tenant_id = uuid4()
    async with audit_sessions() as session:
        _seed_sequence_conflict(session, tenant_id)
        await session.commit()

    async with audit_sessions() as session:
        with pytest.raises(IntegrityError):
            await SqlAlchemyAuditEventRepository(session).add(
                AuditEventCreate(
                    tenant_id=tenant_id,
                    actor_user_id=None,
                    action="test.conflict",
                    resource_type="test",
                )
            )

    failed = _counter_calls(recording_metrics, "agent_platform.audit.events.failed")
    assert failed == [(1, {"operation": "persist"})]
    operations = _counter_calls(recording_metrics, "agent_platform.audit.events")
    assert (1, {"operation": "persist", "outcome": "failed"}) in operations


@pytest.mark.asyncio
async def test_database_error_on_real_write_path_increments_audit_failed_counter(
    recording_metrics: RecordingMeter,
) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    load_database_models()
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async with sessions() as session:
        with pytest.raises(Exception, match="(?i)no such table"):
            await emit_audit_event(
                session,
                tenant_id=uuid4(),
                actor_user_id=None,
                action="test.database_error",
                resource_type="test",
            )
    await engine.dispose()

    failed = _counter_calls(recording_metrics, "agent_platform.audit.events.failed")
    assert failed == [(1, {"operation": "persist"})]


@pytest.mark.asyncio
async def test_successful_write_verify_and_retention_record_metrics_without_failures(
    recording_metrics: RecordingMeter,
    audit_sessions: async_sessionmaker[AsyncSession],
) -> None:
    tenant_id = uuid4()
    async with audit_sessions() as session:
        repository = SqlAlchemyAuditEventRepository(session)
        await repository.add(
            AuditEventCreate(
                tenant_id=tenant_id,
                actor_user_id=None,
                action="test.success",
                resource_type="test",
            )
        )
        verification = await repository.verify_integrity(tenant_id=tenant_id)
        purged = await repository.purge_before(
            tenant_id=tenant_id,
            cutoff=datetime.now(UTC) + timedelta(days=1),
            limit=100,
        )
        await session.commit()

    assert verification.valid is True
    assert purged == 1
    operations = _counter_calls(recording_metrics, "agent_platform.audit.events")
    attributes = [attrs for _, attrs in operations]
    assert {"operation": "persist", "outcome": "succeeded"} in attributes
    assert {"operation": "verify", "outcome": "succeeded"} in attributes
    assert {"operation": "retention", "outcome": "succeeded"} in attributes
    assert _counter_calls(recording_metrics, "agent_platform.audit.events.failed") == []


@pytest.mark.asyncio
async def test_audit_write_survives_metric_instrument_failure(
    audit_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """指标 instrument 抛异常不得回滚或阻断已成功的审计写入。"""

    class ExplodingMetrics:
        def record(self, **kwargs: object) -> None:
            raise RuntimeError("metric instrument exploded")

    tenant_id = uuid4()
    async with audit_sessions() as session:
        repository = SqlAlchemyAuditEventRepository(
            session,
            metrics=cast(OperationalMetrics, ExplodingMetrics()),
        )
        event = await repository.add(
            AuditEventCreate(
                tenant_id=tenant_id,
                actor_user_id=None,
                action="test.metric_failure",
                resource_type="test",
            )
        )
        await session.commit()

    assert event.sequence == 1
    async with audit_sessions() as session:
        stored = await SqlAlchemyAuditEventRepository(session).list(
            tenant_id=tenant_id,
            limit=10,
        )
    assert [record.action for record in stored] == ["test.metric_failure"]


@pytest.mark.asyncio
async def test_telemetry_wires_operational_metrics_into_audit_write_path(
    audit_sessions: async_sessionmaker[AsyncSession],
) -> None:
    metric_reader = InMemoryMetricReader()
    telemetry = configure_telemetry(
        AppSettings(otel_enabled=True),
        providers=TelemetryProviders(
            tracer_provider=TracerProvider(shutdown_on_exit=False),
            meter_provider=MeterProvider(
                metric_readers=[metric_reader],
                shutdown_on_exit=False,
            ),
            logger_provider=LoggerProvider(shutdown_on_exit=False),
        ),
        instrumentors=InstrumentorSet(),
    )
    try:
        assert active_operational_metrics() is telemetry.operational_metrics

        tenant_id = uuid4()
        async with audit_sessions() as session:
            _seed_sequence_conflict(session, tenant_id)
            await session.commit()
        async with audit_sessions() as session:
            with pytest.raises(IntegrityError):
                await emit_audit_event(
                    session,
                    tenant_id=tenant_id,
                    actor_user_id=None,
                    action="test.telemetry_conflict",
                    resource_type="test",
                )

        metrics_data = metric_reader.get_metrics_data()
        failed_total = 0.0
        for resource_metrics in metrics_data.resource_metrics:  # type: ignore[union-attr]
            for scope_metrics in resource_metrics.scope_metrics:
                for metric in scope_metrics.metrics:
                    if metric.name != "agent_platform.audit.events.failed":
                        continue
                    for point in metric.data.data_points:
                        failed_total += point.value
        assert failed_total == 1
    finally:
        set_operational_metrics(None)
