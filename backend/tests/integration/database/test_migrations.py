import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

BACKEND_ROOT = Path(__file__).parents[3]


def test_tenant_migration_can_upgrade_and_downgrade(tmp_path: Path) -> None:
    database_path = tmp_path / "migration.db"
    config = Config(BACKEND_ROOT / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database_path}")

    command.upgrade(config, "head")

    with sqlite3.connect(database_path) as connection:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(tenants)").fetchall()
        }
        user_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(users)").fetchall()
        }
        session_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(auth_sessions)").fetchall()
        }
    assert columns == {"id", "name", "slug", "created_at"}
    assert user_columns == {
        "id",
        "email",
        "password_hash",
        "email_verified",
        "created_at",
    }
    assert session_columns == {
        "id",
        "user_id",
        "token_digest",
        "created_at",
        "expires_at",
        "revoked_at",
    }

    command.downgrade(config, "base")

    with sqlite3.connect(database_path) as connection:
        platform_tables = connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name IN ('tenants', 'users', 'auth_sessions')"
        ).fetchall()
    assert platform_tables == []
