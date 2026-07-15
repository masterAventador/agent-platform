import json
import sqlite3
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType
from uuid import UUID, uuid4

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.dialects import postgresql

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
        conversation_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(conversations)").fetchall()
        }
        conversation_message_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(conversation_messages)").fetchall()
        }
        conversation_message_foreign_keys = {
            (row[2], row[3], row[4])
            for row in connection.execute(
                "PRAGMA foreign_key_list(conversation_messages)"
            ).fetchall()
        }
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
        policy_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(tenant_model_gateway_policies)"
            ).fetchall()
        }
        provisioning_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(model_gateway_provisioning_commands)"
            ).fetchall()
        }
        policy_foreign_keys = {
            (row[2], row[3], row[4], row[6])
            for row in connection.execute(
                "PRAGMA foreign_key_list(tenant_model_gateway_policies)"
            ).fetchall()
        }
        provisioning_foreign_keys = {
            (row[2], row[3], row[4], row[6])
            for row in connection.execute(
                "PRAGMA foreign_key_list(model_gateway_provisioning_commands)"
            ).fetchall()
        }
        provisioning_unique_index = next(
            row[1]
            for row in connection.execute(
                "PRAGMA index_list(model_gateway_provisioning_commands)"
            ).fetchall()
            if row[2] == 1
        )
        provisioning_unique_columns = tuple(
            row[2]
            for row in connection.execute(
                f"PRAGMA index_info('{provisioning_unique_index}')"
            ).fetchall()
        )
        provisioning_table_sql = connection.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type = 'table' AND name = 'model_gateway_provisioning_commands'"
        ).fetchone()[0]
        file_columns = {row[1] for row in connection.execute("PRAGMA table_info(files)").fetchall()}
        attachment_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(task_attachments)").fetchall()
        }
        artifact_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(artifacts)").fetchall()
        }
        storage_operation_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(artifact_storage_operations)"
            ).fetchall()
        }
        attachment_foreign_keys = {
            (row[2], row[3], row[4])
            for row in connection.execute("PRAGMA foreign_key_list(task_attachments)").fetchall()
        }
        artifact_foreign_keys = {
            (row[2], row[3], row[4])
            for row in connection.execute("PRAGMA foreign_key_list(artifacts)").fetchall()
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
    assert membership_columns == {"id", "tenant_id", "user_id", "role", "created_at"}
    assert {"id", "tenant_id", "name", "runtime_type", "status"} <= employee_columns
    assert {"id", "employee_id", "tenant_id", "version", "definition"} <= version_columns
    assert {
        "id",
        "tenant_id",
        "employee_id",
            "thread_id",
            "status",
            "idempotency_key",
            "conversation_id",
        } <= run_columns
    assert {
        "id",
        "tenant_id",
        "employee_id",
        "created_by",
        "title",
        "thread_id",
        "created_at",
        "updated_at",
        "last_message_at",
    } == conversation_columns
    assert {
        "id",
        "tenant_id",
        "conversation_id",
        "run_id",
        "sequence",
        "role",
        "content",
        "attachment_ids",
        "created_at",
    } == conversation_message_columns
    assert ("conversations", "tenant_id", "tenant_id") in conversation_message_foreign_keys
    assert ("conversations", "conversation_id", "id") in conversation_message_foreign_keys
    assert ("runs", "run_id", "id") in conversation_message_foreign_keys
    assert {"event_id", "run_id", "sequence", "event_type", "payload"} <= event_columns
    assert {"id", "run_id", "action", "dispatched_at", "processed_at"} <= command_columns
    assert {"id", "tenant_id", "name", "provider", "provider_id"} <= knowledge_columns
    assert {
        "id",
        "tenant_id",
        "name",
        "latest_version",
        "published_version",
        "lifecycle_status",
        "source",
        "archived_at",
        "deleted_at",
    } <= skill_columns
    assert {
        "id",
        "skill_id",
        "version",
        "digest",
        "storage_key",
        "review_status",
        "security_findings",
        "reviewed_at",
    } <= skill_version_columns
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
    assert {
        "tenant_id",
        "enabled",
        "allowed_aliases",
        "budget_microusd",
        "budget_period",
        "rpm_limit",
        "tpm_limit",
        "max_parallel_requests",
        "revision",
        "status",
        "created_at",
        "updated_at",
        "updated_by",
    } == policy_columns
    assert {
        "id",
        "tenant_id",
        "desired_revision",
        "action",
        "status",
        "attempts",
        "last_error_code",
        "created_at",
        "processed_at",
    } == provisioning_columns
    assert ("tenants", "tenant_id", "id", "CASCADE") in policy_foreign_keys
    assert ("users", "updated_by", "id", "RESTRICT") in policy_foreign_keys
    assert ("tenants", "tenant_id", "id", "CASCADE") in provisioning_foreign_keys
    assert provisioning_unique_columns == ("tenant_id", "desired_revision", "action")
    assert "action = 'reconcile'" in provisioning_table_sql
    assert "status IN ('pending', 'processing', 'completed', 'failed')" in provisioning_table_sql
    assert {
        "id",
        "tenant_id",
        "owner_id",
        "name",
        "media_type",
        "size_bytes",
        "sha256",
        "storage_key",
        "created_at",
    } == file_columns
    assert {
        "id",
        "tenant_id",
        "run_id",
        "file_id",
        "workspace_path",
        "created_at",
    } == attachment_columns
    assert {
        "id",
        "tenant_id",
        "run_id",
        "created_by",
        "name",
        "media_type",
        "size_bytes",
        "sha256",
        "storage_key",
        "created_at",
    } == artifact_columns
    assert {
        "id",
        "tenant_id",
        "action",
        "entity_kind",
        "entity_id",
        "storage_key",
        "status",
        "phase",
        "lease_owner",
        "reconcile_after",
        "retire_after",
        "created_at",
        "updated_at",
    } == storage_operation_columns
    assert ("runs", "tenant_id", "tenant_id") in attachment_foreign_keys
    assert ("runs", "run_id", "id") in attachment_foreign_keys
    assert ("files", "tenant_id", "tenant_id") in attachment_foreign_keys
    assert ("files", "file_id", "id") in attachment_foreign_keys
    assert ("runs", "tenant_id", "tenant_id") in artifact_foreign_keys
    assert ("runs", "run_id", "id") in artifact_foreign_keys

    command.downgrade(config, "base")

    with sqlite3.connect(database_path) as connection:
        platform_tables = connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name IN ("
            "'tenants', 'users', 'auth_sessions', 'tenant_memberships', "
            "'employees', 'employee_versions'"
            ", 'runs', 'run_events', 'run_commands', 'knowledge_bases', "
            "'conversations', 'conversation_messages', "
            "'skills', 'skill_versions', 'mcp_servers', 'tools', 'sandbox_leases', "
            "'tool_audit_events', 'tenant_model_gateway_policies', "
            "'model_gateway_provisioning_commands', 'files', 'task_attachments', 'artifacts', "
            "'artifact_storage_operations'"
            ")"
        ).fetchall()
    assert platform_tables == []


def test_artifact_storage_lease_migration_backfills_existing_operations(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "artifact-storage-lease.db"
    config = Config(BACKEND_ROOT / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database_path}")
    command.upgrade(config, "20260715_0018")

    tenant_id = uuid4().hex
    timestamp = "2026-07-15 12:00:00+00:00"
    operations = [
        (uuid4().hex, "put", "pending", "intent"),
        (uuid4().hex, "delete", "pending", "metadata_applied"),
        (uuid4().hex, "put", "completed", "storage_applied"),
    ]
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO tenants (id, name, slug, created_at) VALUES (?, ?, ?, ?)",
            (tenant_id, "迁移租户", f"migration-{tenant_id}", timestamp),
        )
        connection.executemany(
            """
            INSERT INTO artifact_storage_operations (
                id, tenant_id, action, entity_kind, entity_id, storage_key,
                status, created_at, updated_at
            ) VALUES (?, ?, ?, 'artifact', ?, ?, ?, ?, ?)
            """,
            [
                (
                    operation_id,
                    tenant_id,
                    action,
                    uuid4().hex,
                    f"migration/{operation_id}",
                    status,
                    timestamp,
                    timestamp,
                )
                for operation_id, action, status, _expected_phase in operations
            ],
        )
        connection.commit()

    command.upgrade(config, "20260715_0019")
    with sqlite3.connect(database_path) as connection:
        migrated = connection.execute(
            """
            SELECT id, phase, lease_owner, reconcile_after
            FROM artifact_storage_operations
            ORDER BY id
            """
        ).fetchall()
    expected_phases = {
        operation_id: expected_phase
        for operation_id, _action, _status, expected_phase in operations
    }
    assert {
        operation_id: (phase, lease_owner, reconcile_after)
        for operation_id, phase, lease_owner, reconcile_after in migrated
    } == {
        operation_id: (expected_phase, None, timestamp)
        for operation_id, expected_phase in expected_phases.items()
    }

    command.downgrade(config, "20260715_0018")
    with sqlite3.connect(database_path) as connection:
        columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(artifact_storage_operations)"
            ).fetchall()
        }
        remaining = connection.execute(
            "SELECT COUNT(*) FROM artifact_storage_operations"
        ).fetchone()[0]
    assert {"phase", "lease_owner", "reconcile_after"}.isdisjoint(columns)
    assert remaining == 3


def test_artifact_workflow_hardening_migration_round_trips_existing_data(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "artifact-workflow-hardening.db"
    config = Config(BACKEND_ROOT / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database_path}")
    command.upgrade(config, "20260715_0019")

    tenant_id = uuid4().hex
    operation_id = uuid4().hex
    timestamp = "2026-07-16 12:00:00+00:00"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO tenants (id, name, slug, created_at) VALUES (?, ?, ?, ?)",
            (tenant_id, "存量租户", f"existing-{tenant_id}", timestamp),
        )
        connection.execute(
            """
            INSERT INTO artifact_storage_operations (
                id, tenant_id, action, entity_kind, entity_id, storage_key,
                status, phase, reconcile_after, created_at, updated_at
            ) VALUES (?, ?, 'put', 'file', ?, ?, 'compensated',
                      'metadata_applied', ?, ?, ?)
            """,
            (
                operation_id,
                tenant_id,
                uuid4().hex,
                f"migration/{operation_id}",
                timestamp,
                timestamp,
                timestamp,
            ),
        )
        connection.commit()

    command.upgrade(config, "20260716_0020")
    with sqlite3.connect(database_path) as connection:
        run_columns = {row[1] for row in connection.execute("PRAGMA table_info(runs)").fetchall()}
        operation_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(artifact_storage_operations)"
            ).fetchall()
        }
        unique_indexes = [
            row[1]
            for row in connection.execute("PRAGMA index_list(runs)").fetchall()
            if row[2] == 1
        ]
        unique_column_sets = {
            tuple(
                column[2]
                for column in connection.execute(f"PRAGMA index_info('{index_name}')").fetchall()
            )
            for index_name in unique_indexes
        }
        migrated = connection.execute(
            "SELECT status, retire_after FROM artifact_storage_operations WHERE id = ?",
            (operation_id,),
        ).fetchone()
        connection.execute(
            "UPDATE artifact_storage_operations "
            "SET status = 'retired', retire_after = ? WHERE id = ?",
            (timestamp, operation_id),
        )
        connection.commit()

    assert "idempotency_key" in run_columns
    assert "retire_after" in operation_columns
    assert (
        "tenant_id",
        "created_by",
        "employee_id",
        "idempotency_key",
    ) in unique_column_sets
    assert migrated == ("compensated", None)

    command.downgrade(config, "20260715_0019")
    with sqlite3.connect(database_path) as connection:
        downgraded_run_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(runs)").fetchall()
        }
        downgraded_operation_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(artifact_storage_operations)"
            ).fetchall()
        }
        downgraded = connection.execute(
            "SELECT status FROM artifact_storage_operations WHERE id = ?",
            (operation_id,),
        ).fetchone()

    assert "idempotency_key" not in downgraded_run_columns
    assert "retire_after" not in downgraded_operation_columns
    assert downgraded == ("completed",)

    command.upgrade(config, "head")
    with sqlite3.connect(database_path) as connection:
        reupgraded = connection.execute(
            "SELECT status, retire_after FROM artifact_storage_operations WHERE id = ?",
            (operation_id,),
        ).fetchone()
    assert reupgraded == ("completed", None)


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
    assert ScriptDirectory.from_config(config).get_current_head() == "20260716_0023"


def test_model_gateway_alias_migration_rewrites_drafts_and_published_versions(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "model-gateway-alias.db"
    config = Config(BACKEND_ROOT / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database_path}")
    command.upgrade(config, "20260713_0015")

    tenant_id = uuid4().hex
    user_id = uuid4().hex
    employee_id = uuid4().hex
    now = "2026-07-14 00:00:00.000000"
    old_model = {"provider": "dashscope", "name": "qwen-plus"}
    definition = {
        "name": "历史员工",
        "work_mode": "autonomous",
        "model": old_model,
    }
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO tenants (id, name, slug, created_at) VALUES (?, ?, ?, ?)",
            (tenant_id, "历史租户", "legacy-model-tenant", now),
        )
        connection.execute(
            "INSERT INTO users "
            "(id, email, password_hash, email_verified, created_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, "legacy@example.com", "hash", 0, now),
        )
        connection.execute(
            "INSERT INTO employees "
            "(id, tenant_id, created_by, name, avatar_url, role_description, visibility, "
            "runtime_type, system_prompt, model_settings, input_schema, output_schema, "
            "capabilities, skill_ids, tool_ids, knowledge_base_ids, approval_policy, "
            "release_strategy, status, published_version, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                employee_id,
                tenant_id,
                user_id,
                "历史员工",
                None,
                "迁移测试",
                "tenant",
                "autonomous",
                "迁移测试",
                json.dumps(old_model),
                "{}",
                "{}",
                json.dumps({"conversation": True}),
                "[]",
                "[]",
                "[]",
                "{}",
                json.dumps({"mode": "all"}),
                "published",
                1,
                now,
                now,
            ),
        )
        connection.execute(
            "INSERT INTO employee_versions "
            "(id, employee_id, tenant_id, version, definition, published_by, published_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (uuid4().hex, employee_id, tenant_id, 1, json.dumps(definition), user_id, now),
        )
        connection.commit()

    command.upgrade(config, "head")

    with sqlite3.connect(database_path) as connection:
        migrated_draft = json.loads(
            connection.execute(
                "SELECT model_settings FROM employees WHERE id = ?", (employee_id,)
            ).fetchone()[0]
        )
        migrated_definition = json.loads(
            connection.execute(
                "SELECT definition FROM employee_versions WHERE employee_id = ?",
                (employee_id,),
            ).fetchone()[0]
        )
        backups = [
            (kind, entity_id, json.loads(original_model))
            for kind, entity_id, original_model in connection.execute(
                "SELECT entity_kind, entity_id, original_model "
                "FROM employee_model_migration_backups ORDER BY entity_kind"
            ).fetchall()
        ]
    expected = {"kind": "gateway_alias", "alias": "general-purpose"}
    assert migrated_draft == expected
    assert migrated_definition["model"] == expected
    assert [(kind, model) for kind, _, model in backups] == [
        ("draft", old_model),
        ("version", old_model),
    ]

    new_employee_id = uuid4().hex
    new_version_id = uuid4().hex
    new_model = {"kind": "gateway_alias", "alias": "general-purpose"}
    new_definition = {**definition, "name": "迁移后员工", "model": new_model}
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO employees "
            "(id, tenant_id, created_by, name, avatar_url, role_description, visibility, "
            "runtime_type, system_prompt, model_settings, input_schema, output_schema, "
            "capabilities, skill_ids, tool_ids, knowledge_base_ids, approval_policy, "
            "release_strategy, status, published_version, created_at, updated_at) "
            "SELECT ?, tenant_id, created_by, ?, avatar_url, role_description, visibility, "
            "runtime_type, system_prompt, ?, input_schema, output_schema, capabilities, "
            "skill_ids, tool_ids, knowledge_base_ids, approval_policy, release_strategy, "
            "status, published_version, created_at, updated_at FROM employees WHERE id = ?",
            (
                new_employee_id,
                "迁移后员工",
                json.dumps(new_model),
                employee_id,
            ),
        )
        connection.execute(
            "INSERT INTO employee_versions "
            "(id, employee_id, tenant_id, version, definition, published_by, published_at) "
            "SELECT ?, ?, tenant_id, version, ?, published_by, published_at "
            "FROM employee_versions WHERE employee_id = ?",
            (
                new_version_id,
                new_employee_id,
                json.dumps(new_definition),
                employee_id,
            ),
        )
        connection.commit()

    command.downgrade(config, "20260713_0015")

    with sqlite3.connect(database_path) as connection:
        restored_draft = json.loads(
            connection.execute(
                "SELECT model_settings FROM employees WHERE id = ?", (employee_id,)
            ).fetchone()[0]
        )
        restored_definition = json.loads(
            connection.execute(
                "SELECT definition FROM employee_versions WHERE employee_id = ?",
                (employee_id,),
            ).fetchone()[0]
        )
        backup_table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name = 'employee_model_migration_backups'"
        ).fetchone()
        downgraded_new_draft = json.loads(
            connection.execute(
                "SELECT model_settings FROM employees WHERE id = ?",
                (new_employee_id,),
            ).fetchone()[0]
        )
        downgraded_new_definition = json.loads(
            connection.execute(
                "SELECT definition FROM employee_versions WHERE id = ?",
                (new_version_id,),
            ).fetchone()[0]
        )
    assert restored_draft == old_model
    assert restored_definition["model"] == old_model
    legacy_fallback = {"provider": "openai", "name": "gpt-5"}
    assert downgraded_new_draft == legacy_fallback
    assert downgraded_new_definition["model"] == legacy_fallback
    assert backup_table is None


def test_model_gateway_alias_migration_uses_uuid_binds_for_postgres() -> None:
    migration = _load_model_gateway_migration()
    employees = migration._employees_table()
    employee_id = uuid4()
    statement = (
        sa.update(employees)
        .where(employees.c.id == employee_id)
        .values(model_settings={"kind": "gateway_alias", "alias": "general-purpose"})
    )

    compiled = statement.compile(dialect=postgresql.dialect())
    id_bind = compiled.binds["id_1"]

    assert isinstance(id_bind.type, sa.Uuid)
    assert id_bind.type.python_type is UUID


def _load_model_gateway_migration() -> ModuleType:
    path = BACKEND_ROOT / "migrations" / "versions" / "20260714_0016_migrate_model_gateway_alias.py"
    spec = spec_from_file_location("model_gateway_alias_migration", path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
