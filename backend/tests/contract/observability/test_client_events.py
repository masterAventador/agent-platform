from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_platform.api.app import create_app
from agent_platform.config import AppSettings
from agent_platform.infrastructure.database.base import Base
from agent_platform.infrastructure.database.models import load_database_models
from agent_platform.observability.metrics import OperationalComponent
from agent_platform.platform.knowledge.models import KnowledgeDataset


class AllowAllRateLimiter:
    async def ensure_allowed(self, *, scope: str, key: str) -> None:
        del scope, key


class FakeKnowledgeProvider:
    provider_name = "fake-knowledge"

    async def create_dataset(
        self,
        *,
        name: str,
        description: str = "",
        chunk_method: str = "naive",
    ) -> KnowledgeDataset:
        del description, chunk_method
        return KnowledgeDataset(provider_id=f"dataset-{name}", name=name)

    async def delete_dataset(self, provider_id: str) -> None:
        del provider_id


@dataclass(slots=True)
class RecordingMetrics:
    calls: list[tuple[OperationalComponent, str, str, float]] = field(default_factory=list)

    def record(
        self,
        *,
        component: OperationalComponent,
        operation: str,
        outcome: str,
        duration_ms: float,
    ) -> None:
        self.calls.append((component, operation, outcome, duration_ms))


@dataclass(frozen=True, slots=True)
class ClientEventHarness:
    client: AsyncClient
    metrics: RecordingMetrics


@pytest_asyncio.fixture
async def client_event_harness() -> AsyncIterator[ClientEventHarness]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    load_database_models()
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    app = create_app(
        settings=AppSettings(auth_cookie_secure=False),
        session_factory=session_factory,
        auth_rate_limiter=AllowAllRateLimiter(),
        knowledge_provider=FakeKnowledgeProvider(),
    )
    metrics = RecordingMetrics()
    app.state.telemetry.operational_metrics = metrics
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        yield ClientEventHarness(client=client, metrics=metrics)
    await engine.dispose()


async def _register_and_login(client: AsyncClient) -> str:
    credentials = {
        "email": f"client-events-{uuid4()}@example.com",
        "password": "correct horse battery staple",
    }
    assert (await client.post("/api/v1/auth/register", json=credentials)).status_code == 201
    assert (await client.post("/api/v1/auth/login", json=credentials)).status_code == 200
    current = await client.get("/api/v1/auth/me")
    assert current.status_code == 200
    return current.json()["workspaces"][0]["id"]


@pytest.mark.asyncio
async def test_client_event_requires_auth_and_records_only_bounded_dimensions(
    client_event_harness: ClientEventHarness,
) -> None:
    payload = {"operation": "api", "outcome": "failed", "duration_ms": 42.5}
    anonymous = await client_event_harness.client.post(
        "/api/v1/observability/client-events",
        json=payload,
    )
    assert anonymous.status_code == 401

    tenant_id = await _register_and_login(client_event_harness.client)
    response = await client_event_harness.client.post(
        "/api/v1/observability/client-events",
        headers={"X-Tenant-ID": tenant_id},
        json=payload,
    )

    assert response.status_code == 204
    assert client_event_harness.metrics.calls == [
        (OperationalComponent.CLIENT, "api", "failed", 42.5)
    ]


@pytest.mark.asyncio
async def test_client_event_rejects_unbounded_or_content_bearing_payloads(
    client_event_harness: ClientEventHarness,
) -> None:
    tenant_id = await _register_and_login(client_event_harness.client)
    headers = {"X-Tenant-ID": tenant_id}

    unsupported = await client_event_harness.client.post(
        "/api/v1/observability/client-events",
        headers=headers,
        json={"operation": "custom-action", "outcome": "failed", "duration_ms": 1},
    )
    content_bearing = await client_event_harness.client.post(
        "/api/v1/observability/client-events",
        headers=headers,
        json={
            "operation": "error",
            "outcome": "failed",
            "duration_ms": 1,
            "message": "password=must-not-leave-client",
        },
    )

    assert unsupported.status_code == 422
    assert content_bearing.status_code == 422
    assert client_event_harness.metrics.calls == []
