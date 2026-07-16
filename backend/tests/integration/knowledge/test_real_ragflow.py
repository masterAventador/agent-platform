"""真实 RAGFlow v0.25.6 集成验收门禁。

需要显式提供 TEST_RAGFLOW_URL 与 TEST_RAGFLOW_API_KEY 才运行（对齐真实 COS 门禁模式）；
使用 `infra/ragflow/manage.sh` 拉起锁定版本的官方独立栈后执行。

覆盖：数据集创建、文档上传、解析等待、检索（top_k/相似度阈值/召回条数）、
元数据过滤（命中与不命中）、引用返回、文档删除与数据集删除。
重排（rerank_id）仅在实例配置了 TEST_RAGFLOW_RERANK_ID 指定的重排模型时验证。
"""

import asyncio
import os
from uuid import uuid4

import httpx
import pytest

from agent_platform.knowledge.ragflow import RagFlowClient
from agent_platform.platform.knowledge.retrieval import validate_knowledge_retrieval_config

PARSE_TIMEOUT_SECONDS = 600.0


def _required_environment() -> tuple[str, str]:
    url = os.getenv("TEST_RAGFLOW_URL")
    api_key = os.getenv("TEST_RAGFLOW_API_KEY")
    missing = [
        name
        for name, value in {
            "TEST_RAGFLOW_URL": url,
            "TEST_RAGFLOW_API_KEY": api_key,
        }.items()
        if not value
    ]
    if missing:
        pytest.skip(f"需要 {', '.join(missing)} 才运行真实 RAGFlow 集成验收")
    assert url is not None and api_key is not None
    return url, api_key


async def _wait_until_parsed(client: RagFlowClient, dataset_id: str, expected: int) -> None:
    deadline = asyncio.get_event_loop().time() + PARSE_TIMEOUT_SECONDS
    while True:
        documents = await client.list_documents(dataset_id=dataset_id)
        states = {document.provider_id: document.status for document in documents}
        if len(states) == expected and all(status == "DONE" for status in states.values()):
            return
        if any(status in {"FAIL", "CANCEL"} for status in states.values()):
            raise AssertionError(f"真实 RAGFlow 解析失败: {states}")
        if asyncio.get_event_loop().time() > deadline:
            raise AssertionError(f"真实 RAGFlow 解析超时: {states}")
        await asyncio.sleep(3.0)


async def _set_document_metadata(
    *, base_url: str, api_key: str, dataset_id: str, document_id: str, metadata: dict[str, str]
) -> None:
    """测试准备步骤：通过官方文档更新 API 写入 meta_fields，用于元数据过滤验收。"""
    async with httpx.AsyncClient(base_url=base_url, timeout=60.0) as http:
        response = await http.put(
            f"/api/v1/datasets/{dataset_id}/documents/{document_id}",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"meta_fields": metadata},
        )
        response.raise_for_status()
        assert response.json()["code"] == 0, response.text


@pytest.mark.asyncio
async def test_real_ragflow_dataset_document_retrieval_and_cleanup() -> None:
    base_url, api_key = _required_environment()
    client = RagFlowClient(base_url=base_url, api_key=api_key)
    dataset_name = f"c07-acceptance-{uuid4().hex[:8]}"
    dataset_id: str | None = None

    try:
        dataset = await client.create_dataset(
            name=dataset_name, description="C07 真实集成验收数据集"
        )
        dataset_id = dataset.provider_id

        hr_document = await client.upload_document(
            dataset_id=dataset_id,
            filename="hr-annual-leave.txt",
            content="人事制度：正式员工每年享有十天带薪年假，入职满五年增加至十五天。".encode(),
            content_type="text/plain",
        )
        finance_document = await client.upload_document(
            dataset_id=dataset_id,
            filename="finance-reimburse.txt",
            content="财务制度：差旅报销需在出差结束后三十天内提交发票与行程单。".encode(),
            content_type="text/plain",
        )
        await _set_document_metadata(
            base_url=base_url,
            api_key=api_key,
            dataset_id=dataset_id,
            document_id=hr_document.provider_id,
            metadata={"department": "HR"},
        )
        await _set_document_metadata(
            base_url=base_url,
            api_key=api_key,
            dataset_id=dataset_id,
            document_id=finance_document.provider_id,
            metadata={"department": "Finance"},
        )
        await client.start_parsing(
            dataset_id=dataset_id,
            document_ids=[hr_document.provider_id, finance_document.provider_id],
        )
        await _wait_until_parsed(client, dataset_id, expected=2)

        # 默认配置检索：引用返回且字段完整（真实响应契约）
        default_result = await client.retrieve(
            question="员工每年有几天年假？",
            dataset_ids=[dataset_id],
        )
        assert default_result.citations, "真实 RAGFlow 默认检索未返回引用"
        top_citation = default_result.citations[0]
        assert top_citation.dataset_id == dataset_id
        assert top_citation.document_id == hr_document.provider_id
        assert top_citation.document_name == "hr-annual-leave.txt"
        assert "年假" in top_citation.content
        assert 0.0 <= top_citation.score <= 1.0
        assert top_citation.metadata.get("department") == "HR"

        # 召回参数：page_size=1 限制引用条数；top_k 与相似度阈值真实生效
        limited = await client.retrieve(
            question="报销 年假 制度",
            dataset_ids=[dataset_id],
            options=validate_knowledge_retrieval_config(
                {"page_size": 1, "top_k": 64, "similarity_threshold": 0.0}
            ),
        )
        assert len(limited.citations) == 1

        # 高阈值应过滤掉低相关内容
        strict = await client.retrieve(
            question="与知识库完全无关的火星探测器轨道参数",
            dataset_ids=[dataset_id],
            options=validate_knowledge_retrieval_config({"similarity_threshold": 0.99}),
        )
        assert strict.citations == []

        # 元数据过滤：命中 HR 只返回 HR 文档
        hr_only = await client.retrieve(
            question="公司制度规定了什么？",
            dataset_ids=[dataset_id],
            options=validate_knowledge_retrieval_config(
                {
                    "similarity_threshold": 0.0,
                    "metadata_condition": {
                        "logic": "and",
                        "conditions": [
                            {"name": "department", "comparison_operator": "=", "value": "HR"},
                        ],
                    },
                }
            ),
        )
        assert hr_only.citations, "元数据过滤命中 HR 时应返回引用"
        assert {citation.document_id for citation in hr_only.citations} == {
            hr_document.provider_id
        }

        # 元数据过滤：条件不命中返回空
        no_match = await client.retrieve(
            question="公司制度规定了什么？",
            dataset_ids=[dataset_id],
            options=validate_knowledge_retrieval_config(
                {
                    "similarity_threshold": 0.0,
                    "metadata_condition": {
                        "logic": "and",
                        "conditions": [
                            {"name": "department", "comparison_operator": "=", "value": "Legal"},
                        ],
                    },
                }
            ),
        )
        assert no_match.citations == []

        # 重排：仅在实例配置了对应重排模型时验证
        rerank_id = os.getenv("TEST_RAGFLOW_RERANK_ID")
        if rerank_id:
            reranked = await client.retrieve(
                question="员工每年有几天年假？",
                dataset_ids=[dataset_id],
                options=validate_knowledge_retrieval_config(
                    {"rerank_id": rerank_id, "similarity_threshold": 0.0}
                ),
            )
            assert reranked.citations
            assert reranked.citations[0].document_id == hr_document.provider_id

        # 文档删除
        await client.delete_documents(
            dataset_id=dataset_id,
            document_ids=[hr_document.provider_id, finance_document.provider_id],
        )
        assert await client.list_documents(dataset_id=dataset_id) == []
    finally:
        if dataset_id is not None:
            await client.delete_dataset(dataset_id)
        await client.aclose()
