from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import ValidationError

from agent_platform.capabilities.social_operations.local_executor_protocol import (
    LocalDiagnosticEvent,
    LocalExecutorMessage,
    LocalHandoffRequested,
    LocalStepProgress,
)

REPOSITORY_ROOT = Path(__file__).parents[4]
PROTOCOL_ROOT = (
    REPOSITORY_ROOT / "contracts/fixtures/capabilities/social-operations/local-executor-v1"
)


def _fixture_payload(kind: str, fixture_name: str) -> dict[str, object]:
    payload = json.loads((PROTOCOL_ROOT / kind / fixture_name).read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


@pytest.mark.parametrize(
    ("fixture_name", "message_type"),
    [
        ("step-started.json", LocalStepProgress),
        ("step-waiting-for-human.json", LocalStepProgress),
        ("handoff-requested.json", LocalHandoffRequested),
        ("diagnostic-event.json", LocalDiagnosticEvent),
    ],
)
def test_control_event_fixtures_round_trip(
    fixture_name: str,
    message_type: type[object],
) -> None:
    payload = _fixture_payload("valid", fixture_name)

    event = LocalExecutorMessage.model_validate(payload).root

    assert isinstance(event, message_type)
    assert event.protocol_version == "1.0"
    assert event.identity.capability_id == "social-operations"


@pytest.mark.parametrize(
    "fixture_name",
    [
        "control-event-cookie-assignment.json",
        "control-event-inline-data.json",
        "control-event-nested-extension.json",
        "control-event-unknown-field.json",
        "diagnostic-sensitive-field.json",
        "diagnostic-unsafe-message.json",
        "diagnostic-inline-artifact.json",
        "handoff-bypass-reason.json",
        "handoff-foreign-extension.json",
        "step-invalid-status.json",
        "step-missing-sequence.json",
        "step-started-nonzero.json",
        "step-completed-incomplete.json",
    ],
)
def test_invalid_control_event_fixtures_are_rejected(fixture_name: str) -> None:
    with pytest.raises(ValidationError):
        LocalExecutorMessage.model_validate(_fixture_payload("invalid", fixture_name))


@pytest.mark.parametrize(
    "fixture_name",
    [
        "step-started.json",
        "step-waiting-for-human.json",
        "handoff-requested.json",
        "diagnostic-event.json",
    ],
)
def test_standard_schema_accepts_control_event_fixtures(fixture_name: str) -> None:
    validator = Draft202012Validator(
        LocalExecutorMessage.model_json_schema(),
        format_checker=FormatChecker(),
    )

    assert list(validator.iter_errors(_fixture_payload("valid", fixture_name))) == []


@pytest.mark.parametrize(
    "fixture_name",
    [
        "control-event-nested-extension.json",
        "control-event-unknown-field.json",
        "diagnostic-inline-artifact.json",
        "handoff-bypass-reason.json",
        "handoff-foreign-extension.json",
        "step-invalid-status.json",
        "step-missing-sequence.json",
        "step-started-nonzero.json",
        "step-completed-incomplete.json",
    ],
)
def test_standard_schema_rejects_structural_control_event_failures(
    fixture_name: str,
) -> None:
    validator = Draft202012Validator(
        LocalExecutorMessage.model_json_schema(),
        format_checker=FormatChecker(),
    )

    assert list(validator.iter_errors(_fixture_payload("invalid", fixture_name)))


def test_sensitive_extension_is_an_explicit_semantic_failure() -> None:
    payload = _fixture_payload("invalid", "diagnostic-sensitive-field.json")
    schema = LocalExecutorMessage.model_json_schema()
    validator = Draft202012Validator(schema, format_checker=FormatChecker())

    assert list(validator.iter_errors(payload)) == []
    assert "extension keys must not name sensitive data" in schema["x-semantic-validation-required"]
    with pytest.raises(ValidationError, match="extension keys must not name sensitive data"):
        LocalExecutorMessage.model_validate(payload)


@pytest.mark.parametrize(
    "extension_key",
    [
        "social.diagnostic.file_path",
        "social.api_token",
        "social.api_file_path",
        "social.api_key",
        "social.private_key",
        "social.access_token",
        "social.refresh_token",
        "social.session_cookie",
        "social.result_object_key",
        "social.temp_signed_url",
    ],
)
def test_sensitive_extension_names_preserve_underscore_segments(
    extension_key: str,
) -> None:
    payload = _fixture_payload("valid", "diagnostic-event.json")
    payload["extensions"] = {extension_key: "redacted"}

    with pytest.raises(ValidationError, match="extension keys must not name sensitive data"):
        LocalExecutorMessage.model_validate(payload)


def test_unsafe_display_message_is_an_explicit_semantic_failure() -> None:
    payload = _fixture_payload("invalid", "diagnostic-unsafe-message.json")
    schema = LocalExecutorMessage.model_json_schema()
    validator = Draft202012Validator(schema, format_checker=FormatChecker())

    assert list(validator.iter_errors(payload)) == []
    assert (
        "safe_message must not contain credential or local path markers"
        in schema["x-semantic-validation-required"]
    )
    with pytest.raises(
        ValidationError,
        match="safe_message must not contain credential or local path markers",
    ):
        LocalExecutorMessage.model_validate(payload)


@pytest.mark.parametrize(
    ("status", "progress_percent"),
    [("started", 1), ("completed", 99)],
)
def test_step_status_requires_a_canonical_progress_boundary(
    status: str,
    progress_percent: int,
) -> None:
    payload = _fixture_payload("valid", "step-started.json")
    payload["status"] = status
    payload["progress_percent"] = progress_percent

    with pytest.raises(ValidationError, match="progress_percent"):
        LocalExecutorMessage.model_validate(payload)


@pytest.mark.parametrize(
    "fixture_name",
    ["step-started-nonzero.json", "step-completed-incomplete.json"],
)
def test_step_progress_boundaries_are_encoded_in_standard_schema(
    fixture_name: str,
) -> None:
    validator = Draft202012Validator(
        LocalExecutorMessage.model_json_schema(),
        format_checker=FormatChecker(),
    )

    assert list(validator.iter_errors(_fixture_payload("invalid", fixture_name)))


def test_control_event_nested_extensions_are_rejected_by_model_and_schema() -> None:
    payload = _fixture_payload("invalid", "control-event-nested-extension.json")
    validator = Draft202012Validator(
        LocalExecutorMessage.model_json_schema(),
        format_checker=FormatChecker(),
    )

    with pytest.raises(ValidationError):
        LocalExecutorMessage.model_validate(payload)
    assert list(validator.iter_errors(payload))


def test_control_event_string_extensions_use_safe_value_semantics() -> None:
    payload = _fixture_payload("valid", "diagnostic-event.json")
    payload["extensions"] = {"social.diagnostic.note": "/Users/example/private.log"}
    schema = LocalExecutorMessage.model_json_schema()

    assert (
        "control event string extensions must not contain credential or local path markers"
        in schema["x-semantic-validation-required"]
    )
    with pytest.raises(
        ValidationError,
        match=("control event string extensions must not contain credential or local path markers"),
    ):
        LocalExecutorMessage.model_validate(payload)


@pytest.mark.parametrize(
    "fixture_name",
    ["control-event-cookie-assignment.json", "control-event-inline-data.json"],
)
def test_control_event_unsafe_string_extensions_are_semantic_failures(
    fixture_name: str,
) -> None:
    payload = _fixture_payload("invalid", fixture_name)
    schema = LocalExecutorMessage.model_json_schema()
    validator = Draft202012Validator(schema, format_checker=FormatChecker())

    assert list(validator.iter_errors(payload)) == []
    with pytest.raises(
        ValidationError,
        match=("control event string extensions must not contain credential or local path markers"),
    ):
        LocalExecutorMessage.model_validate(payload)


def test_control_event_safe_string_extensions_do_not_reject_ordinary_words() -> None:
    payload = _fixture_payload("valid", "diagnostic-event.json")
    safe_values = (
        "Cookie policy and base64 encoding are unavailable.",
        "Cookie: disabled by administrator.",
        "The token: unavailable.",
        "token: string",
        "Required schema fields are token: string and retryable: boolean.",
        "Status {ready}; no structured payload attached.",
        "https://example.test/api/v1/users/current",
        "/api/v1/users/current",
        "true",
        "42",
        '"ordinary text"',
    )

    for value in safe_values:
        payload["safe_message"] = value
        payload["extensions"] = {"social.diagnostic.note": value}
        event = LocalExecutorMessage.model_validate(payload).root
        assert event.safe_message == value
        assert event.extensions == payload["extensions"]


@pytest.mark.parametrize(
    "unsafe_value",
    [
        "https://example.test/status?api_key=session-secret",
        "private_key=session-secret",
        "access_token=session-secret",
        "refresh_token=session-secret",
        "session_cookie=session-secret",
        "api_token=session-secret",
    ],
)
def test_control_event_extensions_reject_sensitive_field_families(
    unsafe_value: str,
) -> None:
    payload = _fixture_payload("valid", "diagnostic-event.json")
    payload["extensions"] = {"social.diagnostic.note": unsafe_value}

    with pytest.raises(
        ValidationError,
        match=("control event string extensions must not contain credential or local path markers"),
    ):
        LocalExecutorMessage.model_validate(payload)


@pytest.mark.parametrize(
    "private_path",
    [
        "file:///Users/example/private.log",
        "/home/example/private.log",
        "/root/.config/private",
        "/tmp/private.log",
        "/var/folders/ab/private.log",
        "C:\\Users\\example\\private.log",
        "D:/private/private.log",
    ],
)
def test_control_event_extensions_reject_private_local_paths(
    private_path: str,
) -> None:
    payload = _fixture_payload("valid", "diagnostic-event.json")
    payload["extensions"] = {"social.diagnostic.note": private_path}

    with pytest.raises(
        ValidationError,
        match=("control event string extensions must not contain credential or local path markers"),
    ):
        LocalExecutorMessage.model_validate(payload)


@pytest.mark.parametrize(
    "encoded_container",
    [
        '{"token":"session-secret"}',
        '{"api_token":"session-secret"}',
        '{"session_cookie":"session-secret"}',
    ],
)
def test_control_event_extensions_reject_json_encoded_containers(
    encoded_container: str,
) -> None:
    payload = _fixture_payload("valid", "diagnostic-event.json")
    payload["extensions"] = {"social.diagnostic.note": encoded_container}

    with pytest.raises(
        ValidationError,
        match=("control event string extensions must not contain credential or local path markers"),
    ):
        LocalExecutorMessage.model_validate(payload)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_control_event_extensions_reject_non_finite_floats(value: float) -> None:
    payload = _fixture_payload("valid", "diagnostic-event.json")
    payload["extensions"] = {"social.diagnostic.confidence": value}

    with pytest.raises(ValidationError):
        LocalExecutorMessage.model_validate(payload)


def test_control_event_json_rejects_overflowing_float() -> None:
    payload = _fixture_payload("valid", "diagnostic-event.json")
    payload["extensions"] = {"social.diagnostic.confidence": "__OVERFLOW__"}
    wire = json.dumps(payload).replace('"__OVERFLOW__"', "1e309")

    with pytest.raises(ValidationError):
        LocalExecutorMessage.model_validate_json(wire)


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("event_sequence", True),
        ("event_sequence", "1"),
        ("progress_percent", True),
        ("progress_percent", "0"),
    ],
)
def test_control_event_integer_fields_are_strict(
    field_name: str,
    value: object,
) -> None:
    payload = _fixture_payload("valid", "step-started.json")
    payload[field_name] = value

    with pytest.raises(ValidationError):
        LocalExecutorMessage.model_validate(payload)


def test_task_request_extensions_keep_existing_json_value_compatibility() -> None:
    payload = _fixture_payload("valid", "task-request.json")
    payload["extensions"] = {"social.request.options": {"nested": True}}
    validator = Draft202012Validator(
        LocalExecutorMessage.model_json_schema(),
        format_checker=FormatChecker(),
    )

    assert LocalExecutorMessage.model_validate(payload).root.message_type == "task.request"
    assert list(validator.iter_errors(payload)) == []


def test_control_event_artifacts_remain_core_references_only() -> None:
    for event_type in (LocalStepProgress, LocalHandoffRequested, LocalDiagnosticEvent):
        annotation = event_type.model_fields["artifact_refs"].annotation
        assert "LocalArtifactReference" in str(annotation)
