from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_platform.infrastructure.database.base import Base
from agent_platform.infrastructure.database.models import load_database_models
from agent_platform.infrastructure.database.repositories.tenants import (
    SqlAlchemyTenantRepository,
)
from agent_platform.platform.tenants.entities import Tenant
from agent_platform.platform.tenants.errors import TenantSlugAlreadyExists


@pytest_asyncio.fixture
async def tenant_repository() -> AsyncIterator[SqlAlchemyTenantRepository]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    load_database_models()

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        yield SqlAlchemyTenantRepository(session)

    await engine.dispose()


@pytest.mark.asyncio
async def test_add_and_get_tenant_by_slug(
    tenant_repository: SqlAlchemyTenantRepository,
) -> None:
    tenant = Tenant.create(name="示例企业", slug="example-corp")

    await tenant_repository.add(tenant)

    loaded_tenant = await tenant_repository.get_by_slug("example-corp")
    assert loaded_tenant == tenant


@pytest.mark.asyncio
async def test_duplicate_slug_raises_domain_error(
    tenant_repository: SqlAlchemyTenantRepository,
) -> None:
    await tenant_repository.add(Tenant.create(name="企业一", slug="same-slug"))

    with pytest.raises(TenantSlugAlreadyExists):
        await tenant_repository.add(Tenant.create(name="企业二", slug="same-slug"))
