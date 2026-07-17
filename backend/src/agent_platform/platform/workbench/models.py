from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EmployeeCounts:
    total: int
    draft: int
    published: int


@dataclass(frozen=True, slots=True)
class RunCounts:
    total: int
    queued: int
    running: int
    waiting_for_input: int
    waiting_for_approval: int
    completed: int
    failed: int
    cancelled: int


@dataclass(frozen=True, slots=True)
class WorkbenchSummary:
    employees: EmployeeCounts
    runs: RunCounts
    pending_approvals: int = 0
