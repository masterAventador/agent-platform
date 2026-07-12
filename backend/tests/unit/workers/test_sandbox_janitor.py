from __future__ import annotations

import asyncio

import pytest

from agent_platform.workers.sandbox_janitor import serve_janitor


class RecordingJanitorManager:
    def __init__(self, stop_event: asyncio.Event) -> None:
        self.stop_event = stop_event
        self.limits: list[int] = []

    async def cleanup_expired(self, *, limit: int = 100):
        self.limits.append(limit)
        self.stop_event.set()
        return []


@pytest.mark.asyncio
async def test_janitor_cleans_once_marks_ready_and_removes_ready_file(tmp_path) -> None:
    stop_event = asyncio.Event()
    manager = RecordingJanitorManager(stop_event)
    ready_file = tmp_path / "janitor-ready"

    await serve_janitor(
        manager=manager,
        stop_event=stop_event,
        interval_seconds=1,
        batch_size=17,
        ready_file=ready_file,
    )

    assert manager.limits == [17]
    assert not ready_file.exists()
