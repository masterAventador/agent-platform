"""Provisioning Controller 进程契约（C16 阶段一）。

Controller 是独立于 API 请求路径的常驻进程（与 sandbox_janitor 同构）：对账只发生在
它自己的循环里，API 请求只写 desired 状态与 outbox。本层验证循环的取消、错误隔离、
有界清扫与生产装配。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest

from agent_platform.config import AppSettings
from agent_platform.workers.model_gateway_controller import (
    ControllerConfigurationError,
    build_reconciler,
    serve_controller,
)


class FakeReconciler:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.reconcile_calls = 0
        self.prune_calls = 0
        self.pruned_retentions: list[timedelta] = []
        self._error = error

    async def reconcile_once(self, *, now: datetime) -> bool:
        self.reconcile_calls += 1
        if self._error is not None:
            raise self._error
        return False

    async def prune_settled_commands(
        self, *, now: datetime, retention: timedelta, limit: int
    ) -> int:
        self.prune_calls += 1
        self.pruned_retentions.append(retention)
        return 0


async def _serve_briefly(reconciler: FakeReconciler, **kwargs: object) -> None:
    stop_event = asyncio.Event()
    task = asyncio.create_task(
        serve_controller(
            reconciler=reconciler,
            stop_event=stop_event,
            interval_seconds=0.01,
            retention=timedelta(days=7),
            prune_interval_seconds=0.01,
            prune_batch_limit=100,
            ready_file=kwargs.get("ready_file"),  # type: ignore[arg-type]
        )
    )
    await asyncio.sleep(0.08)
    stop_event.set()
    await asyncio.wait_for(task, timeout=2)


@pytest.mark.asyncio
async def test_controller_drains_the_outbox_and_prunes_settled_commands(tmp_path) -> None:
    reconciler = FakeReconciler()

    await _serve_briefly(reconciler, ready_file=tmp_path / "ready")

    assert reconciler.reconcile_calls >= 1
    assert reconciler.prune_calls >= 1
    assert reconciler.pruned_retentions[0] == timedelta(days=7)


@pytest.mark.asyncio
async def test_controller_stops_promptly_on_the_stop_event(tmp_path) -> None:
    """优雅停机：不得因为退避 sleep 而拖住 SIGTERM。"""
    reconciler = FakeReconciler()
    stop_event = asyncio.Event()
    ready_file = tmp_path / "ready"
    task = asyncio.create_task(
        serve_controller(
            reconciler=reconciler,
            stop_event=stop_event,
            interval_seconds=30,
            retention=timedelta(days=7),
            prune_interval_seconds=30,
            prune_batch_limit=100,
            ready_file=ready_file,
        )
    )
    await asyncio.sleep(0.05)
    assert ready_file.exists()
    stop_event.set()

    await asyncio.wait_for(task, timeout=1)

    assert not ready_file.exists()


@pytest.mark.asyncio
async def test_a_failing_reconcile_never_kills_the_controller_loop(tmp_path) -> None:
    """单条命令的异常不得让整个 Controller 退出，否则所有租户停止对账。"""
    reconciler = FakeReconciler(error=RuntimeError("gateway exploded"))

    await _serve_briefly(reconciler, ready_file=tmp_path / "ready")

    assert reconciler.reconcile_calls >= 2


@pytest.mark.asyncio
async def test_controller_loop_errors_never_leak_secrets(tmp_path, caplog) -> None:
    reconciler = FakeReconciler(error=RuntimeError("sk-super-secret-master-key"))

    with caplog.at_level("ERROR"):
        await _serve_briefly(reconciler, ready_file=tmp_path / "ready")

    assert "sk-super-secret-master-key" not in caplog.text


def test_production_controller_requires_an_explicit_admin_master_key() -> None:
    """没有 master key 就无法对账：必须启动即失败，而不是运行期静默不工作。"""
    with pytest.raises(ControllerConfigurationError):
        build_reconciler(
            settings=AppSettings(model_gateway_admin_master_key=""),
            session_factory=None,  # type: ignore[arg-type]
        )


def test_production_controller_assembly_wires_the_real_litellm_provisioner() -> None:
    from agent_platform.infrastructure.llm.provisioner import (
        LiteLLMModelGatewayProvisioner,
    )

    reconciler = build_reconciler(
        settings=AppSettings(
            model_gateway_admin_master_key="sk-local-admin-master-key-000001",
            model_gateway_key_secret="a-strong-model-gateway-key-secret-000001",
        ),
        session_factory=None,  # type: ignore[arg-type]
    )

    assert isinstance(reconciler._provisioner, LiteLLMModelGatewayProvisioner)


def test_controller_config_errors_never_leak_the_master_key() -> None:
    master_key = "sk-master-key-must-not-leak-0001"
    with pytest.raises(ControllerConfigurationError) as captured:
        build_reconciler(
            settings=AppSettings(
                model_gateway_admin_url="https://user:pass@litellm.example?token=leak",
                model_gateway_admin_master_key=master_key,
            ),
            session_factory=None,  # type: ignore[arg-type]
        )

    rendered = []
    error: BaseException | None = captured.value
    while error is not None:
        rendered.append(f"{error!r}\n{error}")
        error = error.__cause__
    text = "\n".join(rendered)
    assert master_key not in text
    assert "pass" not in text
    assert "token" not in text
