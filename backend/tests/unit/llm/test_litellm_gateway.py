import httpx
import pytest
from langchain_openai import ChatOpenAI
from pydantic import SecretStr, ValidationError

from agent_platform.config import AppSettings
from agent_platform.infrastructure.llm.litellm import (
    LiteLLMChatModelFactory,
    LiteLLMGatewayReadinessProbe,
    ModelGatewayConfigurationError,
    ModelGatewayReadinessError,
)


def test_gateway_factory_builds_only_an_internal_openai_compatible_client() -> None:
    factory = LiteLLMChatModelFactory(
        base_url=SecretStr("http://litellm:4000/v1"),
        api_key=SecretStr("internal-gateway-secret"),
        timeout_seconds=90,
        max_retries=3,
    )

    model = factory("general-purpose")

    assert isinstance(model, ChatOpenAI)
    assert model.model_name == "general-purpose"
    assert model.openai_api_base == "http://litellm:4000/v1"
    assert model.openai_api_key is not None
    assert model.openai_api_key.get_secret_value() == "internal-gateway-secret"
    assert model.request_timeout == 90
    assert model.max_retries == 3


def test_gateway_factory_fails_closed_without_leaking_configuration() -> None:
    secret = "gateway-secret-must-not-leak"
    with pytest.raises(ModelGatewayConfigurationError) as captured:
        LiteLLMChatModelFactory(
            base_url=SecretStr(
                "https://user:password@litellm.example/v1?token=leak"
            ),
            api_key=SecretStr(secret),
            timeout_seconds=90,
            max_retries=2,
        )

    assert secret not in repr(captured.value)
    assert "password" not in repr(captured.value)
    assert "token" not in repr(captured.value)

    with pytest.raises(ModelGatewayConfigurationError, match="gateway key"):
        LiteLLMChatModelFactory(
            base_url=SecretStr("http://litellm:4000/v1"),
            api_key=SecretStr(""),
            timeout_seconds=90,
            max_retries=2,
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"llm_gateway_request_timeout_seconds": 0},
        {"llm_gateway_request_timeout_seconds": 3_601},
        {"llm_gateway_max_retries": -1},
        {"llm_gateway_max_retries": 11},
        {"llm_gateway_allowed_aliases": frozenset()},
        {"llm_gateway_allowed_aliases": frozenset({""})},
        {"llm_gateway_readiness_timeout_seconds": 0},
        {"llm_gateway_readiness_timeout_seconds": 31},
    ],
)
def test_gateway_settings_have_bounded_retry_and_timeout(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        AppSettings(**overrides)


def test_gateway_settings_use_provider_neutral_local_defaults() -> None:
    settings = AppSettings()

    assert settings.llm_gateway_url.get_secret_value() == "http://127.0.0.1:4000/v1"
    assert settings.llm_gateway_api_key.get_secret_value() == ""
    assert settings.llm_gateway_request_timeout_seconds == 120
    assert settings.llm_gateway_max_retries == 2
    assert settings.llm_gateway_readiness_timeout_seconds == 5


def test_gateway_url_is_redacted_from_settings_and_validation_errors() -> None:
    sensitive_url = "https://user:password@litellm.example/v1?token=gateway-url-secret"
    settings = AppSettings(llm_gateway_url=sensitive_url)

    assert "password" not in repr(settings)
    assert "gateway-url-secret" not in repr(settings)

    with pytest.raises(ValidationError) as captured:
        AppSettings(
            llm_gateway_url=sensitive_url,
            llm_gateway_max_retries=-1,
        )

    rendered_error = f"{captured.value!r}\n{captured.value}"
    assert "password" not in rendered_error
    assert "gateway-url-secret" not in rendered_error


@pytest.mark.asyncio
async def test_gateway_readiness_uses_models_endpoint_and_internal_key() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"data": [{"id": "general-purpose", "object": "model"}]},
        )

    probe = LiteLLMGatewayReadinessProbe(
        base_url=SecretStr("http://litellm:4000/v1"),
        api_key=SecretStr("internal-gateway-secret"),
        timeout_seconds=5,
        transport=httpx.MockTransport(respond),
    )

    await probe.assert_ready(frozenset({"general-purpose"}))

    assert len(requests) == 1
    assert requests[0].method == "GET"
    assert requests[0].url == "http://litellm:4000/v1/models"
    assert requests[0].headers["Authorization"] == "Bearer internal-gateway-secret"


@pytest.mark.asyncio
async def test_gateway_readiness_fails_closed_when_alias_is_not_advertised() -> None:
    probe = LiteLLMGatewayReadinessProbe(
        base_url=SecretStr("http://litellm:4000/v1"),
        api_key=SecretStr("internal-gateway-secret"),
        timeout_seconds=5,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"data": []})
        ),
    )

    with pytest.raises(ModelGatewayReadinessError, match="required model aliases"):
        await probe.assert_ready(frozenset({"general-purpose"}))


@pytest.mark.asyncio
async def test_gateway_readiness_redacts_an_upstream_auth_failure() -> None:
    upstream_secret = "upstream-auth-detail-must-not-leak"
    probe = LiteLLMGatewayReadinessProbe(
        base_url=SecretStr("http://litellm:4000/v1"),
        api_key=SecretStr("invalid-internal-key"),
        timeout_seconds=5,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                401,
                json={"detail": upstream_secret},
            )
        ),
    )

    with pytest.raises(ModelGatewayReadinessError) as captured:
        await probe.assert_ready(frozenset({"general-purpose"}))

    assert upstream_secret not in repr(captured.value)
