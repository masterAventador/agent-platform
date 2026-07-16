from __future__ import annotations

import asyncio
import os
import sys
from uuid import UUID

import docker
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_platform.infrastructure.database.models import load_database_models
from agent_platform.infrastructure.database.repositories.runs import (
    RunCommandRecord,
    RunEventRecord,
)
from agent_platform.infrastructure.database.repositories.sandbox import SandboxLeaseRecord
from agent_platform.sandbox.controller.service import LEASE_LABEL, MANAGED_LABEL

load_database_models()


async def verify(run_id: UUID) -> None:
    database_url = os.environ["AGENT_PLATFORM_DATABASE_URL"]
    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            command = (
                await session.execute(
                    select(RunCommandRecord).where(
                        RunCommandRecord.run_id == run_id,
                        RunCommandRecord.action == "start",
                    )
                )
            ).scalar_one()
            if command.dispatched_at is None or command.processed_at is None:
                raise AssertionError("run command was not dispatched and processed")
            event_types = list(
                (
                    await session.execute(
                        select(RunEventRecord.event_type)
                        .where(RunEventRecord.run_id == run_id)
                        .order_by(RunEventRecord.sequence)
                    )
                ).scalars()
            )
            required = {"run.started", "message.output", "run.completed"}
            if not required.issubset(event_types):
                raise AssertionError(f"missing runtime events: {required - set(event_types)}")
            lease = (
                await session.execute(
                    select(SandboxLeaseRecord).where(SandboxLeaseRecord.run_id == run_id)
                )
            ).scalar_one()
            if lease.status != "deleted":
                raise AssertionError(f"sandbox lease is not deleted: {lease.status}")
    finally:
        await engine.dispose()
    containers = docker.from_env().containers.list(
        all=True,
        filters={
            "label": [
                f"{MANAGED_LABEL}=true",
                f"{LEASE_LABEL}={lease.id}",
            ]
        },
    )
    if containers:
        raise AssertionError("runtime E2E sandbox container remains")


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: verify_runtime_e2e RUN_ID")
    asyncio.run(verify(UUID(sys.argv[1])))


if __name__ == "__main__":
    main()
