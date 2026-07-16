import json

import httpx
import pytest

from agent_platform.knowledge.ragflow import RagFlowClient
from agent_platform.platform.knowledge.errors import (
    InvalidKnowledgeProviderResponse,
    KnowledgeProviderUnavailable,
)


@pytest.mark.asyncio
async def test_ragflow_client_uses_public_dataset_document_and_retrieval_apis() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/v1/datasets":
            return httpx.Response(200, json={"code": 0, "data": {"id": "ds-1", "name": "制度库"}})
        if request.method == "DELETE" and request.url.path.endswith("/documents"):
            return httpx.Response(200, json={"code": 0})
        if request.url.path.endswith("/documents"):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": [
                        {
                            "id": "doc-1",
                            "name": "handbook.pdf",
                            "run": "UNSTART",
                            "size": 42,
                        }
                    ],
                },
            )
        if request.url.path.endswith("/chunks"):
            return httpx.Response(200, json={"code": 0})
        if request.url.path == "/api/v1/retrieval":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "total": 1,
                        "chunks": [
                            {
                                "id": "chunk-1",
                                "document_id": "doc-1",
                                "document_name": "handbook.pdf",
                                "dataset_id": "ds-1",
                                "content": "年假为十天",
                                "similarity": 0.91,
                                "document_metadata": {"department": "HR"},
                            }
                        ],
                    },
                },
            )
        raise AssertionError(request.url)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://ragflow") as http:
        client = RagFlowClient(base_url="http://ragflow", api_key="secret", client=http)
        dataset = await client.create_dataset(name="制度库")
        document = await client.upload_document(
            dataset_id=dataset.provider_id,
            filename="handbook.pdf",
            content=b"pdf",
            content_type="application/pdf",
        )
        await client.start_parsing(
            dataset_id=dataset.provider_id,
            document_ids=[document.provider_id],
        )
        await client.delete_documents(
            dataset_id=dataset.provider_id,
            document_ids=[document.provider_id],
        )
        result = await client.retrieve(
            question="年假有几天",
            dataset_ids=[dataset.provider_id],
            metadata_condition={"logic": "and", "conditions": []},
        )

    assert client.provider_name == "ragflow"
    assert dataset.provider_id == "ds-1"
    assert document.status == "UNSTART"
    assert result.citations[0].content == "年假为十天"
    assert all(request.headers["authorization"] == "Bearer secret" for request in requests)
    delete_request = next(
        request
        for request in requests
        if request.method == "DELETE" and "/documents" in str(request.url)
    )
    assert json.loads(delete_request.content) == {"ids": ["doc-1"]}
    retrieval_body = json.loads(requests[-1].content)
    assert retrieval_body["metadata_condition"]["logic"] == "and"


@pytest.mark.asyncio
async def test_ragflow_error_envelope_does_not_leak_provider_response() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"code": 102, "message": "invalid dataset"})
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://ragflow") as http:
        client = RagFlowClient(base_url="http://ragflow", api_key="secret", client=http)
        with pytest.raises(KnowledgeProviderUnavailable) as exception:
            await client.create_dataset(name="broken")
    assert "invalid dataset" not in str(exception.value)


@pytest.mark.asyncio
async def test_ragflow_connection_failure_maps_to_provider_unavailable() -> None:
    def fail_to_connect(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    transport = httpx.MockTransport(fail_to_connect)
    async with httpx.AsyncClient(transport=transport, base_url="http://ragflow") as http:
        client = RagFlowClient(base_url="http://ragflow", api_key="secret", client=http)
        with pytest.raises(KnowledgeProviderUnavailable):
            await client.create_dataset(name="broken")


@pytest.mark.asyncio
async def test_ragflow_malformed_success_json_maps_to_invalid_provider_response() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            content=b"not-json",
            headers={"content-type": "application/json"},
        )
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://ragflow") as http:
        client = RagFlowClient(base_url="http://ragflow", api_key="secret", client=http)
        with pytest.raises(InvalidKnowledgeProviderResponse):
            await client.create_dataset(name="broken")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "data",
    [
        {"name": "missing-id"},
        {"id": "ds-1", "name": "invalid-count", "document_count": "not-an-integer"},
        {"id": None, "name": "null-id"},
        {"id": "ds-1", "name": ["not", "a", "string"]},
        {"id": "ds-1", "name": "negative-count", "document_count": -1},
    ],
)
async def test_ragflow_invalid_dataset_fields_map_to_invalid_provider_response(data) -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"code": 0, "data": data})
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://ragflow") as http:
        client = RagFlowClient(base_url="http://ragflow", api_key="secret", client=http)
        with pytest.raises(InvalidKnowledgeProviderResponse):
            await client.create_dataset(name="broken")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "document",
    [
        {"id": "doc-1", "name": "policy.txt", "run": None},
        {"id": "doc-1", "name": ["policy.txt"], "run": "DONE"},
        {"id": "doc-1", "name": "policy.txt", "run": "DONE", "size": -1},
    ],
)
async def test_ragflow_invalid_document_fields_map_to_invalid_provider_response(
    document,
) -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"code": 0, "data": [document]})
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://ragflow") as http:
        client = RagFlowClient(base_url="http://ragflow", api_key="secret", client=http)
        with pytest.raises(InvalidKnowledgeProviderResponse):
            await client.upload_document(
                dataset_id="ds-1",
                filename="policy.txt",
                content=b"policy",
                content_type="text/plain",
            )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("content", "similarity"),
    [(None, 0.5), (["not", "content"], 0.5), ("valid", float("inf"))],
)
async def test_ragflow_invalid_citation_fields_map_to_invalid_provider_response(
    content, similarity: float
) -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            content=json.dumps(
                {
                    "code": 0,
                    "data": {
                        "total": 1,
                        "chunks": [
                            {
                                "id": "chunk-1",
                                "document_id": "doc-1",
                                "document_name": "policy.txt",
                                "dataset_id": "ds-1",
                                "content": content,
                                "similarity": similarity,
                                "document_metadata": {},
                            }
                        ],
                    },
                }
            ).encode(),
            headers={"content-type": "application/json"},
        )
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://ragflow") as http:
        client = RagFlowClient(base_url="http://ragflow", api_key="secret", client=http)
        with pytest.raises(InvalidKnowledgeProviderResponse):
            await client.retrieve(question="broken", dataset_ids=["ds-1"])


@pytest.mark.asyncio
async def test_ragflow_pydantic_validation_maps_to_invalid_provider_response() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "total": 1,
                    "chunks": [
                        {
                            "id": "chunk-1",
                            "document_id": "doc-1",
                            "dataset_id": "ds-1",
                            "document_metadata": ["not", "an", "object"],
                        }
                    ],
                },
            },
        )
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://ragflow") as http:
        client = RagFlowClient(base_url="http://ragflow", api_key="secret", client=http)
        with pytest.raises(InvalidKnowledgeProviderResponse):
            await client.retrieve(question="broken", dataset_ids=["ds-1"])
