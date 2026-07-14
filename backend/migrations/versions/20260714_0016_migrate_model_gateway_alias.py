"""将员工模型定义迁移为 provider-neutral gateway alias。"""

from collections.abc import Sequence
from copy import deepcopy
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "20260714_0016"
down_revision: str | None = "20260713_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFAULT_MODEL = {"kind": "gateway_alias", "alias": "general-purpose"}
LEGACY_FALLBACK_MODEL = {"provider": "openai", "name": "gpt-5"}


def upgrade() -> None:
    op.create_table(
        "employee_model_migration_backups",
        sa.Column("entity_kind", sa.String(length=32), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("original_model", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("entity_kind", "entity_id"),
    )
    connection = op.get_bind()
    employees = _employees_table()
    versions = _versions_table()
    backups = _backups_table()

    for employee_id, model_settings in connection.execute(
        sa.select(employees.c.id, employees.c.model_settings)
    ):
        if _is_legacy_model(model_settings):
            connection.execute(
                sa.insert(backups).values(
                    entity_kind="draft",
                    entity_id=employee_id,
                    original_model=model_settings,
                )
            )
            connection.execute(
                sa.update(employees)
                .where(employees.c.id == employee_id)
                .values(model_settings=DEFAULT_MODEL)
            )

    for version_id, definition in connection.execute(
        sa.select(versions.c.id, versions.c.definition)
    ):
        if not isinstance(definition, dict) or not _is_legacy_model(
            definition.get("model")
        ):
            continue
        migrated_definition = deepcopy(definition)
        migrated_definition["model"] = DEFAULT_MODEL
        connection.execute(
            sa.insert(backups).values(
                entity_kind="version",
                entity_id=version_id,
                original_model=definition["model"],
            )
        )
        connection.execute(
            sa.update(versions)
            .where(versions.c.id == version_id)
            .values(definition=migrated_definition)
        )


def downgrade() -> None:
    connection = op.get_bind()
    employees = _employees_table()
    versions = _versions_table()
    backups = _backups_table()

    for entity_kind, entity_id, original_model in connection.execute(
        sa.select(
            backups.c.entity_kind,
            backups.c.entity_id,
            backups.c.original_model,
        )
    ):
        if entity_kind == "draft":
            connection.execute(
                sa.update(employees)
                .where(employees.c.id == entity_id)
                .values(model_settings=original_model)
            )
            continue
        current_definition = connection.execute(
            sa.select(versions.c.definition).where(versions.c.id == entity_id)
        ).scalar_one_or_none()
        if not isinstance(current_definition, dict):
            continue
        restored_definition = deepcopy(current_definition)
        restored_definition["model"] = original_model
        connection.execute(
            sa.update(versions)
            .where(versions.c.id == entity_id)
            .values(definition=restored_definition)
        )

    for employee_id, model_settings in connection.execute(
        sa.select(employees.c.id, employees.c.model_settings)
    ):
        if _is_gateway_model(model_settings):
            connection.execute(
                sa.update(employees)
                .where(employees.c.id == employee_id)
                .values(model_settings=LEGACY_FALLBACK_MODEL)
            )

    for version_id, definition in connection.execute(
        sa.select(versions.c.id, versions.c.definition)
    ):
        if not isinstance(definition, dict) or not _is_gateway_model(
            definition.get("model")
        ):
            continue
        legacy_definition = deepcopy(definition)
        legacy_definition["model"] = LEGACY_FALLBACK_MODEL
        connection.execute(
            sa.update(versions)
            .where(versions.c.id == version_id)
            .values(definition=legacy_definition)
        )

    op.drop_table("employee_model_migration_backups")


def _employees_table() -> sa.TableClause:
    return sa.table(
        "employees",
        sa.column("id", sa.Uuid()),
        sa.column("model_settings", sa.JSON()),
    )


def _versions_table() -> sa.TableClause:
    return sa.table(
        "employee_versions",
        sa.column("id", sa.Uuid()),
        sa.column("definition", sa.JSON()),
    )


def _backups_table() -> sa.TableClause:
    return sa.table(
        "employee_model_migration_backups",
        sa.column("entity_kind", sa.String(length=32)),
        sa.column("entity_id", sa.Uuid()),
        sa.column("original_model", sa.JSON()),
    )


def _is_legacy_model(value: Any) -> bool:
    return isinstance(value, dict) and "provider" in value and "name" in value


def _is_gateway_model(value: Any) -> bool:
    return isinstance(value, dict) and value.get("kind") == "gateway_alias"
