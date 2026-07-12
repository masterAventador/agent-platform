from typing import Protocol

from agent_platform.platform.tenants.entities import Tenant


class TenantRepository(Protocol):
    """租户持久化端口。"""

    async def add(self, tenant: Tenant) -> None: ...

    async def get_by_slug(self, slug: str) -> Tenant | None: ...
