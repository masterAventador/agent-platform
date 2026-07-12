from uuid import uuid4

import pytest

from agent_platform.platform.runs.entities import Run, RunStatus
from agent_platform.platform.runs.errors import InvalidRunTransition


def test_run_can_wait_resume_and_complete() -> None:
    run = Run.create(
        tenant_id=uuid4(),
        employee_id=uuid4(),
        employee_version=2,
        created_by=uuid4(),
        input_data={"topic": "AI Agent"},
    )

    assert run.status is RunStatus.QUEUED
    assert run.thread_id == str(run.id)

    run = run.transition_to(RunStatus.RUNNING)
    assert run.started_at is not None
    run = run.transition_to(RunStatus.WAITING_FOR_APPROVAL)
    run = run.transition_to(RunStatus.RUNNING)
    run = run.transition_to(RunStatus.COMPLETED)

    assert run.status is RunStatus.COMPLETED
    assert run.finished_at is not None


def test_terminal_run_cannot_transition_again() -> None:
    run = Run.create(
        tenant_id=uuid4(),
        employee_id=uuid4(),
        employee_version=1,
        created_by=uuid4(),
        input_data={},
    ).transition_to(RunStatus.CANCELLED)

    with pytest.raises(InvalidRunTransition):
        run.transition_to(RunStatus.RUNNING)
