"""Worker 与审批中心（C13）的联动：APPROVAL_REQUIRED 落库建审批记录并保持一致。

覆盖：记录创建（含业务上下文与风险等级）、事件重放幂等（进程重启后待办恢复）、
run 终态时结算悬挂的 pending 审批。
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_platform.infrastructure.database.base import Base
from agent_platform.infrastructure.database.models import load_database_models
from agent_platform.infrastructure.database.repositories.approvals import (
    ApprovalRecord,
    SqlAlchemyApprovalRepository,
)
from agent_platform.infrastructure.database.repositories.audit import AuditEventRecord
from agent_platform.infrastructure.database.repositories.employees import (
    SqlAlchemyEmployeeVersionRepository,
)
from agent_platform.infrastructure.database.repositories.runs import (
    RunCommandRecord,
    SqlAlchemyRunCommandRepository,
    SqlAlchemyRunRepository,
)
from agent_platform.infrastructure.database.repositories.tools import (
    SqlAlchemyToolRepository,
)
from agent_platform.infrastructure.queue.redis_streams import (
    RunQueueDelivery,
    RunQueueMessage,
)
from agent_platform.platform.approvals.entities import ApprovalStatus
from agent_platform.platform.employees.entities import EmployeeVersion
from agent_platform.platform.runs.commands import RunCommand, RunCommandAction
from agent_platform.platform.runs.entities import Run, RunStatus
from agent_platform.platform.runs.events import EventType, PlatformEvent
from agent_platform.platform.tools.entities import (
    McpServer,
    McpTransport,
    Tool,
    ToolRiskLevel,
)
from agent_platform.runtimes.base import RuntimeStartRequest, RuntimeState
from agent_platform.workers.run_worker import RunWorker


@pytest_asyncio.fixture
async def factory() -> AsyncIterator[async_sessionmaker]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    load_database_models()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    value = async_sessionmaker(engine, expire_on_commit=False)
    yield value
    await engine.dispose()


class ApprovalWaitingRuntime:
    """start 后进入等待审批；stream 始终重放全量历史（含审批事件）。"""

    def __init__(self, approval_id: UUID) -> None:
        self.approval_id = approval_id
        self.events: list[PlatformEvent] = []
        self.state: RuntimeState | None = None

    async def start(self, request: RuntimeStartRequest) -> RuntimeState:
        self.events = [
            PlatformEvent.create(
                tenant_id=request.tenant_id,
                employee_id=request.employee_id,
                run_id=request.run_id,
                sequence=1,
                event_type=EventType.RUN_STARTED,
                payload={},
            ),
            PlatformEvent.create(
                tenant_id=request.tenant_id,
                employee_id=request.employee_id,
                run_id=request.run_id,
                sequence=2,
                event_type=EventType.APPROVAL_REQUIRED,
                payload={
                    "status": "waiting_for_approval",
                    "approval_id": str(self.approval_id),
                    "tool_name": "send_email",
                    "arguments": {"to": "user@example.com"},
                },
            ),
        ]
        self.state = RuntimeState(
            run_id=request.run_id,
            status=RunStatus.WAITING_FOR_APPROVAL,
            data={},
        )
        return self.state

    async def cancel(self, run_id: UUID) -> None:
        assert self.state is not None
        self.events.append(
            PlatformEvent.create(
                tenant_id=self.events[0].tenant_id,
                employee_id=self.events[0].employee_id,
                run_id=run_id,
                sequence=len(self.events) + 1,
                event_type=EventType.RUN_CANCELLED,
                payload={"status": "cancelled"},
            )
        )
        self.state = RuntimeState(run_id=run_id, status=RunStatus.CANCELLED, data={})

    async def get_state(self, run_id: UUID) -> RuntimeState:
        assert self.state is not None and self.state.run_id == run_id
        return self.state

    def stream(self, run_id: UUID, *, after_sequence: int = 0):
        async def iterate():
            for event in self.events:
                if event.run_id == run_id and event.sequence > after_sequence:
                    yield event

        return iterate()


@dataclass
class Prepared:
    runtime: ApprovalWaitingRuntime
    employee_definition: dict[str, object]
    knowledge_context: object | None = None

    async def close(self) -> None:
        return None

    async def renew(self) -> None:
        return None

    async def detach(self) -> None:
        return None


@dataclass
class Resolver:
    runtime: ApprovalWaitingRuntime
    calls: list[Run] = field(default_factory=list)

    async def resolve(self, run: Run, definition: dict[str, object]) -> Prepared:
        self.calls.append(run)
        return Prepared(runtime=self.runtime, employee_definition=definition)


class OneMessageQueue:
    def __init__(self, *deliveries: RunQueueDelivery) -> None:
        self._deliveries = list(deliveries)
        self.acknowledged: list[str] = []
        self.dead_lettered: list[tuple[str, str]] = []

    async def dequeue(self, *, consumer_name: str, block_ms: int) -> RunQueueDelivery | None:
        del consumer_name, block_ms
        return self._deliveries.pop(0) if self._deliveries else None

    async def acknowledge(self, delivery_id: str) -> None:
        self.acknowledged.append(delivery_id)

    async def dead_letter_if_exhausted(
        self,
        delivery: RunQueueDelivery,
        *,
        error_type: str,
    ) -> bool:
        self.dead_lettered.append((delivery.delivery_id, error_type))
        return False

    async def exhausted_delivery_attempts(self, delivery_id: str) -> int | None:
        del delivery_id
        return None


async def _seed_run(factory: async_sessionmaker, *, tool: bool = True) -> tuple[Run, RunCommand]:
    run = Run.create(
        tenant_id=uuid4(),
        employee_id=uuid4(),
        employee_version=1,
        created_by=uuid4(),
        input_data={"task": "需要外部工具"},
    )
    command = RunCommand.create(
        run_id=run.id, tenant_id=run.tenant_id, action=RunCommandAction.START
    )
    version = EmployeeVersion(
        id=uuid4(),
        employee_id=run.employee_id,
        tenant_id=run.tenant_id,
        version=1,
        definition={"runtime_type": "workflow"},
        published_by=run.created_by,
        published_at=datetime.now(UTC),
    )
    async with factory() as session:
        await SqlAlchemyRunRepository(session).add(run)
        await SqlAlchemyEmployeeVersionRepository(session).add(version)
        await SqlAlchemyRunCommandRepository(session).add(command)
        if tool:
            tools = SqlAlchemyToolRepository(session)
            server = McpServer.create(
                tenant_id=run.tenant_id,
                created_by=run.created_by,
                name="邮件服务",
                transport=McpTransport.STREAMABLE_HTTP,
                endpoint="http://mail.internal/mcp",
                command=None,
                args=[],
                secret_reference=None,
                enabled=True,
            )
            await tools.add_server(server)
            await tools.add_tool(
                Tool.create(
                    tenant_id=run.tenant_id,
                    server_id=server.id,
                    name="send_email",
                    description="发送邮件",
                    input_schema={"type": "object"},
                    risk_level=ToolRiskLevel.EXTERNAL,
                    enabled=True,
                )
            )
        await session.commit()
    return run, command


def _delivery(command: RunCommand, run: Run, action: str = "start") -> RunQueueDelivery:
    return RunQueueDelivery(
        delivery_id=f"{action}-1",
        message=RunQueueMessage(
            command_id=command.id,
            run_id=run.id,
            tenant_id=run.tenant_id,
            action=action,
        ),
    )


@pytest.mark.asyncio
async def test_worker_creates_approval_record_for_approval_required_event(factory) -> None:
    run, command = await _seed_run(factory)
    approval_id = uuid4()
    worker = RunWorker(
        session_factory=factory,
        queue=OneMessageQueue(_delivery(command, run)),
        runtime_resolver=Resolver(ApprovalWaitingRuntime(approval_id)),
        consumer_name="test-worker",
        approval_pending_timeout_seconds=300,
    )

    assert await worker.run_once(block_ms=1) is True

    async with factory() as session:
        records = list(
            (await session.execute(select(ApprovalRecord))).scalars()
        )
    assert len(records) == 1
    record = records[0]
    assert record.tenant_id == run.tenant_id
    assert record.run_id == run.id
    assert record.invocation_id == approval_id
    assert record.requested_by == run.created_by
    assert record.status == "pending"
    assert record.source == "tool_risk"
    assert record.risk_level == "external"
    assert record.context["tool_name"] == "send_email"
    assert record.context["arguments"] == {"to": "user@example.com"}
    assert record.request_key == f"tool:{run.id}:{approval_id}"
    assert record.expires_at is not None
    expires_at = (
        record.expires_at
        if record.expires_at.tzinfo is not None
        else record.expires_at.replace(tzinfo=UTC)
    )
    remaining = expires_at - datetime.now(UTC)
    assert timedelta(minutes=4) < remaining <= timedelta(minutes=5)

    # C14 审计经 worker 投递路径真实落库（HMAC 链有效）：worker 进程必须已装配
    # 审计哈希器，否则审计写入 fail-closed，投递失败（本用例的 conftest 已装配）。
    async with factory() as session:
        audit_actions = (
            (
                await session.execute(
                    select(AuditEventRecord.action).where(
                        AuditEventRecord.tenant_id == run.tenant_id,
                        AuditEventRecord.resource_id == record.id,
                    )
                )
            )
            .scalars()
            .all()
        )
    assert "approval.created" in audit_actions


@pytest.mark.asyncio
async def test_worker_replayed_approval_event_does_not_duplicate_record(factory) -> None:
    run, command = await _seed_run(factory)
    approval_id = uuid4()
    runtime = ApprovalWaitingRuntime(approval_id)
    worker = RunWorker(
        session_factory=factory,
        queue=OneMessageQueue(_delivery(command, run)),
        runtime_resolver=Resolver(runtime),
        consumer_name="test-worker",
        approval_pending_timeout_seconds=300,
    )
    assert await worker.run_once(block_ms=1) is True

    # 模拟进程重启后重新接管：全量历史重放（同 approval_id 新 event_id）
    cancel = RunCommand.create(
        run_id=run.id, tenant_id=run.tenant_id, action=RunCommandAction.CANCEL
    )
    async with factory() as session:
        await SqlAlchemyRunCommandRepository(session).add(cancel)
        await session.commit()
    worker._queue = OneMessageQueue(_delivery(cancel, run, action="cancel"))  # type: ignore[attr-defined]
    assert await worker.run_once(block_ms=1) is True

    async with factory() as session:
        records = list((await session.execute(select(ApprovalRecord))).scalars())
    assert len(records) == 1


@pytest.mark.asyncio
async def test_worker_settles_pending_approval_when_run_reaches_terminal(factory) -> None:
    run, command = await _seed_run(factory)
    approval_id = uuid4()
    runtime = ApprovalWaitingRuntime(approval_id)
    worker = RunWorker(
        session_factory=factory,
        queue=OneMessageQueue(_delivery(command, run)),
        runtime_resolver=Resolver(runtime),
        consumer_name="test-worker",
        approval_pending_timeout_seconds=300,
    )
    assert await worker.run_once(block_ms=1) is True

    cancel = RunCommand.create(
        run_id=run.id, tenant_id=run.tenant_id, action=RunCommandAction.CANCEL
    )
    async with factory() as session:
        await SqlAlchemyRunCommandRepository(session).add(cancel)
        await session.commit()
    worker._queue = OneMessageQueue(_delivery(cancel, run, action="cancel"))  # type: ignore[attr-defined]
    assert await worker.run_once(block_ms=1) is True

    async with factory() as session:
        persisted_run = await SqlAlchemyRunRepository(session).get(
            tenant_id=run.tenant_id, run_id=run.id
        )
        approvals, _ = await SqlAlchemyApprovalRepository(session).list(
            tenant_id=run.tenant_id, limit=10, offset=0
        )
    assert persisted_run is not None and persisted_run.status is RunStatus.CANCELLED
    assert len(approvals) == 1
    assert approvals[0].status is ApprovalStatus.WITHDRAWN
    assert approvals[0].decision_reason == "run_cancelled"


@pytest.mark.asyncio
async def test_sync_run_approvals_fails_closed_on_malformed_approval_id(factory) -> None:
    """安全 fail-closed：APPROVAL_REQUIRED 事件 approval_id 非法（无法解析 UUID）时，

    run 已进等待审批态却无法建审批记录属异常，必须让投递受控失败（抛错重投/死信），
    不得静默放行——否则会留下「WAITING_FOR_APPROVAL 却无记录」的 run 控制入口旁路窗口。
    """
    from agent_platform.infrastructure.database.repositories.approvals import (
        MalformedApprovalRequiredEvent,
        sync_run_approvals,
    )

    run, _ = await _seed_run(factory, tool=True)
    malformed_event = PlatformEvent.create(
        tenant_id=run.tenant_id,
        employee_id=run.employee_id,
        run_id=run.id,
        sequence=2,
        event_type=EventType.APPROVAL_REQUIRED,
        payload={
            "status": "waiting_for_approval",
            "approval_id": "not-a-uuid",
            "tool_name": "send_email",
        },
    )

    async with factory() as session:
        with pytest.raises(MalformedApprovalRequiredEvent):
            await sync_run_approvals(
                session,
                run=run,
                history=[malformed_event],
                pending_timeout=timedelta(minutes=5),
            )

    # 未静默建出半条记录
    async with factory() as session:
        records = list((await session.execute(select(ApprovalRecord))).scalars())
    assert records == []


@pytest.mark.asyncio
async def test_decide_tool_approval_without_invocation_id_is_guarded(factory) -> None:
    """防御 str(None)：TOOL_RISK 审批绑定了 run 却无 invocation_id 时，决策不得下发

    payload approval_id='None' 的 run 命令（worker 侧 UUID('None') 会崩）。当前不变量下
    TOOL_RISK+run 审批必然带 invocation_id；缺失属数据异常，须受控报错而非产出坏命令。
    """
    from agent_platform.infrastructure.database.repositories.approvals import (
        create_approval_service,
    )
    from agent_platform.platform.approvals.entities import Approval, ApprovalSource
    from agent_platform.platform.approvals.errors import ApprovalInvariantViolation
    from agent_platform.platform.runs.entities import Run
    from agent_platform.platform.tenants.memberships import TenantRole

    tenant_id = uuid4()
    approver = uuid4()
    run = Run.create(
        tenant_id=tenant_id,
        employee_id=uuid4(),
        employee_version=1,
        created_by=uuid4(),
        input_data={},
    ).transition_to(RunStatus.RUNNING).transition_to(RunStatus.WAITING_FOR_APPROVAL)
    approval = Approval.create(
        tenant_id=tenant_id,
        source=ApprovalSource.TOOL_RISK,
        approval_type="tool.invocation",
        risk_level="external",
        requested_by=run.created_by,
        request_key=f"tool:{run.id}:missing-invocation",
        context={"tool_name": "send_email"},
        run_id=run.id,
        invocation_id=None,
        employee_id=run.employee_id,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    async with factory() as session:
        await SqlAlchemyRunRepository(session).add(run)
        await SqlAlchemyApprovalRepository(session).add_idempotent(approval)
        await session.commit()

    async with factory() as session:
        with pytest.raises(ApprovalInvariantViolation):
            await create_approval_service(session).approve(
                tenant_id=tenant_id,
                approval_id=approval.id,
                actor_id=approver,
                actor_role=TenantRole.ADMIN,
            )

    # 未产出坏的 run 命令
    async with factory() as session:
        commands = list(
            (
                await session.execute(
                    select(RunCommandRecord).where(RunCommandRecord.run_id == run.id)
                )
            ).scalars()
        )
    assert commands == []
