from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import ValidationError

from agent_platform.capabilities.social_operations.local_executor_protocol import (
    LOCAL_EXECUTOR_PROTOCOL_VERSION,
    LocalArtifactReference,
    LocalExecutorMessage,
    LocalTaskCancel,
    LocalTaskError,
    LocalTaskRequest,
    LocalTaskResponse,
)

REPOSITORY_ROOT = Path(__file__).parents[4]
PROTOCOL_ROOT = (
    REPOSITORY_ROOT / "contracts/fixtures/capabilities/social-operations/local-executor-v1"
)
SCHEMA_PATH = (
    REPOSITORY_ROOT / "contracts/capabilities/social-operations/local-executor-v1.schema.json"
)
PROTOCOL_DOC_PATH = (
    REPOSITORY_ROOT / "contracts/capabilities/social-operations/local-executor-v1.md"
)
SEMANTIC_ONLY_INVALID_FIXTURES = frozenset(
    {
        "control-event-cookie-assignment.json",
        "control-event-inline-data.json",
        "deadline-before-send.json",
        "diagnostic-sensitive-field.json",
        "diagnostic-unsafe-message.json",
    }
)


def _fixture_payload(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


@pytest.mark.parametrize(
    ("fixture_name", "message_type"),
    [
        ("task-request.json", LocalTaskRequest),
        ("task-cancel.json", LocalTaskCancel),
        ("task-accepted.json", LocalTaskResponse),
        ("task-completed.json", LocalTaskResponse),
        ("task-error.json", LocalTaskError),
    ],
)
def test_valid_fixtures_round_trip_as_the_expected_message_type(
    fixture_name: str,
    message_type: type[object],
) -> None:
    payload = _fixture_payload(PROTOCOL_ROOT / "valid" / fixture_name)

    message = LocalExecutorMessage.model_validate(payload).root

    assert isinstance(message, message_type)
    assert message.protocol_version == LOCAL_EXECUTOR_PROTOCOL_VERSION
    assert message.identity.capability_id == "social-operations"
    assert message.governance.audit_correlation_id
    assert (
        LocalExecutorMessage.model_validate_json(
            LocalExecutorMessage(root=message).model_dump_json()
        ).root
        == message
    )


def test_task_request_carries_stable_identity_idempotency_deadline_and_references() -> None:
    request = LocalExecutorMessage.model_validate(
        _fixture_payload(PROTOCOL_ROOT / "valid/task-request.json")
    ).root

    assert isinstance(request, LocalTaskRequest)
    assert request.task_type == "social.publish.video"
    assert request.idempotency_key == "social-task:00000000-0000-4000-8000-000000000101:attempt:1"
    assert request.deadline_at.tzinfo is not None
    assert request.governance.approval_id is not None
    assert request.artifact_refs == (
        LocalArtifactReference(
            artifact_id="00000000-0000-4000-8000-000000000106",
            usage="input",
        ),
    )
    assert request.extensions == {"social.protocol.test_marker": "fixture"}


def test_cancel_is_a_separate_idempotent_control_message() -> None:
    cancel = LocalExecutorMessage.model_validate(
        _fixture_payload(PROTOCOL_ROOT / "valid/task-cancel.json")
    ).root

    assert isinstance(cancel, LocalTaskCancel)
    assert cancel.idempotency_key.endswith(":cancel:1")
    assert cancel.reason_code == "emergency_stop"


def test_response_and_error_are_mutually_distinguishable_terminal_outcomes() -> None:
    completed = LocalExecutorMessage.model_validate(
        _fixture_payload(PROTOCOL_ROOT / "valid/task-completed.json")
    ).root
    error = LocalExecutorMessage.model_validate(
        _fixture_payload(PROTOCOL_ROOT / "valid/task-error.json")
    ).root

    assert isinstance(completed, LocalTaskResponse)
    assert completed.status == "completed"
    assert completed.artifact_refs[0].usage == "output"
    assert isinstance(error, LocalTaskError)
    assert error.category == "execution_failed"
    assert error.retryable is False
    assert error.retry_after_ms is None


@pytest.mark.parametrize(
    "fixture_path",
    sorted((PROTOCOL_ROOT / "invalid").glob("*.json")),
    ids=lambda path: path.name,
)
def test_invalid_fixtures_are_rejected(fixture_path: Path) -> None:
    with pytest.raises(ValidationError):
        LocalExecutorMessage.model_validate(_fixture_payload(fixture_path))


def test_exported_schema_matches_the_backend_protocol_source() -> None:
    assert json.loads(SCHEMA_PATH.read_text(encoding="utf-8")) == (
        LocalExecutorMessage.model_json_schema()
    )


def test_exported_schema_is_valid_draft_2020_12() -> None:
    schema = LocalExecutorMessage.model_json_schema()

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    Draft202012Validator.check_schema(schema)


def test_protocol_version_must_be_explicit_in_every_wire_message() -> None:
    schema = LocalExecutorMessage.model_json_schema()

    for message_name in (
        "LocalTaskRequest",
        "LocalTaskCancel",
        "LocalTaskResponse",
        "LocalTaskError",
        "LocalStepProgress",
        "LocalHandoffRequested",
        "LocalDiagnosticEvent",
    ):
        version_schema = schema["$defs"][message_name]["properties"]["protocol_version"]
        assert "default" not in version_schema
        assert "protocol_version" in schema["$defs"][message_name]["required"]


@pytest.mark.parametrize(
    "fixture_path",
    sorted((PROTOCOL_ROOT / "valid").glob("*.json")),
    ids=lambda path: path.name,
)
def test_standard_json_schema_accepts_valid_fixtures(fixture_path: Path) -> None:
    validator = Draft202012Validator(
        LocalExecutorMessage.model_json_schema(),
        format_checker=FormatChecker(),
    )

    assert list(validator.iter_errors(_fixture_payload(fixture_path))) == []


@pytest.mark.parametrize(
    "fixture_path",
    [
        path
        for path in sorted((PROTOCOL_ROOT / "invalid").glob("*.json"))
        if path.name not in SEMANTIC_ONLY_INVALID_FIXTURES
    ],
    ids=lambda path: path.name,
)
def test_standard_json_schema_rejects_structural_invalid_fixtures(
    fixture_path: Path,
) -> None:
    validator = Draft202012Validator(
        LocalExecutorMessage.model_json_schema(),
        format_checker=FormatChecker(),
    )

    assert list(validator.iter_errors(_fixture_payload(fixture_path)))


def test_protocol_document_lists_the_exact_fixture_validation_layers() -> None:
    document = PROTOCOL_DOC_PATH.read_text(encoding="utf-8")
    invalid_fixtures = frozenset(path.name for path in (PROTOCOL_ROOT / "invalid").glob("*.json"))

    assert len(invalid_fixtures) == 25
    assert len(SEMANTIC_ONLY_INVALID_FIXTURES) == 5
    assert invalid_fixtures > SEMANTIC_ONLY_INVALID_FIXTURES
    for fixture_name in SEMANTIC_ONLY_INVALID_FIXTURES:
        assert f"`{fixture_name}`" in document
    assert "5 个语义层无效样例" in document
    assert "其余 20 个无效样例" in document


def test_deadline_order_is_an_explicit_post_schema_semantic_validation() -> None:
    payload = _fixture_payload(PROTOCOL_ROOT / "invalid/deadline-before-send.json")
    schema = LocalExecutorMessage.model_json_schema()
    validator = Draft202012Validator(schema, format_checker=FormatChecker())

    assert list(validator.iter_errors(payload)) == []
    assert schema["$defs"]["LocalTaskRequest"]["x-semantic-validation-required"] == [
        "deadline_at must be later than sent_at"
    ]
    with pytest.raises(ValidationError, match="deadline_at must be later than sent_at"):
        LocalExecutorMessage.model_validate(payload)


def test_non_retryable_error_allows_retry_delay_to_be_null_or_absent() -> None:
    payload = _fixture_payload(PROTOCOL_ROOT / "valid/task-error.json")
    payload.pop("retry_after_ms")
    schema = LocalExecutorMessage.model_json_schema()
    validator = Draft202012Validator(schema, format_checker=FormatChecker())

    assert list(validator.iter_errors(payload)) == []
    error = LocalExecutorMessage.model_validate(payload).root
    assert isinstance(error, LocalTaskError)
    assert error.retry_after_ms is None


def test_schema_rejects_unknown_fields_and_reserves_an_explicit_extension_bag() -> None:
    schema = LocalExecutorMessage.model_json_schema()

    assert schema["$defs"]["LocalTaskRequest"]["additionalProperties"] is False
    assert schema["$defs"]["LocalTaskIdentity"]["additionalProperties"] is False
    assert schema["$defs"]["LocalTaskRequest"]["properties"]["protocol_version"]["const"] == (
        LOCAL_EXECUTOR_PROTOCOL_VERSION
    )
    extensions_schema = schema["$defs"]["LocalTaskRequest"]["properties"]["extensions"]
    assert extensions_schema["additionalProperties"] is False
    assert set(extensions_schema["patternProperties"]) == {
        r"^social\.[a-z0-9_]+(?:[.-][a-z0-9_]+)*$"
    }


def test_artifact_contract_is_a_reference_not_a_copy_of_the_core_artifact_domain() -> None:
    assert set(LocalArtifactReference.model_fields) == {"artifact_id", "usage"}
