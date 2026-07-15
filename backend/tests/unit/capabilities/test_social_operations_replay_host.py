from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_platform.capabilities.mock_host import MockCapabilityHost
from agent_platform.capabilities.social_operations.manifest import (
    SOCIAL_OPERATIONS_MANIFEST,
)
from agent_platform.capabilities.social_operations.mock_replay_host import (
    ControlEventReplayError,
    MockSocialOperationsReplayHost,
)

REPOSITORY_ROOT = Path(__file__).parents[4]
PROTOCOL_ROOT = (
    REPOSITORY_ROOT / "contracts/fixtures/capabilities/social-operations/local-executor-v1"
)
CORE_PROTOCOLS = {
    "core.approvals": "1.0",
    "core.artifacts": "1.0",
    "core.audit": "1.0",
    "core.capability-host": "1.0",
    "core.events": "1.0",
    "core.permissions": "1.0",
    "core.runs": "1.0",
}


def _payload(fixture_name: str) -> dict[str, object]:
    payload = json.loads((PROTOCOL_ROOT / "valid" / fixture_name).read_text())
    assert isinstance(payload, dict)
    return payload


def _enabled_replay_host() -> MockSocialOperationsReplayHost:
    capability_host = MockCapabilityHost(core_protocols=CORE_PROTOCOLS)
    capability_host.install(SOCIAL_OPERATIONS_MANIFEST)
    return MockSocialOperationsReplayHost(capability_host=capability_host)


def _valid_replay() -> list[dict[str, object]]:
    return [
        _payload("step-started.json"),
        _payload("diagnostic-event.json"),
        _payload("step-waiting-for-human.json"),
        _payload("handoff-requested.json"),
        _payload("step-completed.json"),
    ]


def test_mock_host_replays_a_strict_control_event_sequence() -> None:
    events = _enabled_replay_host().replay(_valid_replay())

    assert [event.event_sequence for event in events] == [1, 2, 3, 4, 5]
    assert events[-1].message_type == "step.progress"
    assert events[-1].status == "completed"


def test_mock_host_requires_the_capability_to_be_installed_and_enabled() -> None:
    capability_host = MockCapabilityHost(core_protocols=CORE_PROTOCOLS)
    replay_host = MockSocialOperationsReplayHost(capability_host=capability_host)

    with pytest.raises(ControlEventReplayError, match="installed and enabled"):
        replay_host.replay([_payload("step-started.json")])

    capability_host.install(SOCIAL_OPERATIONS_MANIFEST)
    capability_host.disable("social-operations")
    with pytest.raises(ControlEventReplayError, match="installed and enabled"):
        replay_host.replay([_payload("step-started.json")])


def test_mock_host_rejects_sequence_gaps() -> None:
    replay = _valid_replay()
    replay[1]["event_sequence"] = 3

    with pytest.raises(ControlEventReplayError, match="event sequence"):
        _enabled_replay_host().replay(replay)


def test_mock_host_rejects_identity_changes() -> None:
    replay = _valid_replay()
    changed_identity = dict(replay[1]["identity"])  # type: ignore[arg-type]
    changed_identity["tenant_id"] = "00000000-0000-4000-8000-000000000999"
    replay[1]["identity"] = changed_identity

    with pytest.raises(ControlEventReplayError, match="identity"):
        _enabled_replay_host().replay(replay)


def test_mock_host_rejects_duplicate_message_ids() -> None:
    replay = _valid_replay()
    replay[1]["message_id"] = replay[0]["message_id"]

    with pytest.raises(ControlEventReplayError, match="duplicate message_id"):
        _enabled_replay_host().replay(replay)


@pytest.mark.parametrize(
    ("field_name", "field_value"),
    [
        ("audit_correlation_id", "00000000-0000-4000-8000-000000000999"),
        ("approval_id", None),
    ],
)
def test_mock_host_rejects_governance_changes(
    field_name: str,
    field_value: object,
) -> None:
    replay = _valid_replay()
    changed_governance = dict(replay[1]["governance"])  # type: ignore[arg-type]
    changed_governance[field_name] = field_value
    replay[1]["governance"] = changed_governance

    with pytest.raises(ControlEventReplayError, match="governance"):
        _enabled_replay_host().replay(replay)


def test_mock_host_rejects_executor_changes() -> None:
    replay = _valid_replay()
    replay[1]["executor_id"] = "00000000-0000-4000-8000-000000000999"

    with pytest.raises(ControlEventReplayError, match="executor"):
        _enabled_replay_host().replay(replay)


def test_mock_host_rejects_out_of_order_timestamps() -> None:
    replay = _valid_replay()
    replay[1]["sent_at"] = "2026-07-15T04:59:59Z"

    with pytest.raises(ControlEventReplayError, match="sent_at"):
        _enabled_replay_host().replay(replay)


def test_mock_host_rejects_invalid_step_transition() -> None:
    replay = _valid_replay()
    replay[2]["status"] = "started"
    replay[2]["progress_percent"] = 0

    with pytest.raises(ControlEventReplayError, match="step transition"):
        _enabled_replay_host().replay(replay)


def test_mock_host_rejects_step_code_changes_for_a_stable_step_id() -> None:
    replay = _valid_replay()
    replay[2]["step_code"] = "social.publish.changed_step"

    with pytest.raises(ControlEventReplayError, match="step_code"):
        _enabled_replay_host().replay(replay)


def test_mock_host_rejects_regressing_progress() -> None:
    start = _payload("step-started.json")
    progress = _payload("step-waiting-for-human.json")
    progress["event_sequence"] = 2
    progress["status"] = "in_progress"
    progress["progress_percent"] = 20
    regressed = dict(progress)
    regressed["message_id"] = "00000000-0000-4000-8000-000000000298"
    regressed["event_sequence"] = 3
    regressed["progress_percent"] = 10

    with pytest.raises(ControlEventReplayError, match="progress regressed"):
        _enabled_replay_host().replay([start, progress, regressed])


def test_mock_host_requires_waiting_step_before_handoff() -> None:
    replay = _valid_replay()
    replay[2]["status"] = "in_progress"

    with pytest.raises(ControlEventReplayError, match="waiting_for_human"):
        _enabled_replay_host().replay(replay)


@pytest.mark.parametrize(
    ("fixture_index", "id_field"),
    [(3, "handoff_id"), (1, "diagnostic_id")],
)
def test_mock_host_rejects_reused_stable_event_ids(
    fixture_index: int,
    id_field: str,
) -> None:
    replay = _valid_replay()
    duplicate = dict(replay[fixture_index])
    duplicate["message_id"] = "00000000-0000-4000-8000-000000000299"
    duplicate["event_sequence"] = 6
    duplicate["sent_at"] = "2026-07-15T05:00:05Z"
    replay.append(duplicate)

    with pytest.raises(ControlEventReplayError, match=f"duplicate {id_field}"):
        _enabled_replay_host().replay(replay)


def test_mock_host_rejects_diagnostic_for_unknown_step() -> None:
    replay = _valid_replay()
    replay[1]["step_id"] = "00000000-0000-4000-8000-000000000999"

    with pytest.raises(ControlEventReplayError, match="unknown step"):
        _enabled_replay_host().replay(replay)


def test_mock_host_sanitizes_invalid_payload_errors() -> None:
    payload = _payload("step-started.json")
    payload["extensions"] = {"social.diagnostic.note": "cookie=session-secret"}

    with pytest.raises(
        ControlEventReplayError,
        match="^invalid control event payload$",
    ) as captured:
        _enabled_replay_host().replay([payload])

    assert "session-secret" not in str(captured.value)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
