from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_platform.api.app import create_app
from agent_platform.config import AppSettings
from agent_platform.infrastructure.database.base import Base
from agent_platform.infrastructure.database.models import load_database_models
from agent_platform.infrastructure.database.repositories.tenants import TenantMembershipRecord
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
        self.deleted_documents: list[tuple[str, list[str]]] = []
        self.parsed: list[tuple[str, list[str]]] = []
        self.create_error: Exception | None = None
        self.fail_upload_at: int | None = None
        self.parse_error: Exception | None = None
        self.delete_documents_error_once: Exception | None = None
        self._document_sequence = 0

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
        self._document_sequence += 1
        if self.fail_upload_at is not None and self._document_sequence >= self.fail_upload_at:
            raise KnowledgeProviderUnavailable("上传中断")
        return KnowledgeDocument(
            provider_id=f"document-{self._document_sequence}",
            name=filename,
            status="UNSTART",
            size_bytes=len(content),
        )

    async def start_parsing(self, *, dataset_id: str, document_ids: list[str]) -> None:
        self.calls.append(("parse", dataset_id))
        if self.parse_error is not None:
            raise self.parse_error
        self.parsed.append((dataset_id, document_ids))

    async def delete_documents(self, *, dataset_id: str, document_ids: list[str]) -> None:
        self.calls.append(("delete_documents", dataset_id))
        if self.delete_documents_error_once is not None:
            error = self.delete_documents_error_once
            self.delete_documents_error_once = None
            raise error
        self.deleted_documents.append((dataset_id, document_ids))

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
async def test_document_lifecycle_supports_batch_retry_update_and_delete(
    knowledge_client,
) -> None:
    client, provider, _ = knowledge_client
    credentials = {
        "email": "knowledge-doc-lifecycle@example.com",
        "password": "correct horse battery staple",
    }
    await client.post("/api/v1/auth/register", json=credentials)
    await client.post("/api/v1/auth/login", json=credentials)
    tenant_id = (await client.get("/api/v1/auth/me")).json()["workspaces"][0]["id"]
    headers = {"X-Tenant-ID": tenant_id}
    created = await client.post(
        "/api/v1/knowledge-bases",
        headers=headers,
        json={"name": "文档生命周期知识库"},
    )
    assert created.status_code == 201
    knowledge_base = created.json()

    batch = await client.post(
        f"/api/v1/knowledge-bases/{knowledge_base['id']}/documents/batch",
        headers=headers,
        files=[
            ("files", ("policy-a.txt", b"policy a", "text/plain")),
            ("files", ("policy-b.txt", b"policy b", "text/plain")),
        ],
    )
    assert batch.status_code == 200
    assert [document["provider_id"] for document in batch.json()] == [
        "document-1",
        "document-2",
    ]
    assert provider.parsed[-1][1] == ["document-1", "document-2"]

    retry = await client.post(
        f"/api/v1/knowledge-bases/{knowledge_base['id']}/documents/document-1/retry",
        headers=headers,
    )
    assert retry.status_code == 204
    assert provider.parsed[-1][1] == ["document-1"]

    updated = await client.put(
        f"/api/v1/knowledge-bases/{knowledge_base['id']}/documents/document-1",
        headers=headers,
        files={"file": ("policy-a-v2.txt", b"policy a v2", "text/plain")},
    )
    assert updated.status_code == 200
    assert updated.json()["provider_id"] == "document-3"
    assert [call[0] for call in provider.calls[-3:]] == [
        "upload",
        "parse",
        "delete_documents",
    ]
    assert len({call[1] for call in provider.calls[-3:]}) == 1
    assert provider.deleted_documents[-1][1] == ["document-1"]
    assert provider.parsed[-1][1] == ["document-3"]

    deleted = await client.delete(
        f"/api/v1/knowledge-bases/{knowledge_base['id']}/documents/document-2",
        headers=headers,
    )
    assert deleted.status_code == 204
    assert provider.deleted_documents[-1][1] == ["document-2"]


async def _login_and_create_base(client, email: str) -> tuple[dict[str, str], str]:
    credentials = {"email": email, "password": "correct horse battery staple"}
    await client.post("/api/v1/auth/register", json=credentials)
    await client.post("/api/v1/auth/login", json=credentials)
    tenant_id = (await client.get("/api/v1/auth/me")).json()["workspaces"][0]["id"]
    headers = {"X-Tenant-ID": tenant_id}
    created = await client.post(
        "/api/v1/knowledge-bases",
        headers=headers,
        json={"name": "补偿测试知识库"},
    )
    assert created.status_code == 201
    return headers, created.json()["id"]


@pytest.mark.asyncio
async def test_batch_upload_rejects_more_documents_than_the_limit(knowledge_client) -> None:
    client, provider, _ = knowledge_client
    headers, knowledge_base_id = await _login_and_create_base(client, "batch-limit@example.com")

    response = await client.post(
        f"/api/v1/knowledge-bases/{knowledge_base_id}/documents/batch",
        headers=headers,
        files=[("files", (f"doc-{index}.txt", b"content", "text/plain")) for index in range(21)],
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "document_batch_too_large"
    assert all(call[0] != "upload" for call in provider.calls)


@pytest.mark.asyncio
async def test_batch_upload_midway_failure_compensates_uploaded_documents(
    knowledge_client,
) -> None:
    client, provider, _ = knowledge_client
    headers, knowledge_base_id = await _login_and_create_base(
        client, "batch-compensation@example.com"
    )
    provider.fail_upload_at = 3

    response = await client.post(
        f"/api/v1/knowledge-bases/{knowledge_base_id}/documents/batch",
        headers=headers,
        files=[
            ("files", ("doc-a.txt", b"a", "text/plain")),
            ("files", ("doc-b.txt", b"b", "text/plain")),
            ("files", ("doc-c.txt", b"c", "text/plain")),
        ],
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "knowledge_provider_unavailable"
    assert provider.parsed == []
    assert provider.deleted_documents[-1][1] == ["document-1", "document-2"]


@pytest.mark.asyncio
async def test_batch_parse_failure_compensates_uploaded_documents(knowledge_client) -> None:
    client, provider, _ = knowledge_client
    headers, knowledge_base_id = await _login_and_create_base(
        client, "batch-parse-compensation@example.com"
    )
    provider.parse_error = KnowledgeProviderUnavailable("解析请求失败")

    response = await client.post(
        f"/api/v1/knowledge-bases/{knowledge_base_id}/documents/batch",
        headers=headers,
        files=[
            ("files", ("doc-a.txt", b"a", "text/plain")),
            ("files", ("doc-b.txt", b"b", "text/plain")),
        ],
    )

    assert response.status_code == 503
    assert provider.parsed == []
    assert provider.deleted_documents[-1][1] == ["document-1", "document-2"]


@pytest.mark.asyncio
async def test_replace_failure_compensates_the_new_document(knowledge_client) -> None:
    client, provider, _ = knowledge_client
    headers, knowledge_base_id = await _login_and_create_base(
        client, "replace-compensation@example.com"
    )
    uploaded = await client.post(
        f"/api/v1/knowledge-bases/{knowledge_base_id}/documents",
        headers=headers,
        files={"file": ("policy.txt", b"v1", "text/plain")},
    )
    assert uploaded.status_code == 200
    provider.delete_documents_error_once = KnowledgeProviderUnavailable("删除旧文档失败")

    response = await client.put(
        f"/api/v1/knowledge-bases/{knowledge_base_id}/documents/document-1",
        headers=headers,
        files={"file": ("policy-v2.txt", b"v2", "text/plain")},
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "knowledge_provider_unavailable"
    # 补偿删除新上传的 document-2，旧 document-1 保持原状
    assert provider.deleted_documents[-1][1] == ["document-2"]


@pytest.mark.asyncio
async def test_admin_manages_knowledge_while_member_is_read_only(knowledge_client) -> None:
    client, _, app = knowledge_client
    password = "correct horse battery staple"
    owner_credentials = {"email": "knowledge-owner@example.com", "password": password}
    await client.post("/api/v1/auth/register", json=owner_credentials)
    await client.post("/api/v1/auth/login", json=owner_credentials)
    tenant_id = UUID((await client.get("/api/v1/auth/me")).json()["workspaces"][0]["id"])
    headers = {"X-Tenant-ID": str(tenant_id)}
    created = await client.post(
        "/api/v1/knowledge-bases",
        headers=headers,
        json={"name": "权限演示知识库"},
    )
    assert created.status_code == 201

    async def join_workspace(email: str, role: str) -> None:
        await client.post("/api/v1/auth/logout")
        credentials = {"email": email, "password": password}
        await client.post("/api/v1/auth/register", json=credentials)
        await client.post("/api/v1/auth/login", json=credentials)
        user_id = UUID((await client.get("/api/v1/auth/me")).json()["id"])
        async with app.state.session_factory() as session:
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

    await join_workspace("knowledge-admin@example.com", "admin")
    admin_created = await client.post(
        "/api/v1/knowledge-bases",
        headers=headers,
        json={"name": "管理员知识库"},
    )
    assert admin_created.status_code == 201
    admin_uploaded = await client.post(
        f"/api/v1/knowledge-bases/{created.json()['id']}/documents",
        headers=headers,
        files={"file": ("admin.txt", b"admin", "text/plain")},
    )
    assert admin_uploaded.status_code == 200

    await join_workspace("knowledge-member@example.com", "member")
    assert (await client.get("/api/v1/knowledge-bases", headers=headers)).status_code == 200
    member_created = await client.post(
        "/api/v1/knowledge-bases",
        headers=headers,
        json={"name": "成员不得创建"},
    )
    member_uploaded = await client.post(
        f"/api/v1/knowledge-bases/{created.json()['id']}/documents",
        headers=headers,
        files={"file": ("member.txt", b"member", "text/plain")},
    )
    member_deleted = await client.delete(
        f"/api/v1/knowledge-bases/{created.json()['id']}",
        headers=headers,
    )
    assert member_created.status_code == 403
    assert member_uploaded.status_code == 403
    assert member_deleted.status_code == 403


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
