from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Any


class SqliteDeviceAccountStateStore:
    """Capability-owned durable adapter used until Core wires PostgreSQL migrations."""

    _SCHEMA_VERSION = 1

    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if self._path.is_symlink():
            raise ValueError("state path must not be a symbolic link")
        descriptor = os.open(self._path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(descriptor)
        os.chmod(self._path, 0o600)
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
        connection = sqlite3.connect(self._path, timeout=5)
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection
