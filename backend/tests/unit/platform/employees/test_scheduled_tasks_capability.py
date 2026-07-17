"""C12 解除 `scheduled_tasks` 的强制关闭，同时保持已发布版本的历史语义不变。"""

from __future__ import annotations

from agent_platform.platform.employees.entities import is_runnable_employee_definition


def definition(*, scheduled_tasks: object) -> dict[str, object]:
    return {
        "work_mode": "autonomous",
        "capabilities": {
            "conversation": True,
            "scheduled_tasks": scheduled_tasks,
            "file_upload": False,
        },
    }


def test_an_employee_with_scheduled_tasks_enabled_is_runnable() -> None:
    # C12 之前这里被强制判为不可运行；定时能力落地后必须放行。
    assert is_runnable_employee_definition(definition(scheduled_tasks=True)) is True


def test_historical_published_versions_keep_their_original_meaning() -> None:
    # 既有已发布版本全部是 scheduled_tasks=False，其解释不能被新逻辑改写。
    assert is_runnable_employee_definition(definition(scheduled_tasks=False)) is True


def test_a_non_boolean_scheduled_tasks_flag_is_still_rejected() -> None:
    for value in ["true", 1, None, {}]:
        assert is_runnable_employee_definition(definition(scheduled_tasks=value)) is False


def test_a_definition_without_the_capability_flag_is_rejected() -> None:
    assert (
        is_runnable_employee_definition(
            {
                "work_mode": "autonomous",
                "capabilities": {"conversation": True, "file_upload": False},
            }
        )
        is False
    )
