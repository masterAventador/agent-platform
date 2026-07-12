from datetime import UTC

from agent_platform.platform.tenants.entities import Tenant


def test_create_tenant_normalizes_name_and_slug() -> None:
    tenant = Tenant.create(name="  示例企业  ", slug="  Example-Corp  ")

    assert tenant.name == "示例企业"
    assert tenant.slug == "example-corp"
    assert tenant.created_at.tzinfo is UTC
