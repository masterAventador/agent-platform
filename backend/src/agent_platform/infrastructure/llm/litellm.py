from __future__ import annotations

from collections.abc import Awaitable
from time import perf_counter
from typing import Protocol
from urllib.parse import urlsplit, urlunsplit

import httpx
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from agent_platform.observability.metrics import OperationalComponent, OperationalMetrics


class ModelGatewayConfigurationError(ValueError):
    """内部模型网关配置无效；错误文本不得包含凭据或原始 URL。"""


class ModelGatewayReadinessError(RuntimeError):
    """内部模型网关未就绪；错误文本不得包含响应或凭据。"""


class ModelGatewayReadiness(Protocol):
    def assert_ready(self, aliases: frozenset[str]) -> Awaitable[None]: ...


class LiteLLMChatModelFactory:
    """为 provider-neutral alias 构造 OpenAI-compatible LiteLLM 客户端。"""

    def __init__(
        self,
        *,
        base_url: SecretStr,
        timeout_seconds: float,
        max_retries: int,
    ) -> None:
        self._base_url = self._validate_base_url(base_url.get_secret_value())
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries

    def __call__(self, alias: str, api_key: SecretStr) -> BaseChatModel:
        # C16：凭据按租户传入，工厂不再持有应用级共享 Key，网关调用因此可归因。
        return ChatOpenAI(
            model=alias,
            base_url=self._base_url,
            api_key=_require_gateway_key_secret(api_key),
            timeout=self._timeout_seconds,
            max_retries=self._max_retries,
        )

    @staticmethod
    def _validate_base_url(value: str) -> str:
        return _validate_gateway_base_url(value)


class LiteLLMGatewayReadinessProbe:
    """用无计费的 OpenAI-compatible models endpoint 检查网关和 alias。"""

    def __init__(
        self,
        *,
        base_url: SecretStr,
        api_key: SecretStr,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
        metrics: OperationalMetrics | None = None,
    ) -> None:
        self._base_url = _validate_gateway_base_url(base_url.get_secret_value())
        self._api_key = _require_gateway_key(api_key)
        self._timeout_seconds = timeout_seconds
        self._transport = transport
        self._metrics = metrics

    async def assert_ready(self, aliases: frozenset[str]) -> None:
        started = perf_counter()
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_seconds,
                transport=self._transport,
            ) as client:
                response = await client.get(
                    f"{self._base_url}/models",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                )
                response.raise_for_status()
                payload = response.json()
            advertised = {
                item["id"]
                for item in payload["data"]
                if isinstance(item, dict) and isinstance(item.get("id"), str)
            }
        except (httpx.HTTPError, KeyError, TypeError, ValueError):
            self._record_metric("failed", started)
            raise ModelGatewayReadinessError("model gateway readiness check failed") from None
        if not aliases.issubset(advertised):
            self._record_metric("failed", started)
            raise ModelGatewayReadinessError(
                "model gateway does not advertise all required model aliases"
            )
        self._record_metric("succeeded", started)

    def _record_metric(self, outcome: str, started: float) -> None:
        if self._metrics is not None:
            self._metrics.record(
                component=OperationalComponent.MODEL_GATEWAY,
                operation="readiness",
                outcome=outcome,
                duration_ms=(perf_counter() - started) * 1_000,
            )


def _require_gateway_key(api_key: SecretStr) -> str:
    value = api_key.get_secret_value()
    if not value:
        raise ModelGatewayConfigurationError("model gateway key is required")
    return value


def _require_gateway_key_secret(api_key: SecretStr) -> SecretStr:
    if not isinstance(api_key, SecretStr) or not api_key.get_secret_value():
        raise ModelGatewayConfigurationError("model gateway key is required")
    return api_key


def _validate_gateway_base_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path.rstrip("/") != "/v1"
        ):
            raise ValueError
        host = parsed.hostname
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        netloc = host if parsed.port is None else f"{host}:{parsed.port}"
        return urlunsplit((parsed.scheme, netloc, "/v1", "", ""))
    except (TypeError, ValueError):
        raise ModelGatewayConfigurationError(
            "model gateway URL must be an http(s) /v1 endpoint without credentials"
        ) from None
