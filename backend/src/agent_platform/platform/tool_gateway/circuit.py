"""Per-(tenant, server) in-memory circuit breaker for tool execution.

进程内快速失败保护：连续失败达到阈值后打开，冷却期结束半开放行一次，
成功即闭合。进程重启后状态清零（回到正常尝试），属于可接受的保守语义。
"""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

DEFAULT_FAILURE_THRESHOLD = 5
DEFAULT_COOLDOWN_SECONDS = 30.0
DEFAULT_MAX_ENTRIES = 1024


@dataclass
class _CircuitState:
    consecutive_failures: int = 0
    opened_at: float | None = None


class InMemoryToolCircuitBreaker:
    def __init__(
        self,
        *,
        failure_threshold: int = DEFAULT_FAILURE_THRESHOLD,
        cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        if cooldown_seconds <= 0:
            raise ValueError("cooldown_seconds must be positive")
        if max_entries < 1:
            raise ValueError("max_entries must be >= 1")
        self._failure_threshold = failure_threshold
        self._cooldown_seconds = cooldown_seconds
        self._max_entries = max_entries
        self._clock = clock
        self._states: OrderedDict[tuple[UUID, UUID], _CircuitState] = OrderedDict()

    @property
    def entry_count(self) -> int:
        return len(self._states)

    def allow(self, *, tenant_id: UUID, server_id: UUID) -> bool:
        state = self._states.get((tenant_id, server_id))
        if state is None or state.opened_at is None:
            return True
        if self._clock() - state.opened_at >= self._cooldown_seconds:
            # 半开：放行一次试探；失败会重新打开并刷新 opened_at。
            state.opened_at = self._clock()
            return True
        return False

    def record_success(self, *, tenant_id: UUID, server_id: UUID) -> None:
        self._states.pop((tenant_id, server_id), None)

    def record_failure(self, *, tenant_id: UUID, server_id: UUID) -> None:
        key = (tenant_id, server_id)
        state = self._states.get(key)
        if state is None:
            state = _CircuitState()
        state.consecutive_failures += 1
        if state.consecutive_failures >= self._failure_threshold:
            state.opened_at = self._clock()
        self._states[key] = state
        self._states.move_to_end(key)
        while len(self._states) > self._max_entries:
            self._states.popitem(last=False)
