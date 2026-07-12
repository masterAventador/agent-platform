import json
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from agent_platform.platform.runs.events import EventType, PlatformEvent

REPOSITORY_ROOT = Path(__file__).parents[4]


def test_platform_event_has_stable_versioned_envelope() -> None:
    event = PlatformEvent.create(
        tenant_id=uuid4(),
        employee_id=uuid4(),
        run_id=uuid4(),
        sequence=1,
        event_type=EventType.RUN_STARTED,
        payload={"status": "running"},
    )

    payload = event.model_dump(mode="json")
    assert payload["event_version"] == "1.0"
    assert payload["type"] == "run.started"
    assert payload["sequence"] == 1
    assert payload["occurred_at"].endswith("Z")
    assert {"tenant_id", "employee_id", "run_id"} <= payload.keys()


def test_platform_event_rejects_non_json_payload() -> None:
    with pytest.raises(ValidationError):
        PlatformEvent.create(
            tenant_id=uuid4(),
            employee_id=uuid4(),
            run_id=uuid4(),
            sequence=1,
            event_type=EventType.RUN_PROGRESS,
            payload={"private_state": object()},
        )


def test_exported_event_schema_matches_backend_model() -> None:
    schema_path = REPOSITORY_ROOT / "contracts/events/platform-event.schema.json"

    assert json.loads(schema_path.read_text()) == PlatformEvent.model_json_schema()
