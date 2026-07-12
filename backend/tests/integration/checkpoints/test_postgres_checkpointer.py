import os
from typing import TypedDict
from uuid import uuid4

import pytest
from langgraph.graph import END, START, StateGraph

from agent_platform.infrastructure.checkpoints.postgres import postgres_checkpointer


class CounterState(TypedDict):
    count: int


def increment(state: CounterState) -> CounterState:
    return {"count": state["count"] + 1}


@pytest.mark.asyncio
async def test_postgres_checkpointer_restores_thread_across_instances() -> None:
    database_url = os.getenv("TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("需要 TEST_DATABASE_URL 才运行真实 PostgreSQL Checkpointer 测试")
    checkpoint_url = database_url.replace("postgresql+asyncpg://", "postgresql://")
    thread_id = f"checkpoint-{uuid4()}"
    config = {"configurable": {"thread_id": thread_id}}

    builder = StateGraph(CounterState)
    builder.add_node("increment", increment)
    builder.add_edge(START, "increment")
    builder.add_edge("increment", END)

    async with postgres_checkpointer(checkpoint_url) as checkpointer:
        await checkpointer.setup()
        graph = builder.compile(checkpointer=checkpointer)
        assert (await graph.ainvoke({"count": 0}, config))["count"] == 1

    async with postgres_checkpointer(checkpoint_url) as restored_checkpointer:
        restored_graph = builder.compile(checkpointer=restored_checkpointer)
        snapshot = await restored_graph.aget_state(config)
        assert snapshot.values["count"] == 1
