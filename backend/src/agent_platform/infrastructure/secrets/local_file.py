from __future__ import annotations

import fcntl
import json
import os
import re
import stat
import tempfile
from collections.abc import Mapping, Sequence
from contextlib import suppress
from pathlib import Path
from uuid import UUID

_MAX_CREDENTIALS_FILE_BYTES = 1024 * 1024
_MAX_REFERENCE_LENGTH = 1000
_MAX_CREDENTIAL_KEY_LENGTH = 256
_HTTP_TOKEN = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")


class _DuplicateJSONKey(ValueError):
    pass


class LocalCredentialConfigurationError(RuntimeError):
    """Stable, sanitized local credential configuration failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class LocalFileCredentialResolver:
    """Development-only credential resolver backed by an external owner-only file."""

    def __init__(
        self,
        *,
        credentials_file: str | Path | None,
        repository_root: str | Path,
    ) -> None:
        self._credentials_file = (
            Path(credentials_file).expanduser() if credentials_file is not None else None
        )
        self._repository_root = Path(repository_root).expanduser().resolve()

    async def resolve(
        self,
        *,
        tenant_id: UUID,
        references: Sequence[str],
    ) -> Mapping[str, str]:
        if any(not reference or len(reference) > _MAX_REFERENCE_LENGTH for reference in references):
            raise self._invalid_document()
        unique_references = tuple(dict.fromkeys(references))
        if not unique_references:
            return {}
        if self._credentials_file is None:
            raise LocalCredentialConfigurationError(
                "local_credentials_not_configured",
                "Local credentials are not configured",
            )

        document = self._load_document()
        tenant_credentials = document.get(str(tenant_id))
        if tenant_credentials is None:
            raise self._unavailable()

        resolved: dict[str, str] = {}
        for reference in unique_references:
            values = tenant_credentials.get(reference)
            if values is None:
                raise self._unavailable()
            for key, value in values.items():
                existing = resolved.get(key)
                if existing is not None and existing != value:
                    raise LocalCredentialConfigurationError(
                        "local_credential_conflict",
                        "Requested local credentials conflict",
                    )
                resolved[key] = value
        return resolved

    def _load_document(self) -> dict[str, dict[str, dict[str, str]]]:
        try:
            raw = json.loads(
                self._read_secure_contents(),
                object_pairs_hook=self._unique_object,
            )
            return self._validated_document(raw)
        except LocalCredentialConfigurationError:
            raise
        except Exception:
            raise LocalCredentialConfigurationError(
                "local_credentials_invalid",
                "Local credentials file is invalid",
            ) from None

    def _read_secure_contents(self) -> str:
        assert self._credentials_file is not None
        try:
            path = self._credentials_file.resolve(strict=True)
        except OSError:
            raise LocalCredentialConfigurationError(
                "local_credentials_unavailable",
                "Local credentials file is unavailable",
            ) from None
        if path == self._repository_root or self._repository_root in path.parents:
            raise LocalCredentialConfigurationError(
                "local_credentials_inside_repository",
                "Local credentials file must be outside the repository",
            )
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = -1
        try:
            descriptor = os.open(self._credentials_file, flags)
            file_stat = os.fstat(descriptor)
            if not stat.S_ISREG(file_stat.st_mode):
                raise LocalCredentialConfigurationError(
                    "local_credentials_invalid",
                    "Local credentials file is invalid",
                )
            if file_stat.st_uid != os.geteuid():
                raise LocalCredentialConfigurationError(
                    "local_credentials_wrong_owner",
                    "Local credentials file must be owned by the current user",
                )
            if stat.S_IMODE(file_stat.st_mode) != 0o600:
                raise LocalCredentialConfigurationError(
                    "local_credentials_insecure_permissions",
                    "Local credentials file permissions must be 0600",
                )
            if file_stat.st_size > _MAX_CREDENTIALS_FILE_BYTES:
                raise LocalCredentialConfigurationError(
                    "local_credentials_too_large",
                    "Local credentials file is too large",
                )
            with os.fdopen(descriptor, encoding="utf-8") as stream:
                descriptor = -1
                contents = stream.read(_MAX_CREDENTIALS_FILE_BYTES + 1)
            if len(contents.encode("utf-8")) > _MAX_CREDENTIALS_FILE_BYTES:
                raise LocalCredentialConfigurationError(
                    "local_credentials_too_large",
                    "Local credentials file is too large",
                )
            return contents
        except LocalCredentialConfigurationError:
            raise
        except (OSError, UnicodeError):
            raise LocalCredentialConfigurationError(
                "local_credentials_unavailable",
                "Local credentials file is unavailable",
            ) from None
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    @classmethod
    def _validated_document(cls, raw: object) -> dict[str, dict[str, dict[str, str]]]:
        if not isinstance(raw, Mapping):
            raise cls._invalid_document()
        document: dict[str, dict[str, dict[str, str]]] = {}
        for tenant_key, tenant_value in raw.items():
            if not isinstance(tenant_key, str) or not cls._is_canonical_uuid(tenant_key):
                raise cls._invalid_document()
            if not isinstance(tenant_value, Mapping):
                raise cls._invalid_document()
            references: dict[str, dict[str, str]] = {}
            for reference, values in tenant_value.items():
                if (
                    not isinstance(reference, str)
                    or not reference
                    or len(reference) > _MAX_REFERENCE_LENGTH
                ):
                    raise cls._invalid_document()
                if not isinstance(values, Mapping):
                    raise cls._invalid_document()
                value_map: dict[str, str] = {}
                for key, value in values.items():
                    if (
                        not isinstance(key, str)
                        or len(key) > _MAX_CREDENTIAL_KEY_LENGTH
                        or _HTTP_TOKEN.fullmatch(key) is None
                        or not isinstance(value, str)
                        or any(character in value for character in ("\r", "\n", "\x00"))
                    ):
                        raise cls._invalid_document()
                    value_map[key] = value
                references[reference] = value_map
            document[tenant_key] = references
        return document

    @staticmethod
    def _is_canonical_uuid(value: str) -> bool:
        try:
            parsed = UUID(value)
        except ValueError:
            return False
        return str(parsed) == value

    @staticmethod
    def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise _DuplicateJSONKey
            result[key] = value
        return result

    @staticmethod
    def _invalid_document() -> LocalCredentialConfigurationError:
        return LocalCredentialConfigurationError(
            "local_credentials_invalid",
            "Local credentials file is invalid",
        )

    @staticmethod
    def _unavailable() -> LocalCredentialConfigurationError:
        return LocalCredentialConfigurationError(
            "local_credential_unavailable",
            "Requested local credential is unavailable",
        )


_MAX_CREDENTIAL_VALUES = 20
_MAX_CREDENTIAL_VALUE_LENGTH = 4096


class LocalFileCredentialStore:
    """Development-only credential writer sharing the resolver document format.

    写入语义：owner-only 0600、临时文件原子替换、独立锁文件互斥；
    禁止把凭据文件放进仓库目录。生产多租户密钥服务在 C18 引入。
    """

    def __init__(
        self,
        *,
        credentials_file: str | Path | None,
        repository_root: str | Path,
    ) -> None:
        self._credentials_file = (
            Path(credentials_file).expanduser() if credentials_file is not None else None
        )
        self._repository_root = Path(repository_root).expanduser().resolve()

    async def store(
        self,
        *,
        tenant_id: UUID,
        reference: str,
        values: Mapping[str, str],
    ) -> None:
        self._validate_reference(reference)
        self._validate_values(values)
        path = self._required_path()
        with self._locked(path):
            document = self._load_or_empty(path)
            tenant_document = document.setdefault(str(tenant_id), {})
            tenant_document[reference] = dict(values)
            self._atomic_write(path, document)

    async def delete(self, *, tenant_id: UUID, reference: str) -> None:
        self._validate_reference(reference)
        path = self._required_path()
        with self._locked(path):
            document = self._load_or_empty(path)
            tenant_document = document.get(str(tenant_id))
            if tenant_document is None or reference not in tenant_document:
                return
            del tenant_document[reference]
            self._atomic_write(path, document)

    def _required_path(self) -> Path:
        if self._credentials_file is None:
            raise LocalCredentialConfigurationError(
                "local_credentials_not_configured",
                "Local credentials are not configured",
            )
        path = self._credentials_file.absolute()
        resolved_parent = path.parent.resolve() if path.parent.exists() else path.parent
        candidates = {path, Path(os.path.normpath(path))}
        if resolved_parent is not None:
            candidates.add(resolved_parent / path.name)
        for candidate in candidates:
            if (
                candidate == self._repository_root
                or self._repository_root in candidate.parents
            ):
                raise LocalCredentialConfigurationError(
                    "local_credentials_inside_repository",
                    "Local credentials file must be outside the repository",
                )
        return path

    def _locked(self, path: Path) -> _CredentialFileLock:
        return _CredentialFileLock(path.with_name(path.name + ".lock"))

    def _load_or_empty(self, path: Path) -> dict[str, dict[str, dict[str, str]]]:
        if not path.exists():
            return {}
        reader = LocalFileCredentialResolver(
            credentials_file=path,
            repository_root=self._repository_root,
        )
        return reader._load_document()  # noqa: SLF001 - 同模块内共享文档格式实现

    @staticmethod
    def _atomic_write(path: Path, document: dict[str, dict[str, dict[str, str]]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        serialized = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True)
        if len(serialized.encode("utf-8")) > _MAX_CREDENTIALS_FILE_BYTES:
            raise LocalCredentialConfigurationError(
                "local_credentials_too_large",
                "Local credentials file is too large",
            )
        descriptor, temp_name = tempfile.mkstemp(
            dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
        )
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(serialized)
            os.replace(temp_name, path)
            os.chmod(path, 0o600)
        except OSError:
            with suppress(OSError):
                os.unlink(temp_name)
            raise LocalCredentialConfigurationError(
                "local_credentials_unavailable",
                "Local credentials file is unavailable",
            ) from None

    @staticmethod
    def _validate_reference(reference: str) -> None:
        if not reference or len(reference) > _MAX_REFERENCE_LENGTH:
            raise LocalCredentialConfigurationError(
                "local_credentials_invalid",
                "Local credentials file is invalid",
            )

    @staticmethod
    def _validate_values(values: Mapping[str, str]) -> None:
        if not values or len(values) > _MAX_CREDENTIAL_VALUES:
            raise LocalCredentialConfigurationError(
                "local_credentials_invalid",
                "Local credentials file is invalid",
            )
        for key, value in values.items():
            if (
                not isinstance(key, str)
                or len(key) > _MAX_CREDENTIAL_KEY_LENGTH
                or _HTTP_TOKEN.fullmatch(key) is None
                or not isinstance(value, str)
                or not value
                or len(value) > _MAX_CREDENTIAL_VALUE_LENGTH
                or any(character in value for character in ("\r", "\n", "\x00"))
            ):
                raise LocalCredentialConfigurationError(
                    "local_credentials_invalid",
                    "Local credentials file is invalid",
                )


class _CredentialFileLock:
    def __init__(self, lock_path: Path) -> None:
        self._lock_path = lock_path
        self._descriptor: int | None = None

    def __enter__(self) -> _CredentialFileLock:
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(
            self._lock_path,
            os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
        except OSError:
            os.close(descriptor)
            raise LocalCredentialConfigurationError(
                "local_credentials_unavailable",
                "Local credentials file is unavailable",
            ) from None
        self._descriptor = descriptor
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self._descriptor is not None:
            try:
                fcntl.flock(self._descriptor, fcntl.LOCK_UN)
            finally:
                os.close(self._descriptor)
            self._descriptor = None
