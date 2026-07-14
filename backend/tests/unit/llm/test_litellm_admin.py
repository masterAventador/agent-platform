from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import httpx
import pytest
from pydantic import SecretStr

from agent_platform.infrastructure.llm.admin import (
    LiteLLMAdminClient,
    LiteLLMAdminConfigurationError,
    LiteLLMAdminError,
    LiteLLMAdminOutcomeUnknown,
    LiteLLMAdminValidationError,
)
from agent_platform.platform.model_gateway.entities import (
    MAX_BUDGET_MICROUSD,
    MAX_SIGNED_INT32,
)

TENANT_ID = UUID("2efea627-2d99-4b8e-a7de-252c742b245b")
MASTER_KEY = "sk-master-must-never-leak"
RAW_KEY = "sk-A7z_2Qp9Lm4Nx8Vr1Tc6Yw3Ke0Hs5Jd2Bf9Ug7Pi4Ro"
KEY_HASH = hashlib.sha256(RAW_KEY.encode()).hexdigest()
TENANT_USER_ID = f"agent-platform:tenant:{TENANT_ID}"
ALLOWED_ROUTES = (
    "/chat/completions",
    "/models",
    "/v1/chat/completions",
    "/v1/models",
)


def _client(transport: httpx.AsyncBaseTransport) -> LiteLLMAdminClient:
    return LiteLLMAdminClient(
        base_url=SecretStr("http://litellm:4000"),
        master_key=SecretStr(MASTER_KEY),
        timeout_seconds=5,
        transport=transport,
    )


def _json(request: httpx.Request) -> dict[str, object]:
    parsed = json.loads(request.content, parse_float=Decimal)
    assert isinstance(parsed, dict)
    return parsed


@pytest.mark.asyncio
async def test_ensure_tenant_aggregate_creates_then_verifies_public_user() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET" and len(requests) == 1:
            return httpx.Response(404, json={"detail": "not found"})
        if request.method == "POST":
            return httpx.Response(200, json={"user_id": TENANT_USER_ID})
        return httpx.Response(
            200,
            json={
                "user_id": TENANT_USER_ID,
                "user_info": {
                    "max_budget": 12.345678,
                    "budget_duration": "30d",
                    "rpm_limit": 7,
                    "tpm_limit": 9000,
                    "max_parallel_requests": 3,
                    "models": ["general-purpose"],
                },
                "keys": [],
                "teams": [],
            },
        )

    aggregate = await _client(httpx.MockTransport(respond)).ensure_tenant_aggregate(
        TENANT_ID,
        max_budget_microusd=12_345_678,
        budget_duration="30d",
        rpm_limit=7,
        tpm_limit=9000,
        max_parallel_requests=3,
        models=("general-purpose",),
    )

    assert [(request.method, request.url.path) for request in requests] == [
        ("GET", "/user/info"),
        ("POST", "/user/new"),
        ("GET", "/user/info"),
    ]
    assert dict(requests[0].url.params) == {"user_id": TENANT_USER_ID}
    assert _json(requests[1]) == {
        "user_id": TENANT_USER_ID,
        "auto_create_key": False,
        "max_budget": Decimal("12.345678"),
        "budget_duration": "30d",
        "rpm_limit": 7,
        "tpm_limit": 9000,
        "max_parallel_requests": 3,
        "models": ["general-purpose"],
    }
    assert aggregate.tenant_id == str(TENANT_ID)
    assert aggregate.max_budget_usd == Decimal("12.345678")
    for request in requests:
        assert request.headers["Authorization"] == f"Bearer {MASTER_KEY}"
        assert MASTER_KEY not in str(request.url)


@pytest.mark.asyncio
async def test_ensure_tenant_aggregate_updates_drift_then_strictly_verifies() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET" and len(requests) == 1:
            return httpx.Response(
                200,
                json={
                    "user_id": TENANT_USER_ID,
                    "user_info": {
                        "max_budget": 1,
                        "budget_duration": "1d",
                        "rpm_limit": 1,
                        "tpm_limit": 1,
                        "max_parallel_requests": 1,
                        "models": ["general-purpose"],
                    },
                },
            )
        if request.method == "POST":
            return httpx.Response(200, json={"user_id": TENANT_USER_ID})
        return httpx.Response(
            200,
            json={
                "user_id": TENANT_USER_ID,
                "user_info": {
                    "max_budget": 2,
                    "budget_duration": "30d",
                    "rpm_limit": 8,
                    "tpm_limit": 10000,
                    "max_parallel_requests": 4,
                    "models": ["general-purpose"],
                },
            },
        )

    aggregate = await _client(httpx.MockTransport(respond)).ensure_tenant_aggregate(
        TENANT_ID,
        max_budget_microusd=2_000_000,
        budget_duration="30d",
        rpm_limit=8,
        tpm_limit=10000,
        max_parallel_requests=4,
        models=("general-purpose",),
    )

    assert [(request.method, request.url.path) for request in requests] == [
        ("GET", "/user/info"),
        ("POST", "/user/update"),
        ("GET", "/user/info"),
    ]
    assert _json(requests[1]) == {
        "user_id": TENANT_USER_ID,
        "max_budget": Decimal("2"),
        "budget_duration": "30d",
        "rpm_limit": 8,
        "tpm_limit": 10000,
        "max_parallel_requests": 4,
        "models": ["general-purpose"],
    }
    assert aggregate.max_budget_usd == Decimal("2")


@pytest.mark.asyncio
async def test_generate_blocked_key_sends_raw_once_and_verifies_by_hash() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET" and len(requests) == 1:
            return httpx.Response(200, json={"keys": [], "total_count": 0})
        if request.method == "POST":
            return httpx.Response(200, json={})
        return httpx.Response(
            200,
            json={
                "keys": [
                    {
                        "token": KEY_HASH,
                        "key_alias": "tenant-2efea627-v1",
                        "user_id": TENANT_USER_ID,
                        "models": ["general-purpose"],
                        "allowed_routes": list(ALLOWED_ROUTES),
                        "blocked": True,
                    }
                ],
                "total_count": 1,
                "current_page": 1,
                "total_pages": 1,
            },
        )

    record = await _client(httpx.MockTransport(respond)).generate_blocked_key(
        TENANT_ID,
        raw_key=SecretStr(RAW_KEY),
        key_alias="tenant-2efea627-v1",
        models=("general-purpose",),
        allowed_routes=tuple(reversed(ALLOWED_ROUTES)),
    )

    assert [(request.method, request.url.path) for request in requests] == [
        ("GET", "/key/list"),
        ("POST", "/key/generate"),
        ("GET", "/key/list"),
    ]
    assert _json(requests[1]) == {
        "key": RAW_KEY,
        "key_alias": "tenant-2efea627-v1",
        "user_id": TENANT_USER_ID,
        "models": ["general-purpose"],
        "allowed_routes": list(ALLOWED_ROUTES),
        "blocked": True,
    }
    assert dict(requests[2].url.params) == {
        "key_hash": KEY_HASH,
        "return_full_object": "true",
    }
    assert RAW_KEY not in str(requests[1].url)
    assert RAW_KEY not in str(requests[1].headers)
    assert record.key_hash == KEY_HASH
    assert record.blocked is True
    assert RAW_KEY not in repr(record)


@pytest.mark.asyncio
async def test_key_lifecycle_management_is_hash_only() -> None:
    requests: list[httpx.Request] = []
    blocked = False
    deleted = False

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal blocked, deleted
        requests.append(request)
        if request.method == "POST":
            if request.url.path == "/key/block":
                blocked = True
            elif request.url.path == "/key/unblock":
                blocked = False
            else:
                deleted = True
            return httpx.Response(200, json={"malicious": "ignored"})
        if deleted:
            return httpx.Response(200, json={"keys": [], "total_count": 0})
        return httpx.Response(
            200,
            json={
                "keys": [
                    {
                        "token": KEY_HASH,
                        "key_alias": "tenant-2efea627-v1",
                        "user_id": TENANT_USER_ID,
                        "models": ["general-purpose"],
                        "allowed_routes": list(ALLOWED_ROUTES),
                        "blocked": blocked,
                    }
                ],
                "total_count": 1,
            },
        )

    client = _client(httpx.MockTransport(respond))
    await client.block_key(TENANT_ID, KEY_HASH)
    await client.unblock_key(TENANT_ID, KEY_HASH)
    await client.delete_key(TENANT_ID, KEY_HASH)

    mutations = [request for request in requests if request.method == "POST"]
    assert [(request.url.path, _json(request)) for request in mutations] == [
        ("/key/block", {"key": KEY_HASH}),
        ("/key/unblock", {"key": KEY_HASH}),
        ("/key/delete", {"keys": [KEY_HASH]}),
    ]
    assert all(RAW_KEY not in request.content.decode() for request in requests)


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["block_key", "unblock_key", "delete_key"])
async def test_key_mutation_never_posts_for_wrong_tenant(action: str) -> None:
    requests: list[httpx.Request] = []
    other = UUID("4892992f-0058-4ec4-80ab-00024f582947")

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "keys": [
                    {
                        "token": KEY_HASH,
                        "key_alias": "tenant-2efea627-v1",
                        "user_id": f"agent-platform:tenant:{other}",
                        "models": ["general-purpose"],
                        "allowed_routes": list(ALLOWED_ROUTES),
                        "blocked": False,
                    }
                ],
                "total_count": 1,
            },
        )

    client = _client(httpx.MockTransport(respond))
    with pytest.raises(LiteLLMAdminError):
        await getattr(client, action)(TENANT_ID, KEY_HASH)

    assert [request.method for request in requests] == ["GET"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"keys": []},
        {"keys": [], "total_count": 1},
        {
            "keys": [
                {
                    "token": KEY_HASH,
                    "key_alias": "tenant-2efea627-v1",
                    "user_id": TENANT_USER_ID,
                    "models": ["general-purpose"],
                    "allowed_routes": list(ALLOWED_ROUTES),
                    "blocked": True,
                }
            ],
            "total_count": 0,
        },
        {"keys": [{}, {}], "total_count": 2},
        {"keys": [], "total_count": True},
    ],
)
async def test_get_key_fails_closed_on_invalid_list_cardinality(
    payload: dict[str, object],
) -> None:
    client = _client(httpx.MockTransport(lambda request: httpx.Response(200, json=payload)))

    with pytest.raises(LiteLLMAdminError) as captured:
        await client.get_key(KEY_HASH)

    assert captured.value.stage == "get_key"
    assert captured.value.status_code == 200


@pytest.mark.asyncio
async def test_get_key_returns_none_only_for_explicit_zero_count() -> None:
    client = _client(
        httpx.MockTransport(
            lambda request: httpx.Response(200, json={"keys": [], "total_count": 0})
        )
    )

    assert await client.get_key(KEY_HASH) is None


@pytest.mark.asyncio
async def test_generate_timeout_is_distinguishable_for_hash_reconciliation() -> None:
    calls = 0

    def timeout(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(200, json={"keys": [], "total_count": 0})
        if calls == 2:
            raise httpx.ReadTimeout("transport detail containing " + RAW_KEY, request=request)
        return httpx.Response(200, json={"keys": [], "total_count": 0})

    with pytest.raises(LiteLLMAdminOutcomeUnknown) as captured:
        await _client(httpx.MockTransport(timeout)).generate_blocked_key(
            TENANT_ID,
            raw_key=SecretStr(RAW_KEY),
            key_alias="tenant-2efea627-v1",
            models=("general-purpose",),
            allowed_routes=ALLOWED_ROUTES,
        )

    error = captured.value
    assert error.stage == "generate_key"
    assert error.status_code is None
    assert error.key_hash == KEY_HASH
    assert error.outcome_unknown is True
    rendered = f"{error!r}\n{error}"
    assert RAW_KEY not in rendered
    assert MASTER_KEY not in rendered
    assert "transport detail" not in rendered


@pytest.mark.asyncio
async def test_generate_409_reconciles_to_exact_hash_state() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(200, json={"keys": [], "total_count": 0})
        if request.method == "POST":
            return httpx.Response(409, json={"detail": RAW_KEY})
        return httpx.Response(
            200,
            json={
                "keys": [
                    {
                        "token": KEY_HASH,
                        "key_alias": "tenant-2efea627-v1",
                        "user_id": TENANT_USER_ID,
                        "models": ["general-purpose"],
                        "allowed_routes": list(ALLOWED_ROUTES),
                        "blocked": True,
                    }
                ],
                "total_count": 1,
            },
        )

    record = await _client(httpx.MockTransport(respond)).generate_blocked_key(
        TENANT_ID,
        raw_key=SecretStr(RAW_KEY),
        key_alias="tenant-2efea627-v1",
        models=("general-purpose",),
        allowed_routes=ALLOWED_ROUTES,
    )

    assert record.key_hash == KEY_HASH
    assert [request.method for request in requests] == ["GET", "POST", "GET"]


@pytest.mark.asyncio
async def test_generate_existing_mismatch_fails_closed_without_post() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "keys": [
                    {
                        "token": KEY_HASH,
                        "key_alias": "tenant-2efea627-other",
                        "user_id": TENANT_USER_ID,
                        "models": ["general-purpose"],
                        "allowed_routes": list(ALLOWED_ROUTES),
                        "blocked": True,
                    }
                ],
                "total_count": 1,
            },
        )

    with pytest.raises(LiteLLMAdminError):
        await _client(httpx.MockTransport(respond)).generate_blocked_key(
            TENANT_ID,
            raw_key=SecretStr(RAW_KEY),
            key_alias="tenant-2efea627-v1",
            models=("general-purpose",),
            allowed_routes=ALLOWED_ROUTES,
        )

    assert [request.method for request in requests] == ["GET"]


@pytest.mark.asyncio
async def test_create_tenant_503_reconciles_by_namespaced_user() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(404, json={})
        if request.method == "POST":
            return httpx.Response(503, json={"detail": MASTER_KEY})
        return httpx.Response(
            200,
            json={
                "user_id": TENANT_USER_ID,
                "user_info": {
                    "max_budget": 1,
                    "budget_duration": "30d",
                    "rpm_limit": 1,
                    "tpm_limit": 1,
                    "max_parallel_requests": 1,
                    "models": ["general-purpose"],
                },
            },
        )

    aggregate = await _client(httpx.MockTransport(respond)).ensure_tenant_aggregate(
        TENANT_ID,
        max_budget_microusd=1_000_000,
        budget_duration="30d",
        rpm_limit=1,
        tpm_limit=1,
        max_parallel_requests=1,
        models=("general-purpose",),
    )

    assert aggregate.tenant_id == str(TENANT_ID)
    assert [request.method for request in requests] == ["GET", "POST", "GET"]


@pytest.mark.asyncio
@pytest.mark.parametrize("user_id", [str(TENANT_ID), f"other-app:tenant:{TENANT_ID}"])
async def test_get_key_rejects_unscoped_or_foreign_user_namespace(user_id: str) -> None:
    client = _client(
        httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "keys": [
                        {
                            "token": KEY_HASH,
                            "key_alias": "tenant-2efea627-v1",
                            "user_id": user_id,
                            "models": ["general-purpose"],
                            "allowed_routes": list(ALLOWED_ROUTES),
                            "blocked": True,
                        }
                    ],
                    "total_count": 1,
                },
            )
        )
    )

    with pytest.raises(LiteLLMAdminError):
        await client.get_key(KEY_HASH)


@pytest.mark.asyncio
async def test_http_and_payload_errors_are_stable_and_redacted() -> None:
    upstream_detail = f"provider=secret-model key={RAW_KEY} master={MASTER_KEY}"
    calls = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                200,
                json={
                    "keys": [
                        {
                            "token": KEY_HASH,
                            "key_alias": "tenant-2efea627-v1",
                            "user_id": TENANT_USER_ID,
                            "models": ["general-purpose"],
                            "allowed_routes": list(ALLOWED_ROUTES),
                            "blocked": False,
                        }
                    ],
                    "total_count": 1,
                },
            )
        return httpx.Response(401, json={"detail": upstream_detail})

    client = _client(httpx.MockTransport(respond))

    with pytest.raises(LiteLLMAdminError) as captured:
        await client.block_key(TENANT_ID, KEY_HASH)

    error = captured.value
    assert error.stage == "block_key"
    assert error.status_code == 401
    assert error.outcome_unknown is False
    rendered = f"{error!r}\n{error}"
    assert upstream_detail not in rendered
    assert RAW_KEY not in rendered
    assert MASTER_KEY not in rendered


@pytest.mark.asyncio
async def test_spend_page_uses_tenant_filter_and_returns_redacted_usage() -> None:
    captured_request: httpx.Request | None = None

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal captured_request
        captured_request = request
        return httpx.Response(
            200,
            content=json.dumps(
                {
                    "data": [
                        {
                            "request_id": "req-1",
                            "api_key": KEY_HASH,
                            "user": TENANT_USER_ID,
                            "spend": 0.00000125,
                            "prompt_tokens": 10,
                            "completion_tokens": 5,
                            "total_tokens": 15,
                            "status": "success",
                            "model": "real-provider/model-secret",
                            "custom_llm_provider": "provider-secret",
                            "metadata": {"raw": "must-not-pass-through"},
                        }
                    ],
                    "total": 3,
                    "page": 2,
                    "page_size": 2,
                    "total_pages": 2,
                }
            ).encode(),
            headers={"content-type": "application/json"},
        )

    page = await _client(httpx.MockTransport(respond)).get_tenant_spend_page(
        TENANT_ID,
        start=datetime(2026, 7, 1, tzinfo=UTC),
        end=datetime(2026, 7, 2, 12, 30, tzinfo=UTC),
        page=2,
        page_size=2,
    )

    assert captured_request is not None
    assert captured_request.url.path == "/spend/logs/v2"
    assert dict(captured_request.url.params) == {
        "user_id": TENANT_USER_ID,
        "start_date": "2026-07-01 00:00:00",
        "end_date": "2026-07-02 12:30:00",
        "page": "2",
        "page_size": "2",
    }
    assert page.total == 3
    assert page.total_pages == 2
    assert page.usage[0].spend_usd == Decimal("0.00000125")
    assert page.usage[0].input_tokens == 10
    assert page.usage[0].output_tokens == 5
    rendered = repr(page)
    assert "real-provider" not in rendered
    assert "provider-secret" not in rendered
    assert "must-not-pass-through" not in rendered


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("metadata", "item_count", "total_tokens"),
    [
        ({"page": 2}, 1, 15),
        ({"page_size": 3}, 1, 15),
        ({"total_pages": 2}, 1, 15),
        ({"total": 0, "total_pages": 0}, 1, 15),
        ({"total": 3, "total_pages": 2}, 1, 15),
        ({}, 1, 16),
        ({"total": 3, "total_pages": 2}, 3, 15),
    ],
)
async def test_spend_page_fails_closed_on_malicious_200(
    metadata: dict[str, int],
    item_count: int,
    total_tokens: int,
) -> None:
    item = {
        "request_id": "req-1",
        "api_key": KEY_HASH,
        "user": TENANT_USER_ID,
        "spend": 0,
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": total_tokens,
        "status": "success",
    }
    payload: dict[str, object] = {
        "data": [item.copy() for _ in range(item_count)],
        "total": 1,
        "page": 1,
        "page_size": 2,
        "total_pages": 1,
    }
    payload.update(metadata)
    client = _client(httpx.MockTransport(lambda request: httpx.Response(200, json=payload)))

    with pytest.raises(LiteLLMAdminError) as captured:
        await client.get_tenant_spend_page(
            TENANT_ID,
            start=datetime(2026, 7, 1, tzinfo=UTC),
            end=datetime(2026, 7, 2, tzinfo=UTC),
            page=1,
            page_size=2,
        )

    assert captured.value.stage == "list_spend"


@pytest.mark.asyncio
async def test_configuration_and_input_validation_fail_without_secrets() -> None:
    with pytest.raises(LiteLLMAdminConfigurationError) as captured:
        LiteLLMAdminClient(
            base_url=SecretStr("https://user:password@litellm.example?token=leak"),
            master_key=SecretStr(MASTER_KEY),
            timeout_seconds=5,
        )
    rendered = f"{captured.value!r}\n{captured.value}"
    assert "password" not in rendered
    assert "token=leak" not in rendered
    assert MASTER_KEY not in rendered

    client = _client(httpx.MockTransport(lambda request: httpx.Response(200)))
    client_rendered = repr(client)
    assert MASTER_KEY not in client_rendered
    assert "litellm:4000" not in client_rendered

    with pytest.raises(LiteLLMAdminValidationError):
        await client.block_key(TENANT_ID, "A" * 64)
    with pytest.raises(LiteLLMAdminValidationError):
        await client.generate_blocked_key(
            str(TENANT_ID).upper(),
            raw_key=SecretStr(RAW_KEY),
            key_alias="tenant-2efea627-v1",
            models=("general-purpose",),
            allowed_routes=ALLOWED_ROUTES,
        )
    with pytest.raises(LiteLLMAdminValidationError):
        await client.generate_blocked_key(
            TENANT_ID,
            raw_key=SecretStr("sk-short"),
            key_alias="tenant-2efea627-v1",
            models=("general-purpose",),
            allowed_routes=ALLOWED_ROUTES,
        )
    with pytest.raises(LiteLLMAdminValidationError):
        await client.generate_blocked_key(
            TENANT_ID,
            raw_key=SecretStr("sk-" + "a" * 48),
            key_alias="tenant-2efea627-v1",
            models=("general-purpose",),
            allowed_routes=ALLOWED_ROUTES,
        )
    with pytest.raises(LiteLLMAdminValidationError):
        await client.generate_blocked_key(
            TENANT_ID,
            raw_key=SecretStr(RAW_KEY),
            key_alias="",
            models=("general-purpose",),
            allowed_routes=ALLOWED_ROUTES,
        )
    with pytest.raises(LiteLLMAdminValidationError):
        await client.generate_blocked_key(
            TENANT_ID,
            raw_key=SecretStr(RAW_KEY),
            key_alias="tenant-2efea627-v1",
            models=(),
            allowed_routes=ALLOWED_ROUTES,
        )
    with pytest.raises(LiteLLMAdminValidationError):
        await client.generate_blocked_key(
            TENANT_ID,
            raw_key=SecretStr(RAW_KEY),
            key_alias="tenant-2efea627-v1",
            models=("openai/provider-model",),
            allowed_routes=ALLOWED_ROUTES,
        )
    with pytest.raises(LiteLLMAdminValidationError):
        await client.generate_blocked_key(
            TENANT_ID,
            raw_key=SecretStr(RAW_KEY),
            key_alias="tenant-2efea627-v1",
            models=("General-Purpose",),
            allowed_routes=ALLOWED_ROUTES,
        )
    with pytest.raises(LiteLLMAdminValidationError):
        await client.generate_blocked_key(
            TENANT_ID,
            raw_key=SecretStr(RAW_KEY),
            key_alias="tenant-2efea627-v1",
            models=("a" * 65,),
            allowed_routes=ALLOWED_ROUTES,
        )
    with pytest.raises(LiteLLMAdminValidationError):
        await client.generate_blocked_key(
            TENANT_ID,
            raw_key=SecretStr(RAW_KEY),
            key_alias="tenant-2efea627-v1",
            models=("other",),
            allowed_routes=ALLOWED_ROUTES,
        )
    with pytest.raises(LiteLLMAdminValidationError):
        await client.generate_blocked_key(
            TENANT_ID,
            raw_key=SecretStr(RAW_KEY),
            key_alias="tenant-2efea627-v1",
            models=("general-purpose",),
            allowed_routes=("/key/generate",),
        )
    with pytest.raises(LiteLLMAdminValidationError):
        await client.ensure_tenant_aggregate(
            TENANT_ID,
            max_budget_microusd=True,
            budget_duration="30d",
            rpm_limit=1,
            tpm_limit=1,
            max_parallel_requests=1,
            models=("general-purpose",),
        )
    for invalid_budget in (0, -1, MAX_BUDGET_MICROUSD + 1):
        with pytest.raises(LiteLLMAdminValidationError):
            await client.ensure_tenant_aggregate(
                TENANT_ID,
                max_budget_microusd=invalid_budget,
                budget_duration="30d",
                rpm_limit=1,
                tpm_limit=1,
                max_parallel_requests=1,
                models=("general-purpose",),
            )
    with pytest.raises(LiteLLMAdminValidationError):
        await client.ensure_tenant_aggregate(
            TENANT_ID,
            max_budget_microusd=1,
            budget_duration="30d",
            rpm_limit=MAX_SIGNED_INT32 + 1,
            tpm_limit=1,
            max_parallel_requests=1,
            models=("general-purpose",),
        )
