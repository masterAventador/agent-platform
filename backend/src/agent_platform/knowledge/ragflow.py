from pathlib import Path
from time import perf_counter
from typing import Any

import httpx
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    FiniteFloat,
    JsonValue,
    TypeAdapter,
    ValidationError,
)

from agent_platform.observability.metrics import OperationalComponent, OperationalMetrics
from agent_platform.platform.knowledge.errors import (
    InvalidKnowledgeProviderResponse,
    KnowledgeProviderRequestRejected,
    KnowledgeProviderUnavailable,
)
from agent_platform.platform.knowledge.models import (
    KnowledgeCitation,
    KnowledgeDataset,
    KnowledgeDocument,
    KnowledgeSearchResult,
)
from agent_platform.platform.knowledge.retrieval import KnowledgeRetrievalConfig


class _RagFlowEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore", strict=True)

    code: int
    data: Any = None


class _RagFlowDatasetPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore", strict=True)

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    document_count: int = Field(default=0, ge=0)
    chunk_count: int = Field(default=0, ge=0)


class _RagFlowDocumentPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore", strict=True)

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    run: str = Field(min_length=1)
    size: int = Field(default=0, ge=0)
    chunk_count: int = Field(default=0, ge=0)


class _RagFlowDocumentListPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore", strict=True)

    docs: list[_RagFlowDocumentPayload]


class _RagFlowCitationPayload(BaseModel):
    """真实 v0.25.6 检索响应经官方 key_mapping 后的 chunk 形态：
    文档名在 document_keyword，document_metadata 仅在请求 include_metadata 时注入。"""

    model_config = ConfigDict(frozen=True, extra="ignore", strict=True)

    id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    document_keyword: str = Field(min_length=1)
    dataset_id: str = Field(min_length=1)
    content: str
    similarity: FiniteFloat
    document_metadata: dict[str, JsonValue] = Field(default_factory=dict)


class _RagFlowRetrievalPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore", strict=True)

    total: int = Field(ge=0)
    chunks: list[_RagFlowCitationPayload]


_ENVELOPE_ADAPTER = TypeAdapter(_RagFlowEnvelope)
_DATASET_ADAPTER = TypeAdapter(_RagFlowDatasetPayload)
_DOCUMENTS_ADAPTER = TypeAdapter(list[_RagFlowDocumentPayload])
_DOCUMENT_LIST_ADAPTER = TypeAdapter(_RagFlowDocumentListPayload)
_RETRIEVAL_ADAPTER = TypeAdapter(_RagFlowRetrievalPayload)


def _validate_response[T](adapter: TypeAdapter[T], data: Any, message: str) -> T:
    try:
        return adapter.validate_python(data, strict=True)
    except ValidationError as error:
        raise InvalidKnowledgeProviderResponse(message) from error
    except (OverflowError, TypeError, ValueError) as error:
        raise InvalidKnowledgeProviderResponse(message) from error


class RagFlowClient:
    provider_name = "ragflow"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        client: httpx.AsyncClient | None = None,
        metrics: OperationalMetrics | None = None,
    ) -> None:
        self._owned_client = client is None
        self._client = client or httpx.AsyncClient(base_url=base_url, timeout=60.0)
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._metrics = metrics

    async def aclose(self) -> None:
        if self._owned_client:
            await self._client.aclose()

    async def create_dataset(
        self,
        *,
        name: str,
        description: str = "",
        chunk_method: str = "naive",
    ) -> KnowledgeDataset:
        data = await self._request(
            "POST",
            "/api/v1/datasets",
            operation="create_dataset",
            json={
                "name": name,
                "description": description,
                "permission": "me",
                "chunk_method": chunk_method,
            },
        )
        return self._dataset(data)

    async def delete_dataset(self, provider_id: str) -> None:
        await self._request(
            "DELETE",
            "/api/v1/datasets",
            operation="delete_dataset",
            json={"ids": [provider_id]},
        )

    async def upload_document(
        self,
        *,
        dataset_id: str,
        filename: str,
        content: bytes,
        content_type: str,
    ) -> KnowledgeDocument:
        data = await self._request(
            "POST",
            f"/api/v1/datasets/{dataset_id}/documents",
            operation="upload_document",
            files={"file": (Path(filename).name, content, content_type)},
        )
        documents = _validate_response(
            _DOCUMENTS_ADAPTER,
            data,
            "知识供应商文档响应格式错误",
        )
        if not documents:
            raise InvalidKnowledgeProviderResponse("知识供应商未返回上传文档")
        return self._document(documents[0])

    async def start_parsing(self, *, dataset_id: str, document_ids: list[str]) -> None:
        await self._request(
            "POST",
            f"/api/v1/datasets/{dataset_id}/chunks",
            operation="parse_document",
            json={"document_ids": document_ids},
        )

    async def delete_documents(self, *, dataset_id: str, document_ids: list[str]) -> None:
        await self._request(
            "DELETE",
            f"/api/v1/datasets/{dataset_id}/documents",
            operation="delete_documents",
            json={"ids": document_ids},
        )

    async def list_documents(self, *, dataset_id: str) -> list[KnowledgeDocument]:
        data = await self._request(
            "GET",
            f"/api/v1/datasets/{dataset_id}/documents",
            operation="list_chunks",
            params={"page": 1, "page_size": 100, "orderby": "create_time", "desc": "true"},
        )
        payload = _validate_response(
            _DOCUMENT_LIST_ADAPTER,
            data,
            "知识供应商文档列表响应格式错误",
        )
        return [self._document(record) for record in payload.docs]

    async def retrieve(
        self,
        *,
        question: str,
        dataset_ids: list[str],
        options: KnowledgeRetrievalConfig | None = None,
    ) -> KnowledgeSearchResult:
        resolved = options or KnowledgeRetrievalConfig()
        payload: dict[str, Any] = {
            "question": question,
            "dataset_ids": dataset_ids,
            "page": 1,
            "page_size": resolved.page_size,
            "similarity_threshold": resolved.similarity_threshold,
            "vector_similarity_weight": resolved.vector_similarity_weight,
            "top_k": resolved.top_k,
            "keyword": resolved.keyword,
            "include_metadata": True,
        }
        if resolved.rerank_id is not None:
            payload["rerank_id"] = resolved.rerank_id
        if resolved.metadata_condition is not None:
            payload["metadata_condition"] = resolved.metadata_condition.model_dump(mode="json")
        data = await self._request(
            "POST",
            "/api/v1/retrieval",
            operation="retrieve",
            json=payload,
        )
        response = _validate_response(
            _RETRIEVAL_ADAPTER,
            data,
            "知识供应商检索响应格式错误",
        )
        return KnowledgeSearchResult(
            total=response.total,
            citations=[self._citation(chunk) for chunk in response.chunks],
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        operation: str,
        **kwargs: Any,
    ) -> Any:
        started = perf_counter()
        try:
            response = await self._client.request(method, path, headers=self._headers, **kwargs)
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            self._record_metric(operation, "failed", started)
            status_code = error.response.status_code
            if 400 <= status_code < 500:
                raise KnowledgeProviderRequestRejected(
                    f"知识供应商拒绝了请求（HTTP {status_code}）"
                ) from error
            raise KnowledgeProviderUnavailable("知识供应商暂时不可用") from error
        except httpx.HTTPError as error:
            self._record_metric(operation, "failed", started)
            raise KnowledgeProviderUnavailable("知识供应商暂时不可用") from error
        try:
            envelope = response.json()
        except (OverflowError, ValueError) as error:
            self._record_metric(operation, "failed", started)
            raise InvalidKnowledgeProviderResponse("知识供应商返回了畸形 JSON") from error
        try:
            validated = _validate_response(
                _ENVELOPE_ADAPTER,
                envelope,
                "知识供应商响应信封格式错误",
            )
        except InvalidKnowledgeProviderResponse:
            self._record_metric(operation, "failed", started)
            raise
        if validated.code != 0:
            self._record_metric(operation, "failed", started)
            raise KnowledgeProviderRequestRejected(
                f"知识供应商拒绝了请求（业务错误码 {validated.code}）"
            )
        self._record_metric(operation, "succeeded", started)
        return validated.data

    def _record_metric(self, operation: str, outcome: str, started: float) -> None:
        if self._metrics is not None:
            self._metrics.record(
                component=OperationalComponent.RAGFLOW,
                operation=operation,
                outcome=outcome,
                duration_ms=(perf_counter() - started) * 1_000,
            )

    @staticmethod
    def _dataset(data: Any) -> KnowledgeDataset:
        payload = _validate_response(
            _DATASET_ADAPTER,
            data,
            "知识供应商数据集响应格式错误",
        )
        return KnowledgeDataset(
            provider_id=payload.id,
            name=payload.name,
            document_count=payload.document_count,
            chunk_count=payload.chunk_count,
        )

    @staticmethod
    def _document(data: _RagFlowDocumentPayload) -> KnowledgeDocument:
        return KnowledgeDocument(
            provider_id=data.id,
            name=data.name,
            status=data.run,
            size_bytes=data.size,
            chunk_count=data.chunk_count,
        )

    @staticmethod
    def _citation(data: _RagFlowCitationPayload) -> KnowledgeCitation:
        return KnowledgeCitation(
            chunk_id=data.id,
            document_id=data.document_id,
            document_name=data.document_keyword,
            dataset_id=data.dataset_id,
            content=data.content,
            score=data.similarity,
            metadata=data.document_metadata,
        )
