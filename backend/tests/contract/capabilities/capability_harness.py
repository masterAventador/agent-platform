from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agent_platform.api.app import create_app
from agent_platform.config import AppSettings
from agent_platform.infrastructure.database.base import Base
from agent_platform.infrastructure.database.models import load_database_models
from agent_platform.infrastructure.database.repositories.tenants import TenantMembershipRecord
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


class InMemorySkillStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def put(self, *, key: str, content: bytes) -> None:
        self.objects[key] = content

    async def get(self, *, key: str) -> bytes:
        return self.objects[key]

    async def delete(self, *, key: str) -> None:
        self.objects.pop(key, None)


@dataclass(frozen=True, slots=True)
class CapabilityHarness:
    client: AsyncClient
    session_factory: async_sessionmaker[AsyncSession]
    app: Any

    async def register_and_login(self, email: str) -> dict[str, Any]:
        credentials = {"email": email, "password": "correct horse battery staple"}
        register = await self.client.post("/api/v1/auth/register", json=credentials)
        assert register.status_code == 201
        login = await self.client.post("/api/v1/auth/login", json=credentials)
        assert login.status_code == 200
        current = await self.client.get("/api/v1/auth/me")
        assert current.status_code == 200
        return current.json()

    async def add_member(self, *, tenant_id: UUID, user_id: UUID, role: str = "member") -> None:
        async with self.session_factory() as session:
            session.add(
                TenantMembershipRecord(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    user_id=user_id,
                    role=role,
                    created_at=datetime.now(UTC),
                )
            )
            await session.commit()


async def build_capability_harness(settings: AppSettings) -> tuple[CapabilityHarness, Any, Any]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    load_database_models()
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    app = create_app(
        settings=settings,
        session_factory=session_factory,
        auth_rate_limiter=AllowAllRateLimiter(),
        knowledge_provider=FakeKnowledgeProvider(),
        skill_storage=InMemorySkillStorage(),
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
    harness = CapabilityHarness(client=client, session_factory=session_factory, app=app)
    return harness, engine, client


