from __future__ import annotations

import errno
import json
import os
import sqlite3
import stat
from collections.abc import Mapping
from contextlib import suppress
from pathlib import Path
from typing import Any


class SqliteDeviceAccountStateStore:
    """Capability-owned durable adapter used until Core wires PostgreSQL migrations."""

    _SCHEMA_VERSION = 1

    def __init__(self, path: Path) -> None:
        self._path = path.absolute()
        descriptor = self._open_without_follow(
            os.O_CREAT | os.O_RDWR, create_parent=True
        )
        try:
            os.fchmod(descriptor, 0o600)
            self._validate_private_file(os.fstat(descriptor))
        finally:
            os.close(descriptor)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS social_device_account_state (
                    singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
                    schema_version INTEGER NOT NULL,
                    revision INTEGER NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )

    def load(self) -> Mapping[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT schema_version, payload
                FROM social_device_account_state
                WHERE singleton_id = 1
                """
            ).fetchone()
        if row is None:
            return None
        if int(row[0]) != self._SCHEMA_VERSION:
            raise ValueError("unsupported Social Operations state schema")
        payload = json.loads(str(row[1]))
        if not isinstance(payload, dict):
            raise ValueError("invalid Social Operations state payload")
        return payload

    def save(self, state: Mapping[str, Any]) -> None:
        payload = json.dumps(state, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO social_device_account_state (
                    singleton_id, schema_version, revision, payload
                ) VALUES (1, ?, 1, ?)
                ON CONFLICT(singleton_id) DO UPDATE SET
                    schema_version = excluded.schema_version,
                    revision = social_device_account_state.revision + 1,
                    payload = excluded.payload
                """,
                (self._SCHEMA_VERSION, payload),
            )
            connection.commit()

    def _connect(self) -> sqlite3.Connection:
        before = self._secure_file_identity()
        connection = sqlite3.connect(self._path, timeout=5)
        try:
            after = self._secure_file_identity()
            if after != before:
                raise ValueError("state path changed while opening")
            connection.execute("PRAGMA busy_timeout = 5000")
            return connection
        except BaseException:
            connection.close()
            raise

    def _secure_file_identity(self) -> tuple[int, int]:
        descriptor = self._open_without_follow(os.O_RDWR)
        try:
            metadata = os.fstat(descriptor)
            self._validate_private_file(metadata)
            return metadata.st_dev, metadata.st_ino
        finally:
            os.close(descriptor)

    def _open_without_follow(self, flags: int, *, create_parent: bool = False) -> int:
        no_follow = getattr(os, "O_NOFOLLOW", 0)
        parent_descriptor = self._open_secure_parent(create=create_parent)
        try:
            return os.open(
                self._path.name,
                flags | no_follow,
                0o600,
                dir_fd=parent_descriptor,
            )
        except OSError as error:
            if error.errno in {errno.ELOOP, errno.ENOTDIR}:
                raise ValueError("state path must not be a symbolic link") from None
            raise error
        finally:
            os.close(parent_descriptor)

    def _assert_secure_path(self) -> None:
        self._secure_file_identity()

    def _open_secure_parent(self, *, create: bool) -> int:
        no_follow = getattr(os, "O_NOFOLLOW", 0)
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | no_follow
        current_descriptor = os.open(self._path.anchor, directory_flags)
        try:
            for part in self._path.parent.parts[1:]:
                try:
                    next_descriptor = os.open(
                        part,
                        directory_flags,
                        dir_fd=current_descriptor,
                    )
                except FileNotFoundError:
                    if not create:
                        raise
                    with suppress(FileExistsError):
                        os.mkdir(part, mode=0o700, dir_fd=current_descriptor)
                    next_descriptor = os.open(
                        part,
                        directory_flags,
                        dir_fd=current_descriptor,
                    )
                except OSError as error:
                    if error.errno in {errno.ELOOP, errno.ENOTDIR}:
                        raise ValueError(
                            "state path ancestors must not be symbolic links"
                        ) from None
                    raise
                os.close(current_descriptor)
                current_descriptor = next_descriptor
            self._validate_private_directory(os.fstat(current_descriptor))
            return current_descriptor
        except OSError as error:
            os.close(current_descriptor)
            if error.errno in {errno.ELOOP, errno.ENOTDIR}:
                raise ValueError(
                    "state path ancestors must not be symbolic links"
                ) from None
            raise
        except BaseException:
            os.close(current_descriptor)
            raise

    @staticmethod
    def _validate_private_directory(metadata: os.stat_result) -> None:
        if not stat.S_ISDIR(metadata.st_mode):
            raise ValueError("state parent must be a private directory")
        if hasattr(os, "getuid") and metadata.st_uid != os.getuid():
            raise ValueError("state parent must be an owner-only private directory")
        if stat.S_IMODE(metadata.st_mode) & 0o077:
            raise ValueError("state parent must be an owner-only private directory")

    @staticmethod
    def _validate_private_file(metadata: os.stat_result) -> None:
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("state path must be a regular file")
        if hasattr(os, "getuid") and metadata.st_uid != os.getuid():
            raise ValueError("state path must be owner-only")
        if stat.S_IMODE(metadata.st_mode) & 0o077:
            raise ValueError("state path must be owner-only")
