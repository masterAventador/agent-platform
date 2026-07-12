import asyncio
import logging
import os
import signal
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)

from agent_platform.config import AppSettings
from agent_platform.infrastructure.database.bootstrap import initialize_database_metadata
from agent_platform.infrastructure.queue.dispatcher import RunCommandDispatcher
from agent_platform.infrastructure.queue.redis_streams import RedisRunQueue

logger = logging.getLogger(__name__)


class DispatcherConfigurationError(Exception):
    """Dispatcher 缺少安全运行所需的明确配置。"""


class DispatcherStartupError(Exception):
    """Dispatcher 启动依赖不可用；消息只能包含脱敏后的错误类型。"""


class DispatcherLoop(Protocol):
    async def dispatch_pending(self, *, limit: int = 100) -> int: ...


@dataclass(slots=True)
class DispatcherHealth:
    """进程存活/就绪状态；ready 文件供容器健康检查读取。"""

    ready_file: Path = Path("/tmp/agent-platform-dispatcher-ready")
    live: bool = False
    ready: bool = False
    single_replica: bool = True

    def mark_ready(self) -> None:
        self.live = True
        self.ready = True
        self.ready_file.touch(exist_ok=True)

    def mark_stopped(self) -> None:
        self.ready = False
        self.live = False
        self.ready_file.unlink(missing_ok=True)


async def serve(
    *,
    dispatcher: DispatcherLoop,
    stop_event: asyncio.Event,
    health: DispatcherHealth,
    batch_size: int = 100,
    idle_backoff_seconds: float = 0.5,
) -> None:
    try:
        health.mark_ready()
        while not stop_event.is_set():
            try:
                dispatched = await dispatcher.dispatch_pending(limit=batch_size)
            except Exception as error:
                logger.error("dispatcher cycle failed: %s", type(error).__name__)
                dispatched = 0
            if dispatched == 0 and not stop_event.is_set():
                await _wait_for_stop(stop_event, timeout=idle_backoff_seconds)
    finally:
        health.mark_stopped()


async def run_dispatcher_service(
    *,
    dispatcher: DispatcherLoop | None = None,
    settings: AppSettings | None = None,
    stop_event: asyncio.Event | None = None,
    health: DispatcherHealth | None = None,
    replicas: int = 1,
    batch_size: int = 100,
    idle_backoff_seconds: float = 0.5,
) -> None:
    if replicas != 1:
        raise DispatcherConfigurationError(
            "only a single replica is supported until outbox claiming has a durable lease"
        )
    if batch_size < 1:
        raise DispatcherConfigurationError("batch size must be positive")
    if idle_backoff_seconds < 0:
        raise DispatcherConfigurationError("idle backoff must not be negative")

    initialize_database_metadata()
    engine: AsyncEngine | None = None
    redis: Redis | None = None
    service_health = health or DispatcherHealth()
    try:
        app_settings = settings or AppSettings()
        service_dispatcher = dispatcher
        if service_dispatcher is None:
            engine = create_async_engine(app_settings.database_url)
            session_factory = async_sessionmaker(engine, expire_on_commit=False)
            redis = Redis.from_url(app_settings.redis_url, decode_responses=True)
            await _verify_dependencies(engine, redis)
            service_dispatcher = RunCommandDispatcher(
                session_factory=session_factory,
                queue=RedisRunQueue(
                    redis,
                    stream_name=app_settings.run_queue_stream_name,
                    group_name=app_settings.run_queue_group_name,
                ),
            )

        service_stop = stop_event or asyncio.Event()
        _install_signal_handlers(service_stop)
        await serve(
            dispatcher=service_dispatcher,
            stop_event=service_stop,
            health=service_health,
            batch_size=batch_size,
            idle_backoff_seconds=idle_backoff_seconds,
        )
    except DispatcherConfigurationError:
        raise
    except Exception as error:
        raise DispatcherStartupError(
            f"dependency verification failed: {type(error).__name__}"
        ) from error
    finally:
        service_health.mark_stopped()
        if redis is not None:
            await redis.aclose()
        if engine is not None:
            await engine.dispose()


async def _verify_dependencies(engine: AsyncEngine, redis: Redis) -> None:
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))
    await redis.ping()


async def _wait_for_stop(stop_event: asyncio.Event, *, timeout: float) -> None:
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=timeout)
    except TimeoutError:
        return


def _install_signal_handlers(stop_event: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, stop_event.set)
        except NotImplementedError:
            signal.signal(signum, lambda _signum, _frame: stop_event.set())


def main() -> None:
    try:
        replicas = int(os.getenv("AGENT_PLATFORM_DISPATCHER_REPLICAS", "1"))
        batch_size = int(os.getenv("AGENT_PLATFORM_DISPATCHER_BATCH_SIZE", "100"))
        idle_backoff_seconds = float(
            os.getenv("AGENT_PLATFORM_DISPATCHER_IDLE_BACKOFF_SECONDS", "0.5")
        )
        asyncio.run(
            run_dispatcher_service(
                replicas=replicas,
                batch_size=batch_size,
                idle_backoff_seconds=idle_backoff_seconds,
            )
        )
    except (DispatcherConfigurationError, ValueError) as error:
        if isinstance(error, ValueError):
            message = "numeric environment values are invalid"
        else:
            message = str(error)
        print(f"dispatcher configuration error: {message}", file=sys.stderr)
        raise SystemExit(2) from error
    except DispatcherStartupError as error:
        print(f"dispatcher startup error: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
