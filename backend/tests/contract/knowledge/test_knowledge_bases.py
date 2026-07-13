from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_platform.api.app import create_app
from agent_platform.config import AppSettings
from agent_platform.infrastructure.database.base import Base
from agent_platform.infrastructure.database.models import load_database_models
from agent_platform.platform.knowledge.errors import (
    InvalidKnowledgeProviderResponse,
    KnowledgeProviderUnavailable,
)
from agent_platform.platform.knowledge.models import (
    KnowledgeCitation,
    KnowledgeDataset,
    KnowledgeDocument,
    KnowledgeSearchResult,
)
from agent_platform.platform.knowledge.registry import KnowledgeProviderRegistry


class AllowAllRateLimiter:
    async def ensure_allowed(self, *, scope: str, key: str) -> None:
        del scope, key


class FakeKnowledgeProvider:
    def __init__(self, provider_name: str = "fake-knowledge") -> None:
        self.provider_name = provider_name
        self.calls: list[tuple[str, str]] = []
        self.deleted: list[str] = []
        self.parsed: list[tuple[str, list[str]]] = []
        self.create_error: Exception | None = None

    async def create_dataset(
        self, *, name: str, description: str = "", chunk_method: str = "naive"
    ):
        del description, chunk_method
        if self.create_error is not None:
            raise self.create_error
        return KnowledgeDataset(provider_id=f"dataset-{name}", name=name)

    async def delete_dataset(self, provider_id: str) -> None:
        self.calls.append(("delete", provider_id))
        self.deleted.append(provider_id)

    async def upload_document(
        self, *, dataset_id: str, filename: str, content: bytes, content_type: str
    ):
        del content_type
        self.calls.append(("upload", dataset_id))
        return KnowledgeDocument(
            provider_id="document-1", name=filename, status="UNSTART", size_bytes=len(content)
        )

    async def start_parsing(self, *, dataset_id: str, document_ids: list[str]) -> None:
        self.calls.append(("parse", dataset_id))
        self.parsed.append((dataset_id, document_ids))

    async def list_documents(self, *, dataset_id: str):
        self.calls.append(("list", dataset_id))
        return [KnowledgeDocument(provider_id="document-1", name="policy.txt", status="DONE")]

    async def retrieve(
        self,
        *,
        question: str,
        dataset_ids: list[str],
        page_size: int = 10,
        metadata_condition=None,
    ):
        del question, page_size, metadata_condition
        self.calls.append(("retrieve", dataset_ids[0]))
        return KnowledgeSearchResult(
            total=1,
            citations=[
                KnowledgeCitation(
                    chunk_id="chunk-1",
                    document_id="document-1",
                    document_name="policy.txt",
                    dataset_id=dataset_ids[0],
                    content="年假十天",
                    score=0.9,
                )
            ],
        )


@pytest_asyncio.fixture
async def knowledge_client() -> AsyncIterator[tuple[AsyncClient, FakeKnowledgeProvider, FastAPI]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    load_database_models()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    provider = FakeKnowledgeProvider()
    app = create_app(
        settings=AppSettings(auth_cookie_secure=False),
        session_factory=async_sessionmaker(engine, expire_on_commit=False),
        auth_rate_limiter=AllowAllRateLimiter(),
        knowledge_provider=provider,
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client, provider, app
    await engine.dispose()


@pytest.mark.asyncio
async def test_tenant_knowledge_base_document_and_retrieval_flow(knowledge_client) -> None:
    client, provider, _ = knowledge_client
    credentials = {"email": "knowledge@example.com", "password": "correct horse battery staple"}
    await client.post("/api/v1/auth/register", json=credentials)
    await client.post("/api/v1/auth/login", json=credentials)
    tenant_id = (await client.get("/api/v1/auth/me")).json()["workspaces"][0]["id"]
    headers = {"X-Tenant-ID": tenant_id}

    created = await client.post(
        "/api/v1/knowledge-bases",
        headers=headers,
        json={"name": "员工制度", "description": "企业人事制度"},
    )
    assert created.status_code == 201
    knowledge_base = created.json()
    assert knowledge_base["name"] == "员工制度"
    assert knowledge_base["provider"] == provider.provider_name
    assert "provider_id" not in knowledge_base
    assert (await client.get("/api/v1/knowledge-bases", headers=headers)).json() == [knowledge_base]

    uploaded = await client.post(
        f"/api/v1/knowledge-bases/{knowledge_base['id']}/documents",
        headers=headers,
        files={"file": ("policy.txt", b"annual leave", "text/plain")},
    )
    assert uploaded.status_code == 200
    assert uploaded.json()["name"] == "policy.txt"
    assert provider.parsed[0][1] == ["document-1"]

    retrieved = await client.post(
        f"/api/v1/knowledge-bases/{knowledge_base['id']}/retrieve",
        headers=headers,
        json={"question": "年假几天"},
    )
    assert retrieved.json()["citations"][0]["content"] == "年假十天"

    deleted = await client.delete(
        f"/api/v1/knowledge-bases/{knowledge_base['id']}", headers=headers
    )
    assert deleted.status_code == 204
    assert len(provider.deleted) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider_error",
    [KnowledgeProviderUnavailable(), InvalidKnowledgeProviderResponse()],
)
async def test_knowledge_provider_failures_return_stable_api_error(
    knowledge_client, provider_error: Exception
) -> None:
    client, provider, _ = knowledge_client
    credentials = {
        "email": "provider-error@example.com",
        "password": "correct horse battery staple",
    }
    await client.post("/api/v1/auth/register", json=credentials)
    await client.post("/api/v1/auth/login", json=credentials)
    tenant_id = (await client.get("/api/v1/auth/me")).json()["workspaces"][0]["id"]
    provider.create_error = provider_error

    response = await client.post(
        "/api/v1/knowledge-bases",
        headers={"X-Tenant-ID": tenant_id},
        json={"name": "不可用知识库"},
    )

    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "code": "knowledge_provider_unavailable",
            "message": "知识服务暂时不可用，请稍后重试",
        }
    }
    assert (
        await client.get(
            "/api/v1/knowledge-bases",
            headers={"X-Tenant-ID": tenant_id},
        )
    ).json() == []


@pytest.mark.asyncio
async def test_existing_base_never_routes_provider_a_id_to_provider_b(knowledge_client) -> None:
    client, provider_a, app = knowledge_client
    credentials = {
        "email": "provider-routing@example.com",
        "password": "correct horse battery staple",
    }
    await client.post("/api/v1/auth/register", json=credentials)
    await client.post("/api/v1/auth/login", json=credentials)
    tenant_id = (await client.get("/api/v1/auth/me")).json()["workspaces"][0]["id"]
    headers = {"X-Tenant-ID": tenant_id}
    created = await client.post(
        "/api/v1/knowledge-bases",
        headers=headers,
        json={"name": "供应商绑定知识库"},
    )
    assert created.status_code == 201
    knowledge_base_id = created.json()["id"]
    assert created.json()["provider"] == provider_a.provider_name

    provider_b = FakeKnowledgeProvider(provider_name="provider-b")
    app.state.knowledge_provider_registry = KnowledgeProviderRegistry([provider_b])
    requests = [
        await client.post(
            f"/api/v1/knowledge-bases/{knowledge_base_id}/documents",
            headers=headers,
            files={"file": ("policy.txt", b"policy", "text/plain")},
        ),
        await client.get(
            f"/api/v1/knowledge-bases/{knowledge_base_id}/documents",
            headers=headers,
        ),
        await client.post(
            f"/api/v1/knowledge-bases/{knowledge_base_id}/retrieve",
            headers=headers,
            json={"question": "制度是什么"},
        ),
        await client.delete(
            f"/api/v1/knowledge-bases/{knowledge_base_id}",
            headers=headers,
        ),
    ]

    assert provider_b.calls == []
    assert all(response.status_code == 503 for response in requests)
    assert all(
        response.json()["detail"]["code"] == "knowledge_provider_unavailable"
        for response in requests
    )
