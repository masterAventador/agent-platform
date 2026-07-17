"""`_wait_for_database_ready` 的等待日志必须报出调用方，而不是张冠李戴。"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest

from agent_platform.api.app import _wait_for_database_ready


class _FailOnceSession:
    async def execute(self, _statement: object) -> None:
        raise RuntimeError("relation does not exist yet")


class _OkSession:
    async def execute(self, _statement: object) -> None:
        return None


@pytest.mark.asyncio
async def test_waiting_log_is_namespaced_by_the_caller(
    caplog: pytest.LogCaptureFixture,
) -> None:
    attempts = {"count": 0}

    @asynccontextmanager
    async def session_factory() -> AsyncIterator[object]:
        attempts["count"] += 1
        yield _FailOnceSession() if attempts["count"] == 1 else _OkSession()

    with caplog.at_level(logging.INFO, logger="agent_platform.api.app"):
        await _wait_for_database_ready(
            session_factory, log_scope="scheduler", retry_delay_seconds=0
        )

    messages = [record.message for record in caplog.records]
    # 调度器等待 schema 时不得打成 artifact 存储协调器的日志名——排查会指错方向。
    assert "scheduler_waiting_for_schema" in messages
    assert "scheduler_database_ready" in messages
    assert not any("artifact_storage_reconciliation" in message for message in messages)
