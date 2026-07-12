import json
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from agent_platform.config import AppSettings
from agent_platform.infrastructure.secrets import local_file as local_file_module
from agent_platform.infrastructure.secrets.local_file import (
    LocalCredentialConfigurationError,
    LocalFileCredentialResolver,
)


def _write_credentials(path: Path, payload: object, *, mode: int = 0o600) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")
    os.chmod(path, mode)


def _resolver(path: Path | None, *, repository_root: Path) -> LocalFileCredentialResolver:
    return LocalFileCredentialResolver(
        credentials_file=path,
        repository_root=repository_root,
    )


@pytest.mark.asyncio
async def test_unconfigured_resolver_allows_only_an_empty_request(tmp_path: Path) -> None:
    resolver = _resolver(None, repository_root=tmp_path / "repository")

    assert await resolver.resolve(tenant_id=uuid4(), references=[]) == {}
    with pytest.raises(LocalCredentialConfigurationError) as caught:
        await resolver.resolve(tenant_id=uuid4(), references=["crm-token"])

    assert caught.value.code == "local_credentials_not_configured"
    assert str(caught.value) == "Local credentials are not configured"


@pytest.mark.asyncio
async def test_empty_request_never_reads_an_unrelated_configured_file(tmp_path: Path) -> None:
    missing_file = tmp_path / "missing.json"
    resolver = _resolver(missing_file, repository_root=tmp_path / "repository")

    assert await resolver.resolve(tenant_id=uuid4(), references=[]) == {}


@pytest.mark.asyncio
async def test_resolver_returns_only_requested_values_and_deduplicates_references(
    tmp_path: Path,
) -> None:
    tenant_id = uuid4()
    credentials_file = tmp_path / "credentials.json"
    _write_credentials(
        credentials_file,
        {
            str(tenant_id): {
                "crm-token": {"Authorization": "Bearer crm-secret"},
                "billing-token": {"X-API-Key": "billing-secret"},
            }
        },
    )
    resolver = _resolver(credentials_file, repository_root=tmp_path / "repository")

    resolved = await resolver.resolve(
        tenant_id=tenant_id,
        references=["crm-token", "crm-token"],
    )

    assert resolved == {"Authorization": "Bearer crm-secret"}
    assert "billing-secret" not in repr(resolved)


@pytest.mark.asyncio
async def test_resolver_fails_closed_for_missing_or_cross_tenant_reference(
    tmp_path: Path,
) -> None:
    first_tenant = uuid4()
    second_tenant = uuid4()
    credentials_file = tmp_path / "credentials.json"
    _write_credentials(
        credentials_file,
        {str(first_tenant): {"tenant-only": {"Authorization": "Bearer hidden"}}},
    )
    resolver = _resolver(credentials_file, repository_root=tmp_path / "repository")

    for tenant_id, reference in (
        (first_tenant, "missing"),
        (second_tenant, "tenant-only"),
    ):
        with pytest.raises(LocalCredentialConfigurationError) as caught:
            await resolver.resolve(tenant_id=tenant_id, references=[reference])
        assert caught.value.code == "local_credential_unavailable"
        assert str(caught.value) == "Requested local credential is unavailable"
        assert reference not in repr(caught.value)
        assert "hidden" not in repr(caught.value)


@pytest.mark.asyncio
async def test_resolver_rejects_conflicting_keys_across_references(tmp_path: Path) -> None:
    tenant_id = uuid4()
    credentials_file = tmp_path / "credentials.json"
    _write_credentials(
        credentials_file,
        {
            str(tenant_id): {
                "primary": {"Authorization": "Bearer first-secret"},
                "secondary": {"Authorization": "Bearer second-secret"},
            }
        },
    )
    resolver = _resolver(credentials_file, repository_root=tmp_path / "repository")

    with pytest.raises(LocalCredentialConfigurationError) as caught:
        await resolver.resolve(
            tenant_id=tenant_id,
            references=["primary", "secondary"],
        )

    assert caught.value.code == "local_credential_conflict"
    assert str(caught.value) == "Requested local credentials conflict"
    assert "first-secret" not in repr(caught.value)
    assert "second-secret" not in repr(caught.value)


@pytest.mark.asyncio
async def test_resolver_accepts_same_value_for_a_shared_key(tmp_path: Path) -> None:
    tenant_id = uuid4()
    credentials_file = tmp_path / "credentials.json"
    _write_credentials(
        credentials_file,
        {
            str(tenant_id): {
                "primary": {"Authorization": "Bearer shared-secret"},
                "secondary": {"Authorization": "Bearer shared-secret"},
            }
        },
    )
    resolver = _resolver(credentials_file, repository_root=tmp_path / "repository")

    assert await resolver.resolve(
        tenant_id=tenant_id,
        references=["primary", "secondary"],
    ) == {"Authorization": "Bearer shared-secret"}


@pytest.mark.asyncio
async def test_resolver_rejects_yaml_instead_of_using_an_ambiguous_parser(tmp_path: Path) -> None:
    tenant_id = uuid4()
    credentials_file = tmp_path / "credentials.yaml"
    credentials_file.write_text(
        f'"{tenant_id}":\n  crm-token:\n    Authorization: "Bearer yaml-secret"\n',
        encoding="utf-8",
    )
    os.chmod(credentials_file, 0o600)
    resolver = _resolver(credentials_file, repository_root=tmp_path / "repository")

    with pytest.raises(LocalCredentialConfigurationError) as caught:
        await resolver.resolve(tenant_id=tenant_id, references=["crm-token"])

    assert caught.value.code == "local_credentials_invalid"
    assert "yaml-secret" not in repr(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [0o640, 0o644, 0o660, 0o600 | 0o100])
async def test_resolver_requires_exact_owner_only_file_permissions(
    tmp_path: Path,
    mode: int,
) -> None:
    tenant_id = uuid4()
    credentials_file = tmp_path / "credentials.json"
    _write_credentials(credentials_file, {str(tenant_id): {}}, mode=mode)
    resolver = _resolver(credentials_file, repository_root=tmp_path / "repository")

    with pytest.raises(LocalCredentialConfigurationError) as caught:
        await resolver.resolve(tenant_id=tenant_id, references=["required"])

    assert caught.value.code == "local_credentials_insecure_permissions"
    assert str(caught.value) == "Local credentials file permissions must be 0600"


@pytest.mark.asyncio
async def test_resolver_rejects_a_credentials_file_inside_the_repository(tmp_path: Path) -> None:
    tenant_id = uuid4()
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    credentials_file = repository_root / "credentials.json"
    _write_credentials(credentials_file, {str(tenant_id): {}})
    resolver = _resolver(credentials_file, repository_root=repository_root)

    with pytest.raises(LocalCredentialConfigurationError) as caught:
        await resolver.resolve(tenant_id=tenant_id, references=["required"])

    assert caught.value.code == "local_credentials_inside_repository"
    assert str(caught.value) == "Local credentials file must be outside the repository"


@pytest.mark.asyncio
async def test_invalid_document_error_never_exposes_file_contents(tmp_path: Path) -> None:
    secret = "must-not-appear-in-error"
    credentials_file = tmp_path / "credentials.json"
    credentials_file.write_text(f'{{"broken":"{secret}"', encoding="utf-8")
    os.chmod(credentials_file, 0o600)
    resolver = _resolver(credentials_file, repository_root=tmp_path / "repository")

    with pytest.raises(LocalCredentialConfigurationError) as caught:
        await resolver.resolve(tenant_id=uuid4(), references=["anything"])

    assert caught.value.code == "local_credentials_invalid"
    assert str(caught.value) == "Local credentials file is invalid"
    assert secret not in repr(caught.value)


@pytest.mark.asyncio
async def test_resolver_rejects_a_file_not_owned_by_the_current_user(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id = uuid4()
    credentials_file = tmp_path / "credentials.json"
    _write_credentials(credentials_file, {str(tenant_id): {"required": {}}})
    real_fstat = local_file_module.os.fstat

    def foreign_owner_fstat(fd: int) -> object:
        current = real_fstat(fd)
        return SimpleNamespace(
            st_mode=current.st_mode,
            st_uid=os.geteuid() + 1,
            st_size=current.st_size,
        )

    monkeypatch.setattr(local_file_module.os, "fstat", foreign_owner_fstat)
    resolver = _resolver(credentials_file, repository_root=tmp_path / "repository")

    with pytest.raises(LocalCredentialConfigurationError) as caught:
        await resolver.resolve(tenant_id=tenant_id, references=["required"])

    assert caught.value.code == "local_credentials_wrong_owner"
    assert str(caught.value) == "Local credentials file must be owned by the current user"


@pytest.mark.asyncio
async def test_resolver_rejects_an_oversized_file_before_parsing(tmp_path: Path) -> None:
    secret = "oversized-secret-must-not-leak"
    credentials_file = tmp_path / "credentials.json"
    credentials_file.write_text(
        '{"padding":"' + secret + ("x" * 1_100_000) + '"}',
        encoding="utf-8",
    )
    os.chmod(credentials_file, 0o600)
    resolver = _resolver(credentials_file, repository_root=tmp_path / "repository")

    with pytest.raises(LocalCredentialConfigurationError) as caught:
        await resolver.resolve(tenant_id=uuid4(), references=["required"])

    assert caught.value.code == "local_credentials_too_large"
    assert str(caught.value) == "Local credentials file is too large"
    assert secret not in repr(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw_document",
    [
        '{"00000000-0000-0000-0000-000000000000":{},'
        '"00000000-0000-0000-0000-000000000000":{}}',
        '{"00000000-0000-0000-0000-000000000000":'
        '{"same":{},"same":{}}}',
        '{"00000000-0000-0000-0000-000000000000":'
        '{"ref":{"Authorization":"first","Authorization":"second-secret"}}}',
    ],
)
async def test_resolver_fails_closed_for_duplicate_json_keys(
    tmp_path: Path,
    raw_document: str,
) -> None:
    credentials_file = tmp_path / "credentials.json"
    credentials_file.write_text(raw_document, encoding="utf-8")
    os.chmod(credentials_file, 0o600)
    resolver = _resolver(credentials_file, repository_root=tmp_path / "repository")

    with pytest.raises(LocalCredentialConfigurationError) as caught:
        await resolver.resolve(tenant_id=UUID(int=0), references=["ref"])

    assert caught.value.code == "local_credentials_invalid"
    assert "second-secret" not in repr(caught.value)


@pytest.mark.asyncio
async def test_resolver_requires_canonical_tenant_uuid_keys(tmp_path: Path) -> None:
    tenant_id = uuid4()
    credentials_file = tmp_path / "credentials.json"
    _write_credentials(credentials_file, {str(tenant_id).upper(): {"required": {}}})
    resolver = _resolver(credentials_file, repository_root=tmp_path / "repository")

    with pytest.raises(LocalCredentialConfigurationError) as caught:
        await resolver.resolve(tenant_id=tenant_id, references=["required"])

    assert caught.value.code == "local_credentials_invalid"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reference", "key", "value"),
    [
        ("r" * 1001, "Authorization", "Bearer safe"),
        ("required", "Bad Header", "safe"),
        ("required", "Authorization", "line-one\r\nInjected: secret"),
        ("required", "Authorization", "bad\x00secret"),
    ],
)
async def test_resolver_rejects_unsafe_reference_header_or_value(
    tmp_path: Path,
    reference: str,
    key: str,
    value: str,
) -> None:
    tenant_id = uuid4()
    credentials_file = tmp_path / "credentials.json"
    _write_credentials(credentials_file, {str(tenant_id): {reference: {key: value}}})
    resolver = _resolver(credentials_file, repository_root=tmp_path / "repository")

    with pytest.raises(LocalCredentialConfigurationError) as caught:
        await resolver.resolve(tenant_id=tenant_id, references=[reference])

    assert caught.value.code == "local_credentials_invalid"
    assert "secret" not in repr(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("reference", ["", "r" * 1001])
async def test_resolver_rejects_invalid_requested_references_before_file_access(
    tmp_path: Path,
    reference: str,
) -> None:
    resolver = _resolver(tmp_path / "missing.json", repository_root=tmp_path / "repository")

    with pytest.raises(LocalCredentialConfigurationError) as caught:
        await resolver.resolve(tenant_id=uuid4(), references=[reference])

    assert caught.value.code == "local_credentials_invalid"
    assert str(caught.value) == "Local credentials file is invalid"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"not-a-uuid": {}},
        {str(uuid4()): []},
        {str(uuid4()): {"reference": []}},
        {str(uuid4()): {"reference": {"API_KEY": 123}}},
        {str(uuid4()): {"": {"API_KEY": "secret"}}},
    ],
)
async def test_resolver_rejects_invalid_document_shapes_without_values_in_errors(
    tmp_path: Path,
    payload: object,
) -> None:
    credentials_file = tmp_path / "credentials.json"
    _write_credentials(credentials_file, payload)
    resolver = _resolver(credentials_file, repository_root=tmp_path / "repository")

    with pytest.raises(LocalCredentialConfigurationError) as caught:
        await resolver.resolve(tenant_id=UUID(int=0), references=["required"])

    assert caught.value.code == "local_credentials_invalid"
    assert str(caught.value) == "Local credentials file is invalid"


def test_settings_reads_only_the_explicit_local_credentials_file_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_PLATFORM_LOCAL_CREDENTIALS_FILE", "/tmp/agent-secrets.json")
    monkeypatch.setenv("AGENT_PLATFORM_LOCAL_CREDENTIALS_FAKE_TOKEN", "must-be-ignored")

    settings = AppSettings()

    assert settings.local_credentials_file == "/tmp/agent-secrets.json"


def test_settings_do_not_assume_repository_root() -> None:
    assert AppSettings().local_credentials_repository_root is None
