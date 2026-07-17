"""真实边界验收（C16 阶段二核心门禁）：真实 ChatOpenAI → 真实 LiteLLM → 回调捕获 → 真实 PG。

不是内存替身：用真实 ``ChatOpenAI`` 经 **真实 LiteLLM v1.86.2** 发一次真实 HTTP 推理调用，
让 ``ModelUsageCaptureHandler``（实例级 LangChain 回调，零侵入）在真实 ``on_llm_end`` 里触发，
经 ``SessionModelUsageRecorder`` 落进 **真实 PostgreSQL**，再直接查库断言：归属正确、token 来自
真实响应、费用 = 平台定价表按真实 token 计算（对齐设计 B 结论：LiteLLM 只把费用放响应头、
标准 ChatOpenAI 回调拿不到，故费用由平台定价表算）。

需同时设置 TEST_DATABASE_URL / TEST_LITELLM_URL / TEST_LITELLM_KEY 才运行；缺任一即 skip（不假绿）。
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command as alembic_command
from alembic.config import Config
from langchain_openai import ChatOpenAI
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_platform.infrastructure.database.repositories.model_usage import (
    SessionModelUsageRecorder,
    SqlAlchemyModelUsageRepository,
)
from agent_platform.infrastructure.database.repositories.tenants import TenantRecord
from agent_platform.platform.model_gateway.pricing import DEFAULT_MODEL_PRICING
from agent_platform.platform.model_gateway.usage import ModelCallOutcome, ModelUsageQuery
from agent_platform.workers.model_usage_capture import (
    ModelUsageCaptureHandler,
    attach_usage_capture,
)

BACKEND_ROOT = Path(__file__).parents[3]
NOW = datetime(2026, 7, 17, 12, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def migrated_postgres_url() -> str:
    database_url = os.getenv("TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("需要 TEST_DATABASE_URL 才运行真实边界验收")
    config = Config(BACKEND_ROOT / "alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    alembic_command.upgrade(config, "head")
    return database_url


@pytest.mark.asyncio
async def test_real_litellm_call_records_usage_with_attribution_and_pricing_cost(
    migrated_postgres_url: str,
) -> None:
    database_url = migrated_postgres_url
    litellm_url = os.getenv("TEST_LITELLM_URL")
    litellm_key = os.getenv("TEST_LITELLM_KEY")
    if not (litellm_url and litellm_key):
        pytest.skip("需要 TEST_LITELLM_URL / TEST_LITELLM_KEY 才运行真实边界验收")

    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    tenant_id, run_id, employee_id = uuid4(), uuid4(), uuid4()
    try:
        async with factory() as session:
            session.add(
                TenantRecord(
                    id=tenant_id,
                    name="用量真实验收",
                    slug=f"usage-real-{tenant_id.hex}",
                    created_at=NOW,
                )
            )
            await session.commit()

        handler = ModelUsageCaptureHandler(
            recorder=SessionModelUsageRecorder(factory),
            pricing=DEFAULT_MODEL_PRICING,
            tenant_id=tenant_id,
            run_id=run_id,
            employee_id=employee_id,
            model_alias="general-purpose",
        )
        model = ChatOpenAI(
            model="general-purpose",
            base_url=litellm_url,
            api_key=SecretStr(litellm_key),
            max_retries=0,
            timeout=30,
        )
        model = attach_usage_capture(model, handler)

        # 真实 HTTP 调用：ChatOpenAI → LiteLLM v1.86.2 → 上游，回调在真实 on_llm_end 触发。
        response = await model.ainvoke("hello")
        assert response.content is not None

        async with factory() as session:
            page = await SqlAlchemyModelUsageRepository(session).query(
                ModelUsageQuery(tenant_id=tenant_id)
            )
        assert len(page.records) == 1, "真实调用后应恰好落一条用量记录"
        record = page.records[0]
        # 归属正确
        assert record.tenant_id == tenant_id
        assert record.run_id == run_id
        assert record.employee_id == employee_id
        # provider-neutral alias，不是真实模型名
        assert record.model_alias == "general-purpose"
        # token 来自真实 LiteLLM 响应
        assert record.prompt_tokens is not None and record.prompt_tokens > 0
        assert record.completion_tokens is not None and record.completion_tokens > 0
        assert record.outcome is ModelCallOutcome.SUCCESS
        assert record.latency_ms >= 0
        # 费用 = 平台定价表按真实 token 计算（设计 B：不取 LiteLLM 响应头费用）
        expected_cost = DEFAULT_MODEL_PRICING.cost_nanousd(
            alias="general-purpose",
            prompt_tokens=record.prompt_tokens,
            completion_tokens=record.completion_tokens,
        )
        assert expected_cost is not None and expected_cost > 0
        assert record.cost_nanousd == expected_cost
        assert record.cost_source == "platform_pricing_table"
    finally:
        async with factory() as session:
            from sqlalchemy import delete

            from agent_platform.infrastructure.database.repositories.model_usage import (
                ModelUsageRow,
            )

            await session.execute(
                delete(ModelUsageRow).where(ModelUsageRow.tenant_id == tenant_id)
            )
            await session.execute(
                delete(TenantRecord).where(TenantRecord.id == tenant_id)
            )
            await session.commit()
        await engine.dispose()
