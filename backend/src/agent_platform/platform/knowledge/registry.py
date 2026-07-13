from collections.abc import Iterable

from agent_platform.platform.knowledge.errors import KnowledgeProviderUnavailable
from agent_platform.platform.knowledge.ports import KnowledgeProvider


class KnowledgeProviderRegistry:
    """保存默认供应商，并按知识库持久化的供应商名称进行解析。"""

    def __init__(self, providers: Iterable[KnowledgeProvider]) -> None:
        registered: dict[str, KnowledgeProvider] = {}
        for provider in providers:
            provider_name = provider.provider_name
            if not provider_name or provider_name != provider_name.strip():
                raise ValueError("knowledge provider name must be a non-empty canonical value")
            if provider_name in registered:
                raise ValueError(f"duplicate knowledge provider: {provider_name}")
            registered[provider_name] = provider
        if not registered:
            raise ValueError("at least one knowledge provider is required")
        self._providers = registered
        self._default_provider_name = next(iter(registered))

    @property
    def default_provider(self) -> KnowledgeProvider:
        return self._providers[self._default_provider_name]

    def resolve(self, provider_name: str) -> KnowledgeProvider:
        try:
            return self._providers[provider_name]
        except KeyError as error:
            raise KnowledgeProviderUnavailable("知识库供应商当前不可用") from error
