from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_platform.config import AppSettings
from agent_platform.infrastructure.database.repositories.audit import SqlAlchemyToolAuditSink
from agent_platform.infrastructure.database.repositories.workflows import (
    SqlAlchemyWorkflowSpecLoader,
)
from agent_platform.infrastructure.secrets import LocalFileCredentialResolver
from agent_platform.sandbox.manager import SandboxManager
from agent_platform.workers.runtime_adapters import (
    RuntimeAdapterConfigurationError,
    create_runtime_adapters,
)


@pytest.mark.asyncio
async def test_builtin_adapter_bundle_requires_no_external_module(tmp_path) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    settings = AppSettings(
        sandbox_controller_secret="controller-secret-long",
        local_credentials_repository_root=str(tmp_path),
    )

    adapters = create_runtime_adapters(settings=settings, session_factory=session_factory)

    assert isinstance(adapters.sandbox_manager, SandboxManager)
    assert isinstance(adapters.credential_resolver, LocalFileCredentialResolver)
    assert isinstance(adapters.audit_sink, SqlAlchemyToolAuditSink)
    assert isinstance(adapters.workflow_spec_loader, SqlAlchemyWorkflowSpecLoader)
    await adapters.aclose()
    await engine.dispose()


@pytest.mark.asyncio
async def test_adapter_without_credentials_file_does_not_require_repository_root() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    adapters = create_runtime_adapters(
        settings=AppSettings(sandbox_controller_secret="controller-secret-long"),
        session_factory=session_factory,
    )

    assert await adapters.credential_resolver.resolve(tenant_id=uuid4(), references=[]) == {}
    await adapters.aclose()
    await engine.dispose()


def test_credentials_file_requires_explicit_repository_root() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    with pytest.raises(RuntimeAdapterConfigurationError, match="repository root"):
        create_runtime_adapters(
            settings=AppSettings(
                sandbox_controller_secret="controller-secret-long",
                local_credentials_file="/tmp/credentials.json",
            ),
            session_factory=session_factory,
        )


def test_controller_secret_must_match_controller_minimum_length(tmp_path) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    with pytest.raises(RuntimeAdapterConfigurationError, match="at least 16"):
        create_runtime_adapters(
            settings=AppSettings(
                sandbox_controller_secret="too-short",
                local_credentials_repository_root=str(tmp_path),
            ),
            session_factory=session_factory,
        )


def test_builtin_adapter_bundle_fails_fast_without_controller_secret(tmp_path) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    with pytest.raises(RuntimeAdapterConfigurationError, match="secret"):
        create_runtime_adapters(
            settings=AppSettings(local_credentials_repository_root=str(tmp_path)),
            session_factory=session_factory,
        )


