import pytest

from agent_platform.platform.knowledge.errors import KnowledgeProviderNotConfigured
from agent_platform.platform.knowledge.registry import KnowledgeProviderRegistry


class _StubProvider:
    provider_name = "ragflow"


def test_resolving_an_unregistered_provider_is_a_permanent_configuration_error() -> None:
    registry = KnowledgeProviderRegistry([_StubProvider()])

    with pytest.raises(KnowledgeProviderNotConfigured):
        registry.resolve("unknown-provider")


def test_registered_provider_resolves_normally() -> None:
    provider = _StubProvider()
    registry = KnowledgeProviderRegistry([provider])

    assert registry.resolve("ragflow") is provider
