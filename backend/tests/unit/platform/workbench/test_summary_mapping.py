from dataclasses import fields

from agent_platform.infrastructure.database.repositories.workbench import (
    _map_run_status_counts,
)
from agent_platform.platform.runs.entities import RunStatus
from agent_platform.platform.workbench.models import RunCounts


def test_run_status_row_is_mapped_by_enum_key_instead_of_positional_index() -> None:
    status_values = tuple(range(11, 11 + len(RunStatus)))

    mapped = _map_run_status_counts(status_values)

    assert mapped == dict(zip(RunStatus, status_values, strict=True))


def test_run_counts_model_explicitly_covers_every_runtime_status() -> None:
    run_count_fields = tuple(field.name for field in fields(RunCounts) if field.name != "total")

    assert run_count_fields == tuple(status.value for status in RunStatus)
