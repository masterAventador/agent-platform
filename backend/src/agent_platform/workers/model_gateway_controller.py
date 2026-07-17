"""模型网关 Provisioning Controller 进程入口。

架构边界（`docs/backend-architecture.md` 4.4）：对账必须由独立 Controller 完成，
不得在 API 进程内伪装 Controller 行为。本进程与 `sandbox_janitor` 同构——独立镜像命令、
独立副本数、独立生命周期，只消费 outbox，不处理任何 HTTP 请求。

它是唯一同时持有 LiteLLM master key 与租户 Key 派生密钥的进程：API 只写 desired 状态，
Worker 只派生自己租户的 Key，二者都不持有 master key。
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic
from typing import Protocol

from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agent_platform.config import AppSettings
from agent_platform.infrastructure.database.bootstrap import initialize_database_metadata
from agent_platform.infrastructure.database.repositories.model_gateway import (
    SqlAlchemyModelGatewayCommandStore,
)
from agent_platform.infrastructure.database.repositories.model_usage import (
    SessionModelUsagePruner,
)
from agent_platform.infrastructure.llm.admin import (
    LiteLLMAdminClient,
    LiteLLMAdminConfigurationError,
)
from agent_platform.infrastructure.llm.provisioner import LiteLLMModelGatewayProvisioner
from agent_platform.platform.model_gateway.credentials import (
    ModelGatewayKeySecretNotConfiguredError,
)
from agent_platform.platform.model_gateway.reconciler import ModelGatewayReconciler

READY_FILE = Path("/tmp/agent-platform-model-gateway-controller-ready")
logger = logging.getLogger(__name__)


class ControllerConfigurationError(Exception):
    """Controller 缺少安全运行所需的明确装配；消息不含任何凭据材料。"""


class Reconciler(Protocol):
    async def reconcile_once(self, *, now: datetime) -> bool: ...

    async def prune_settled_commands(
        self, *, now: datetime, retention: timedelta, limit: int
    ) -> int: ...


class UsagePruner(Protocol):
    async def prune(self, *, now: datetime, retention: timedelta, limit: int) -> int: ...


async def serve_controller(
    *,
    reconciler: Reconciler,
    stop_event: asyncio.Event,
    interval_seconds: float,
    retention: timedelta,
    prune_interval_seconds: float,
    prune_batch_limit: int,
    ready_file: Path = READY_FILE,
    usage_pruner: UsagePruner | None = None,
    usage_retention: timedelta | None = None,
    usage_prune_interval_seconds: float = 3_600.0,
    usage_prune_batch_limit: int = 1_000,
) -> None:
    ready_file.touch(mode=0o600)
    next_prune_at = 0.0
    next_usage_prune_at = 0.0
    try:
        while not stop_event.is_set():
            try:
                # 有活就连续排空，无活才退避：不做固定间隔的空转轮询。
                drained = not await reconciler.reconcile_once(now=_now())
            except Exception as error:
                # 单条命令失败不得终止整个 Controller，否则所有租户一起停止对账。
                # 只记录异常类型：上游异常文本可能包含响应体或凭据材料。
                logger.error(
                    "model_gateway_reconcile_failed",
                    extra={"error_type": type(error).__name__},
                )
                drained = True
            if monotonic() >= next_prune_at:
                try:
                    await reconciler.prune_settled_commands(
                        now=_now(),
                        retention=retention,
                        limit=prune_batch_limit,
                    )
                except Exception as error:
                    logger.error(
                        "model_gateway_command_prune_failed",
                        extra={"error_type": type(error).__name__},
                    )
                next_prune_at = monotonic() + prune_interval_seconds
            if (
                usage_pruner is not None
                and usage_retention is not None
                and monotonic() >= next_usage_prune_at
            ):
                try:
                    await usage_pruner.prune(
                        now=_now(),
                        retention=usage_retention,
                        limit=usage_prune_batch_limit,
                    )
                except Exception as error:
                    # 用量清扫失败绝不终止 Controller（对账必须继续）。
                    logger.error(
                        "model_usage_prune_failed",
                        extra={"error_type": type(error).__name__},
                    )
                next_usage_prune_at = monotonic() + usage_prune_interval_seconds
            if drained:
                # 用 stop_event 做退避等待：SIGTERM 立即唤醒，不被 sleep 拖住。
                with suppress(TimeoutError):
                    await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
    finally:
        ready_file.unlink(missing_ok=True)


def build_reconciler(
    *,
    settings: AppSettings,
    session_factory: async_sessionmaker[AsyncSession],
) -> ModelGatewayReconciler:
    master_key = settings.model_gateway_admin_master_key
    if not master_key.get_secret_value():
        # 缺 master key 时无法对账：启动即失败，避免进程"活着但永远不工作"。
        raise ControllerConfigurationError("model gateway admin master key is required")
    try:
        admin = LiteLLMAdminClient(
            base_url=settings.model_gateway_admin_url,
            master_key=master_key,
            timeout_seconds=settings.model_gateway_admin_timeout_seconds,
        )
        provisioner = LiteLLMModelGatewayProvisioner(
            admin=admin,
            key_secret=_require_key_secret(settings.model_gateway_key_secret),
        )
    except (
        LiteLLMAdminConfigurationError,
        ModelGatewayKeySecretNotConfiguredError,
    ):
        raise ControllerConfigurationError("model gateway admin client is not configured") from None
    return ModelGatewayReconciler(
        store=SqlAlchemyModelGatewayCommandStore(session_factory),
        provisioner=provisioner,
    )


async def run_controller_service(
    *,
    settings: AppSettings | None = None,
    stop_event: asyncio.Event | None = None,
    ready_file: Path = READY_FILE,
) -> None:
    initialize_database_metadata()
    app_settings = settings or AppSettings()
    engine = create_async_engine(app_settings.database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    service_stop = stop_event or asyncio.Event()
    _install_signal_handlers(service_stop)
    try:
        reconciler = build_reconciler(
            settings=app_settings,
            session_factory=session_factory,
        )
        await serve_controller(
            reconciler=reconciler,
            stop_event=service_stop,
            interval_seconds=app_settings.model_gateway_controller_interval_seconds,
            retention=timedelta(days=app_settings.model_gateway_command_retention_days),
            prune_interval_seconds=(app_settings.model_gateway_command_prune_interval_seconds),
            prune_batch_limit=app_settings.model_gateway_command_prune_batch_limit,
            ready_file=ready_file,
            # C16 阶段二：用量表随调用无界增长，由本 Controller 循环按保留期有界清扫。
            usage_pruner=SessionModelUsagePruner(session_factory),
            usage_retention=timedelta(days=app_settings.model_usage_retention_days),
            usage_prune_interval_seconds=app_settings.model_usage_prune_interval_seconds,
            usage_prune_batch_limit=app_settings.model_usage_prune_batch_limit,
        )
    finally:
        await engine.dispose()


def _require_key_secret(secret: SecretStr) -> SecretStr:
    if not secret.get_secret_value():
        raise ModelGatewayKeySecretNotConfiguredError()
    return secret


def _now() -> datetime:
    return datetime.now(UTC)


def _install_signal_handlers(stop_event: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, stop_event.set)
        except NotImplementedError:
            signal.signal(signum, lambda _signum, _frame: stop_event.set())


def main() -> None:
    replicas = int(os.getenv("AGENT_PLATFORM_MODEL_GATEWAY_CONTROLLER_REPLICAS", "1"))
    if replicas < 1:
        print("controller replicas must be positive", file=sys.stderr)
        raise SystemExit(2)
    try:
        asyncio.run(run_controller_service())
    except ControllerConfigurationError as error:
        print(f"model gateway controller configuration error: {error}", file=sys.stderr)
        raise SystemExit(2) from error


if __name__ == "__main__":
    main()
