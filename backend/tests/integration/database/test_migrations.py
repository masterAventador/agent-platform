import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

BACKEND_ROOT = Path(__file__).parents[3]


def test_tenant_migration_can_upgrade_and_downgrade(tmp_path: Path) -> None:
    database_path = tmp_path / "migration.db"
    config = Config(BACKEND_ROOT / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database_path}")

    command.upgrade(config, "head")

    with sqlite3.connect(database_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(tenants)").fetchall()}
        user_columns = {row[1] for row in connection.execute("PRAGMA table_info(users)").fetchall()}
        session_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(auth_sessions)").fetchall()
        }
        membership_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(tenant_memberships)").fetchall()
        }
        employee_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(employees)").fetchall()
        }
        version_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(employee_versions)").fetchall()
        }
        run_columns = {row[1] for row in connection.execute("PRAGMA table_info(runs)").fetchall()}
        event_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(run_events)").fetchall()
        }
        command_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(run_commands)").fetchall()
        }
        knowledge_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(knowledge_bases)").fetchall()
        }
        skill_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(skills)").fetchall()
        }
        skill_version_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(skill_versions)").fetchall()
        }
        mcp_server_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(mcp_servers)").fetchall()
        }
        tool_columns = {row[1] for row in connection.execute("PRAGMA table_info(tools)").fetchall()}
        sandbox_lease_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(sandbox_leases)").fetchall()
        }
        tool_audit_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(tool_audit_events)").fetchall()
        }
        tool_audit_foreign_keys = connection.execute(
            "PRAGMA foreign_key_list(tool_audit_events)"
        ).fetchall()
        tool_audit_indexes = connection.execute(
            "SELECT name, sql FROM sqlite_master "
            "WHERE type = 'index' AND tbl_name = 'tool_audit_events'"
        ).fetchall()
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
    assert membership_columns == {"id", "tenant_id", "user_id", "role", "created_at"}
    assert {"id", "tenant_id", "name", "runtime_type", "status"} <= employee_columns
    assert {"id", "employee_id", "tenant_id", "version", "definition"} <= version_columns
    assert {"id", "tenant_id", "employee_id", "thread_id", "status"} <= run_columns
    assert {"event_id", "run_id", "sequence", "event_type", "payload"} <= event_columns
    assert {"id", "run_id", "action", "dispatched_at", "processed_at"} <= command_columns
    assert {"id", "tenant_id", "name", "provider", "provider_id"} <= knowledge_columns
    assert {"id", "tenant_id", "name", "latest_version", "published_version"} <= skill_columns
    assert {"id", "skill_id", "version", "digest", "storage_key"} <= skill_version_columns
    assert {"id", "tenant_id", "name", "transport", "secret_reference"} <= mcp_server_columns
    assert {"id", "tenant_id", "server_id", "name", "input_schema", "risk_level"} <= tool_columns
    assert {
        "id",
        "tenant_id",
        "user_id",
        "run_id",
        "thread_id",
        "provider",
        "sandbox_id",
        "status",
        "expires_at",
        "last_error",
    } <= sandbox_lease_columns
    assert {
        "id",
        "event_type",
        "occurred_at",
        "tenant_id",
        "run_id",
        "employee_id",
        "user_id",
        "tool_id",
        "tool_name",
        "risk",
        "argument_keys",
        "argument_sha256",
        "argument_size_bytes",
        "reason",
        "succeeded",
        "invocation_id",
    } == tool_audit_columns
    assert tool_audit_foreign_keys == []
    assert (
        "ix_tool_audit_events_invocation_id",
        "CREATE INDEX ix_tool_audit_events_invocation_id ON tool_audit_events "
        "(invocation_id) WHERE invocation_id IS NOT NULL",
    ) in tool_audit_indexes

    command.downgrade(config, "base")

    with sqlite3.connect(database_path) as connection:
        platform_tables = connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name IN ("
            "'tenants', 'users', 'auth_sessions', 'tenant_memberships', "
            "'employees', 'employee_versions'"
            ", 'runs', 'run_events', 'run_commands', 'knowledge_bases', "
            "'skills', 'skill_versions', 'mcp_servers', 'tools', 'sandbox_leases', "
            "'tool_audit_events'"
            ")"
        ).fetchall()
    assert platform_tables == []


def test_sandbox_epoch_is_added_by_forward_only_migration(tmp_path: Path) -> None:
    database_path = tmp_path / "sandbox-epoch.db"
    config = Config(BACKEND_ROOT / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database_path}")

    command.upgrade(config, "20260713_0013")
    with sqlite3.connect(database_path) as connection:
        at_epoch_revision = {
            row[1] for row in connection.execute("PRAGMA table_info(sandbox_leases)")
        }
    assert "epoch" in at_epoch_revision
    assert "sandbox_epoch" not in at_epoch_revision

    command.upgrade(config, "20260713_0014")
    with sqlite3.connect(database_path) as connection:
        at_pre_generation_head = {
            row[1] for row in connection.execute("PRAGMA table_info(sandbox_leases)")
        }
    assert "epoch" in at_pre_generation_head
    assert "sandbox_epoch" not in at_pre_generation_head

    command.upgrade(config, "head")
    with sqlite3.connect(database_path) as connection:
        at_head = {row[1] for row in connection.execute("PRAGMA table_info(sandbox_leases)")}
    assert {"epoch", "sandbox_epoch"}.issubset(at_head)

    command.downgrade(config, "20260713_0014")
    with sqlite3.connect(database_path) as connection:
        after_generation_downgrade = {
            row[1] for row in connection.execute("PRAGMA table_info(sandbox_leases)")
        }
    assert "epoch" in after_generation_downgrade
    assert "sandbox_epoch" not in after_generation_downgrade

    command.downgrade(config, "20260713_0012")
    with sqlite3.connect(database_path) as connection:
        after_downgrade = {
            row[1] for row in connection.execute("PRAGMA table_info(sandbox_leases)")
        }
    assert {"epoch", "sandbox_epoch"}.isdisjoint(after_downgrade)


def test_migration_head_is_current_forward_only_revision() -> None:
    config = Config(BACKEND_ROOT / "alembic.ini")
    assert ScriptDirectory.from_config(config).get_current_head() == "20260713_0015"
