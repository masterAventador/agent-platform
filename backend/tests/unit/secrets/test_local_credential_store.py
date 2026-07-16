"""C09 本地凭据写入服务：与既有只读解析器同一文档格式的产品化写入。"""

import json
import os
import stat
from pathlib import Path
from uuid import uuid4

import pytest

from agent_platform.infrastructure.secrets.local_file import (
    LocalCredentialConfigurationError,
    LocalFileCredentialResolver,
    LocalFileCredentialStore,
)

TENANT = uuid4()


def _store(tmp_path: Path) -> LocalFileCredentialStore:
    return LocalFileCredentialStore(
        credentials_file=tmp_path / "secrets" / "credentials.json",
        repository_root=tmp_path / "repo",
    )


def _resolver(tmp_path: Path) -> LocalFileCredentialResolver:
    return LocalFileCredentialResolver(
        credentials_file=tmp_path / "secrets" / "credentials.json",
        repository_root=tmp_path / "repo",
    )


@pytest.mark.asyncio
async def test_store_round_trips_through_existing_resolver(tmp_path: Path) -> None:
    store = _store(tmp_path)
    reference = "local://mcp-servers/abc"

    await store.store(
        tenant_id=TENANT,
        reference=reference,
        values={"Authorization": "Bearer token-1"},
    )

    resolved = await _resolver(tmp_path).resolve(tenant_id=TENANT, references=[reference])
    assert resolved == {"Authorization": "Bearer token-1"}

    # 幂等覆盖更新
    await store.store(
        tenant_id=TENANT,
        reference=reference,
        values={"Authorization": "Bearer token-2"},
    )
    resolved = await _resolver(tmp_path).resolve(tenant_id=TENANT, references=[reference])
    assert resolved == {"Authorization": "Bearer token-2"}


@pytest.mark.asyncio
async def test_store_creates_file_with_owner_only_permissions(tmp_path: Path) -> None:
    store = _store(tmp_path)
    await store.store(
        tenant_id=TENANT, reference="local://mcp-servers/a", values={"X-Key": "v"}
    )
    path = tmp_path / "secrets" / "credentials.json"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


@pytest.mark.asyncio
async def test_store_preserves_other_tenants_and_references(tmp_path: Path) -> None:
    store = _store(tmp_path)
    other_tenant = uuid4()
    await store.store(
        tenant_id=other_tenant, reference="local://mcp-servers/x", values={"A": "1"}
    )
    await store.store(
        tenant_id=TENANT, reference="local://mcp-servers/y", values={"B": "2"}
    )

    await store.delete(tenant_id=TENANT, reference="local://mcp-servers/y")

    resolved = await _resolver(tmp_path).resolve(
        tenant_id=other_tenant, references=["local://mcp-servers/x"]
    )
    assert resolved == {"A": "1"}
    with pytest.raises(LocalCredentialConfigurationError):
        await _resolver(tmp_path).resolve(
            tenant_id=TENANT, references=["local://mcp-servers/y"]
        )


@pytest.mark.asyncio
async def test_store_rejects_invalid_keys_and_values(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(LocalCredentialConfigurationError):
        await store.store(
            tenant_id=TENANT,
            reference="local://mcp-servers/a",
            values={"Bad Key With Space": "v"},
        )
    with pytest.raises(LocalCredentialConfigurationError):
        await store.store(
            tenant_id=TENANT,
            reference="local://mcp-servers/a",
            values={"X-Key": "line1\r\nline2"},
        )
    with pytest.raises(LocalCredentialConfigurationError):
        await store.store(tenant_id=TENANT, reference="", values={"X-Key": "v"})
    assert not (tmp_path / "secrets" / "credentials.json").exists()


@pytest.mark.asyncio
async def test_store_refuses_file_inside_repository(tmp_path: Path) -> None:
    store = LocalFileCredentialStore(
        credentials_file=tmp_path / "repo" / "credentials.json",
        repository_root=tmp_path / "repo",
    )
    with pytest.raises(LocalCredentialConfigurationError):
        await store.store(
            tenant_id=TENANT, reference="local://mcp-servers/a", values={"X": "v"}
        )


@pytest.mark.asyncio
async def test_store_without_configured_file_fails_closed(tmp_path: Path) -> None:
    store = LocalFileCredentialStore(
        credentials_file=None, repository_root=tmp_path / "repo"
    )
    with pytest.raises(LocalCredentialConfigurationError):
        await store.store(
            tenant_id=TENANT, reference="local://mcp-servers/a", values={"X": "v"}
        )


@pytest.mark.asyncio
async def test_delete_is_idempotent(tmp_path: Path) -> None:
    store = _store(tmp_path)
    await store.store(
        tenant_id=TENANT, reference="local://mcp-servers/a", values={"X": "v"}
    )
    await store.delete(tenant_id=TENANT, reference="local://mcp-servers/a")
    await store.delete(tenant_id=TENANT, reference="local://mcp-servers/a")
    document = json.loads((tmp_path / "secrets" / "credentials.json").read_text())
    assert document.get(str(TENANT), {}) == {}
