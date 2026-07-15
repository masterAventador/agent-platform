from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Final
from uuid import UUID

from pydantic import ValidationError

from agent_platform.capabilities.mock_host import MockCapabilityHost
from agent_platform.capabilities.social_operations.local_executor_protocol import (
    LocalControlEvent,
    LocalDiagnosticEvent,
    LocalExecutorMessage,
    LocalHandoffRequested,
    LocalStepProgress,
)

_CAPABILITY_ID: Final = "social-operations"
_STEP_TRANSITIONS: Final = {
    "started": frozenset({"in_progress", "waiting_for_human", "completed", "failed", "cancelled"}),
    "in_progress": frozenset(
        {"in_progress", "waiting_for_human", "completed", "failed", "cancelled"}
    ),
    "waiting_for_human": frozenset({"in_progress", "completed", "failed", "cancelled"}),
    "completed": frozenset(),
    "failed": frozenset(),
    "cancelled": frozenset(),
}


class ControlEventReplayError(ValueError):
    """A fixture stream violates the local executor control-event contract."""


@dataclass(frozen=True, slots=True)
class _StepReplayState:
    step_code: str
    status: str
    progress_percent: int


class MockSocialOperationsReplayHost:
    """In-memory protocol replay substitute; it never executes production work."""

    def __init__(self, *, capability_host: MockCapabilityHost) -> None:
        self._capability_host = capability_host

    def replay(
        self,
        payloads: Iterable[Mapping[str, Any]],
    ) -> tuple[LocalControlEvent, ...]:
        self._require_capability()
        events = tuple(self._parse_control_event(payload) for payload in payloads)
        self._validate_replay(events)
        return events

    def _require_capability(self) -> None:
        installed = self._capability_host.installed_capability_ids
        enabled = self._capability_host.enabled_capability_ids
        if _CAPABILITY_ID not in installed or _CAPABILITY_ID not in enabled:
            raise ControlEventReplayError(
                "social-operations must be installed and enabled for Mock Host replay"
            )

    @staticmethod
    def _parse_control_event(payload: Mapping[str, Any]) -> LocalControlEvent:
        message = MockSocialOperationsReplayHost._try_parse_message(payload)
        if message is None:
            raise ControlEventReplayError("invalid control event payload")
        if not isinstance(
            message,
            LocalStepProgress | LocalHandoffRequested | LocalDiagnosticEvent,
        ):
            raise ControlEventReplayError("Mock Host replay accepts control events only")
        return message

    @staticmethod
    def _try_parse_message(payload: Mapping[str, Any]) -> object | None:
        try:
            return LocalExecutorMessage.model_validate(dict(payload)).root
        except ValidationError:
            return None

    @staticmethod
    def _validate_replay(events: tuple[LocalControlEvent, ...]) -> None:
        if not events:
            return

        identity = events[0].identity
        governance = events[0].governance
        executor_id = events[0].executor_id
        previous_sent_at = events[0].sent_at
        message_ids: set[UUID] = set()
        steps: dict[UUID, _StepReplayState] = {}
        handoff_ids: set[UUID] = set()
        diagnostic_ids: set[UUID] = set()

        for expected_sequence, event in enumerate(events, start=1):
            if event.event_sequence != expected_sequence:
                raise ControlEventReplayError("event sequence must start at 1 and be contiguous")
            if event.identity != identity:
                raise ControlEventReplayError("control event identity changed during replay")
            if event.governance != governance:
                raise ControlEventReplayError("control event governance changed during replay")
            if event.executor_id != executor_id:
                raise ControlEventReplayError("control event executor changed during replay")
            if event.sent_at < previous_sent_at:
                raise ControlEventReplayError("control event sent_at must be nondecreasing")
            previous_sent_at = event.sent_at
            if event.message_id in message_ids:
                raise ControlEventReplayError("duplicate message_id")
            message_ids.add(event.message_id)

            if isinstance(event, LocalStepProgress):
                MockSocialOperationsReplayHost._apply_step_event(steps, event)
            elif isinstance(event, LocalHandoffRequested):
                if event.handoff_id in handoff_ids:
                    raise ControlEventReplayError("duplicate handoff_id")
                handoff_ids.add(event.handoff_id)
                step = steps.get(event.step_id)
                if step is None or step.status != "waiting_for_human":
                    raise ControlEventReplayError(
                        "handoff requires a known step in waiting_for_human status"
                    )
            else:
                if event.diagnostic_id in diagnostic_ids:
                    raise ControlEventReplayError("duplicate diagnostic_id")
                diagnostic_ids.add(event.diagnostic_id)
                if event.step_id is not None and event.step_id not in steps:
                    raise ControlEventReplayError("diagnostic references an unknown step")

    @staticmethod
    def _apply_step_event(
        steps: dict[UUID, _StepReplayState],
        event: LocalStepProgress,
    ) -> None:
        current = steps.get(event.step_id)
        if current is None:
            if event.status != "started":
                raise ControlEventReplayError("the first step transition must be started")
        else:
            if event.step_code != current.step_code:
                raise ControlEventReplayError("step_code changed for a stable step_id")
            if event.status not in _STEP_TRANSITIONS[current.status]:
                raise ControlEventReplayError("invalid step transition")
            if event.progress_percent < current.progress_percent:
                raise ControlEventReplayError("step progress regressed")
        steps[event.step_id] = _StepReplayState(
            step_code=event.step_code,
            status=event.status,
            progress_percent=event.progress_percent,
        )
