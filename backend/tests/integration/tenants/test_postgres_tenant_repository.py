import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_platform.infrastructure.database.repositories.tenants import (
    SqlAlchemyTenantRepository,
)
from agent_platform.platform.tenants.entities import Tenant

BACKEND_ROOT = Path(__file__).parents[3]


@pytest.fixture(scope="module")
def migrated_postgres_url() -> str:
    database_url = os.getenv("TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("需要 TEST_DATABASE_URL 才运行真实 PostgreSQL 集成测试")

    config = Config(BACKEND_ROOT / "alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    return database_url


@pytest.mark.asyncio
async def test_repository_round_trip_on_postgres(migrated_postgres_url: str) -> None:
    engine = create_async_engine(migrated_postgres_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    slug = f"postgres-{uuid4()}"
    tenant = Tenant.create(name="PostgreSQL 集成企业", slug=slug)

    async with session_factory() as session:
        repository = SqlAlchemyTenantRepository(session)
        await repository.add(tenant)
        assert await repository.get_by_slug(slug) == tenant

    await engine.dispose()
