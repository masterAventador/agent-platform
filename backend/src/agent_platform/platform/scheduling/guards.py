"""C12 每次调度前重新校验的守卫规则。

创建定时任务时通过校验不代表现在还能跑：员工可能已下线/删除、发布版本可能关掉了
定时能力或改了输入 Schema、创建者可能已被移出企业或降权。这些都必须在**每次**
派发前重新判定，且一律 fail-closed——查不到就是不给跑，绝不放行。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from agent_platform.platform.dynamic_io import (
    DynamicInputTooLarge,
    DynamicInputValidationFailed,
    InvalidDynamicSchema,
    validate_run_input,
)
from agent_platform.platform.employees.entities import is_runnable_employee_definition
from agent_platform.platform.scheduling.entities import (
    ScheduledTask,
    SkipReason,
    is_scheduling_enabled,
)
from agent_platform.platform.tenants.memberships import TenantRole
from agent_platform.platform.tenants.permissions import TenantPermission, role_has_permission


@dataclass(frozen=True, slots=True)
class DispatchContext:
    """派发时刻从库里重新读到的事实；None 一律按「不可用」处理。"""

    published_version: int | None
    definition: Mapping[str, object] | None
    creator_role: TenantRole | None


def evaluate_dispatch_guards(task: ScheduledTask, context: DispatchContext) -> SkipReason | None:
    """返回跳过原因；None 表示本次允许派发。

    这里的每个原因都是**配置性失效**（创建者被移出企业、员工已下线、发布版关掉了
    定时能力、输入与发布版 Schema 不兼容）——它们不会自愈，因此都登记在
    `_GUARD_PAUSE_REASONS` 里，由调用方自动暂停任务并留下审计。

    **C16 配额接入点在这里，但配额不是配置性失效**：配额超限是**瞬态**状态，下个
    计费周期会自愈。因此新增的配额 `SkipReason` **必须不进入** `_GUARD_PAUSE_REASONS`
    ——不登记的原因会走 `_claim_one` 的 `_advance` 分支，表现为「本次临时跳过、
    推进到下一个触发点、任务保持启用」，这正是配额需要的语义。把配额登记进
    `_GUARD_PAUSE_REASONS` 会让配额一超限就永久暂停定时任务，配额恢复后也不自愈。
    详见 `docs/core-capability-roadmap.md` C16 条目的强制门禁①。
    """

    if context.creator_role is None or not role_has_permission(
        role=context.creator_role, permission=TenantPermission.RUNS_EXECUTE
    ):
        return SkipReason.CREATOR_PERMISSION_REVOKED
    if (
        context.published_version is None
        or context.definition is None
        or not is_runnable_employee_definition(context.definition)
    ):
        return SkipReason.EMPLOYEE_NOT_RUNNABLE
    if not is_scheduling_enabled(context.definition.get("capabilities")):
        return SkipReason.SCHEDULED_TASKS_DISABLED

    input_schema = context.definition.get("input_schema")
    if not isinstance(input_schema, dict):
        return SkipReason.EMPLOYEE_NOT_RUNNABLE
    capabilities = context.definition.get("capabilities")
    file_upload_enabled = (
        capabilities.get("file_upload") is True if isinstance(capabilities, Mapping) else False
    )
    try:
        validate_run_input(
            input_schema=input_schema,
            value=task.input_data,
            file_upload_enabled=file_upload_enabled,
        )
    except (DynamicInputTooLarge, DynamicInputValidationFailed, InvalidDynamicSchema):
        return SkipReason.INPUT_SCHEMA_INCOMPATIBLE
    return None
