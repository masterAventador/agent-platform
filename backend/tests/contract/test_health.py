import pytest
from httpx import ASGITransport, AsyncClient

from agent_platform.api.app import _wait_for_database_ready, create_app


@pytest.mark.asyncio
async def test_liveness_endpoint_reports_service_is_running() -> None:
    transport = ASGITransport(app=create_app())

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_artifact_reconciliation_waits_for_schema_without_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    sleeps: list[float] = []

    class FlakySession:
        async def __aenter__(self) -> "FlakySession":
            return self

        async def __aexit__(self, *_exc: object) -> None:
            return None

        async def execute(self, _statement: object) -> None:
            nonlocal calls
            calls += 1
            if calls < 3:
                raise RuntimeError("artifact schema not ready")

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("agent_platform.api.app.asyncio.sleep", fake_sleep)

    await _wait_for_database_ready(lambda: FlakySession(), retry_delay_seconds=0.25)

    assert calls == 3
    assert sleeps == [0.25, 0.25]
