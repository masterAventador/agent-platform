import json

import httpx
import pytest

from agent_platform.knowledge.ragflow import RagFlowClient, RagFlowError


@pytest.mark.asyncio
async def test_ragflow_client_uses_public_dataset_document_and_retrieval_apis() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/v1/datasets":
            return httpx.Response(200, json={"code": 0, "data": {"id": "ds-1", "name": "制度库"}})
        if request.url.path.endswith("/documents"):
            return httpx.Response(200, json={"code": 0, "data": [{
                "id": "doc-1", "name": "handbook.pdf", "run": "UNSTART", "size": 42,
            }]})
        if request.url.path.endswith("/chunks"):
            return httpx.Response(200, json={"code": 0})
        if request.url.path == "/api/v1/retrieval":
            return httpx.Response(200, json={"code": 0, "data": {"total": 1, "chunks": [{
                "id": "chunk-1", "document_id": "doc-1", "document_name": "handbook.pdf",
                "dataset_id": "ds-1", "content": "年假为十天", "similarity": 0.91,
                "document_metadata": {"department": "HR"},
            }]}})
        raise AssertionError(request.url)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://ragflow") as http:
        client = RagFlowClient(base_url="http://ragflow", api_key="secret", client=http)
        dataset = await client.create_dataset(name="制度库")
        document = await client.upload_document(
            dataset_id=dataset.provider_id, filename="handbook.pdf",
            content=b"pdf", content_type="application/pdf",
        )
        await client.start_parsing(
            dataset_id=dataset.provider_id,
            document_ids=[document.provider_id],
        )
        result = await client.retrieve(
            question="年假有几天", dataset_ids=[dataset.provider_id],
            metadata_condition={"logic": "and", "conditions": []},
        )

    assert dataset.provider_id == "ds-1"
    assert document.status == "UNSTART"
    assert result.citations[0].content == "年假为十天"
    assert all(request.headers["authorization"] == "Bearer secret" for request in requests)
    retrieval_body = json.loads(requests[-1].content)
    assert retrieval_body["metadata_condition"]["logic"] == "and"


@pytest.mark.asyncio
async def test_ragflow_error_envelope_does_not_leak_provider_response() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"code": 102, "message": "invalid dataset"})
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://ragflow") as http:
        client = RagFlowClient(base_url="http://ragflow", api_key="secret", client=http)
        with pytest.raises(RagFlowError, match="invalid dataset"):
            await client.create_dataset(name="broken")
