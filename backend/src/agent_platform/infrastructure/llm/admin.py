from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

import httpx
from pydantic import SecretStr

from agent_platform.platform.model_gateway.entities import (
    MAX_BUDGET_MICROUSD,
    MAX_SIGNED_INT32,
)
from agent_platform.platform.models import DEFAULT_MODEL_ALIASES, validate_gateway_alias

_KEY_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_KEY_ALIAS_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,125}[a-z0-9]$")
_RAW_KEY_PATTERN = re.compile(r"^sk-[A-Za-z0-9_-]{32,}$")
_BUDGET_DURATION_PATTERN = re.compile(r"^[1-9][0-9]*(?:s|m|h|d|mo)$")
_TENANT_USER_PREFIX = "agent-platform:tenant:"
_TENANT_ROUTES = frozenset(
    {
        "/chat/completions",
        "/v1/chat/completions",
        "/models",
        "/v1/models",
    }
)
_UNCERTAIN_MUTATION_STATUSES = frozenset({408, 409, 425, 429})
_MICROUSD_PER_USD = Decimal(1_000_000)


class LiteLLMAdminConfigurationError(ValueError):
    """LiteLLM 管理客户端配置无效；错误不得包含原始配置。"""


class LiteLLMAdminValidationError(ValueError):
    """管理操作输入无效；错误不得回显调用者输入。"""


class LiteLLMAdminError(RuntimeError):
    """LiteLLM 公开管理 HTTP API 调用失败。"""

    def __init__(
        self,
        *,
        stage: str,
        status_code: int | None,
        outcome_unknown: bool = False,
        key_hash: str | None = None,
    ) -> None:
        self.stage = stage
        self.status_code = status_code
        self.outcome_unknown = outcome_unknown
        self.key_hash = key_hash
        status = "transport" if status_code is None else str(status_code)
        outcome = "unknown" if outcome_unknown else "known"
        super().__init__(
            f"litellm admin request failed: stage={stage} status={status} outcome={outcome}"
        )


class LiteLLMAdminOutcomeUnknown(LiteLLMAdminError):
    """写请求可能已被 LiteLLM 接受；调用者必须读后收敛。"""

    def __init__(
        self,
        *,
        stage: str,
        key_hash: str | None = None,
    ) -> None:
        super().__init__(
            stage=stage,
            status_code=None,
            outcome_unknown=True,
            key_hash=key_hash,
        )


@dataclass(frozen=True, slots=True)
class TenantAggregate:
    tenant_id: str
    max_budget_usd: Decimal
    budget_duration: str
    rpm_limit: int
    tpm_limit: int
    max_parallel_requests: int
    models: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class VirtualKeyRecord:
    key_hash: str
    tenant_id: str
    key_alias: str
    models: tuple[str, ...]
    allowed_routes: tuple[str, ...]
    blocked: bool


@dataclass(frozen=True, slots=True)
class RedactedSpendUsage:
    request_id: str
    key_hash: str
    tenant_id: str
    spend_usd: Decimal
    input_tokens: int
    output_tokens: int
    total_tokens: int
    status: str


@dataclass(frozen=True, slots=True)
class RedactedSpendPage:
    usage: tuple[RedactedSpendUsage, ...]
    total: int
    page: int
    page_size: int
    total_pages: int


class LiteLLMAdminClient:
    """仅通过 LiteLLM 公开 HTTP 路由管理租户 user、virtual key 与 spend。"""

    def __init__(
        self,
        *,
        base_url: SecretStr,
        master_key: SecretStr,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = _validate_base_url(base_url)
        _validate_master_key(master_key)
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise LiteLLMAdminConfigurationError("invalid litellm admin timeout")
        self._master_key = master_key
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    async def ensure_tenant_aggregate(
        self,
        tenant_id: UUID | str,
        *,
        max_budget_microusd: int,
        budget_duration: str,
        rpm_limit: int,
        tpm_limit: int,
        max_parallel_requests: int,
        models: Sequence[str],
    ) -> TenantAggregate:
        canonical_tenant_id = _canonical_tenant_id(tenant_id)
        max_budget_usd = _microusd_to_usd(max_budget_microusd)
        checked_duration = _budget_duration(budget_duration)
        checked_rpm = _positive_int(rpm_limit, field="rpm_limit")
        checked_tpm = _positive_int(tpm_limit, field="tpm_limit")
        checked_parallel = _positive_int(max_parallel_requests, field="max_parallel_requests")
        checked_models = _model_aliases(models)
        expected = TenantAggregate(
            tenant_id=canonical_tenant_id,
            max_budget_usd=max_budget_usd,
            budget_duration=checked_duration,
            rpm_limit=checked_rpm,
            tpm_limit=checked_tpm,
            max_parallel_requests=checked_parallel,
            models=checked_models,
        )
        tenant_user_id = _tenant_user_id(canonical_tenant_id)

        existing = await self._get_tenant_aggregate(
            canonical_tenant_id,
            allow_missing=True,
        )
        if existing is None:
            stage = "create_tenant"
            body: dict[str, object] = {
                "user_id": tenant_user_id,
                "auto_create_key": False,
                "max_budget": max_budget_usd,
                "budget_duration": checked_duration,
                "rpm_limit": checked_rpm,
                "tpm_limit": checked_tpm,
                "max_parallel_requests": checked_parallel,
                "models": list(checked_models),
            }
        elif existing != expected:
            stage = "update_tenant"
            body = {
                "user_id": tenant_user_id,
                "max_budget": max_budget_usd,
                "budget_duration": checked_duration,
                "rpm_limit": checked_rpm,
                "tpm_limit": checked_tpm,
                "max_parallel_requests": checked_parallel,
                "models": list(checked_models),
            }
        else:
            return existing

        try:
            await self._request(
                "POST",
                "/user/new" if stage == "create_tenant" else "/user/update",
                stage=stage,
                json_body=body,
                outcome_unknown=True,
            )
        except LiteLLMAdminOutcomeUnknown:
            try:
                existing = await self._get_tenant_aggregate(
                    canonical_tenant_id,
                    allow_missing=True,
                )
            except LiteLLMAdminError:
                raise LiteLLMAdminOutcomeUnknown(stage=stage) from None
            if existing == expected:
                return existing
            raise LiteLLMAdminOutcomeUnknown(stage=stage) from None

        existing = await self._get_tenant_aggregate(
            canonical_tenant_id,
            allow_missing=True,
        )

        if existing != expected:
            raise LiteLLMAdminError(stage="verify_tenant", status_code=200)
        assert existing is not None
        return existing

    async def generate_blocked_key(
        self,
        tenant_id: UUID | str,
        *,
        raw_key: SecretStr,
        key_alias: str,
        models: Sequence[str],
        allowed_routes: Sequence[str],
    ) -> VirtualKeyRecord:
        canonical_tenant_id = _canonical_tenant_id(tenant_id)
        raw_key_value = _raw_key(raw_key)
        key_hash = hashlib.sha256(raw_key_value.encode()).hexdigest()
        checked_alias = _key_alias(key_alias)
        checked_models = _model_aliases(models)
        checked_routes = _allowed_routes(allowed_routes)
        expected = VirtualKeyRecord(
            key_hash=key_hash,
            tenant_id=canonical_tenant_id,
            key_alias=checked_alias,
            models=checked_models,
            allowed_routes=checked_routes,
            blocked=True,
        )

        record = await self.get_key(key_hash)
        if record == expected:
            return record
        if record is not None:
            raise LiteLLMAdminError(stage="verify_key", status_code=200, key_hash=key_hash)

        try:
            await self._request(
                "POST",
                "/key/generate",
                stage="generate_key",
                json_body={
                    "key": raw_key_value,
                    "key_alias": checked_alias,
                    "user_id": _tenant_user_id(canonical_tenant_id),
                    "models": list(checked_models),
                    "allowed_routes": list(checked_routes),
                    "blocked": True,
                },
                outcome_unknown=True,
                key_hash=key_hash,
            )
        except LiteLLMAdminOutcomeUnknown:
            try:
                record = await self.get_key(key_hash)
            except LiteLLMAdminError:
                raise LiteLLMAdminOutcomeUnknown(stage="generate_key", key_hash=key_hash) from None
            if record == expected:
                return record
            raise LiteLLMAdminOutcomeUnknown(stage="generate_key", key_hash=key_hash) from None

        record = await self.get_key(key_hash)
        if record != expected:
            raise LiteLLMAdminError(stage="verify_key", status_code=200, key_hash=key_hash)
        assert record is not None
        return record

    async def get_key(self, key_hash: str) -> VirtualKeyRecord | None:
        checked_hash = _key_hash(key_hash)
        response = await self._request(
            "GET",
            "/key/list",
            stage="get_key",
            params={"key_hash": checked_hash, "return_full_object": "true"},
        )
        payload = _response_object(response, stage="get_key")
        keys = payload.get("keys")
        if not isinstance(keys, list):
            raise LiteLLMAdminError(stage="get_key", status_code=response.status_code)
        total_count = payload.get("total_count")
        if (
            isinstance(total_count, bool)
            or not isinstance(total_count, int)
            or total_count < 0
            or total_count > 1
            or total_count != len(keys)
        ):
            raise LiteLLMAdminError(stage="get_key", status_code=response.status_code)
        if total_count == 0:
            return None
        if not isinstance(keys[0], dict):
            raise LiteLLMAdminError(stage="get_key", status_code=response.status_code)
        return _virtual_key_record(keys[0], expected_hash=checked_hash)

    async def block_key(self, tenant_id: UUID | str, key_hash: str) -> None:
        await self._mutate_owned_key(tenant_id, key_hash, action="block")

    async def unblock_key(self, tenant_id: UUID | str, key_hash: str) -> None:
        await self._mutate_owned_key(tenant_id, key_hash, action="unblock")

    async def delete_key(self, tenant_id: UUID | str, key_hash: str) -> None:
        await self._mutate_owned_key(tenant_id, key_hash, action="delete")

    async def _mutate_owned_key(
        self,
        tenant_id: UUID | str,
        key_hash: str,
        *,
        action: str,
    ) -> None:
        canonical_tenant_id = _canonical_tenant_id(tenant_id)
        checked_hash = _key_hash(key_hash)
        stage = f"{action}_key"
        existing = await self.get_key(checked_hash)
        if existing is None:
            if action in {"block", "delete"}:
                return
            raise LiteLLMAdminError(stage=stage, status_code=404, key_hash=checked_hash)
        if existing.tenant_id != canonical_tenant_id:
            raise LiteLLMAdminError(stage=stage, status_code=403, key_hash=checked_hash)
        if action == "block" and existing.blocked:
            return
        if action == "unblock" and not existing.blocked:
            return

        body: Mapping[str, object] = (
            {"keys": [checked_hash]} if action == "delete" else {"key": checked_hash}
        )
        try:
            await self._request(
                "POST",
                f"/key/{action}",
                stage=stage,
                json_body=body,
                outcome_unknown=True,
                key_hash=checked_hash,
            )
        except LiteLLMAdminOutcomeUnknown:
            try:
                current = await self.get_key(checked_hash)
            except LiteLLMAdminError:
                raise LiteLLMAdminOutcomeUnknown(stage=stage, key_hash=checked_hash) from None
            if _key_action_reached(
                current,
                action=action,
                tenant_id=canonical_tenant_id,
            ):
                return
            raise LiteLLMAdminOutcomeUnknown(stage=stage, key_hash=checked_hash) from None

        current = await self.get_key(checked_hash)
        if not _key_action_reached(
            current,
            action=action,
            tenant_id=canonical_tenant_id,
        ):
            raise LiteLLMAdminError(stage=f"verify_{stage}", status_code=200, key_hash=checked_hash)

    async def get_tenant_spend_page(
        self,
        tenant_id: UUID | str,
        *,
        start: datetime,
        end: datetime,
        page: int = 1,
        page_size: int = 50,
    ) -> RedactedSpendPage:
        canonical_tenant_id = _canonical_tenant_id(tenant_id)
        start_utc = _utc_datetime(start, field="start")
        end_utc = _utc_datetime(end, field="end")
        if start_utc >= end_utc:
            raise LiteLLMAdminValidationError("invalid spend time range")
        checked_page = _bounded_int(page, field="page", minimum=1, maximum=2**31 - 1)
        checked_page_size = _bounded_int(page_size, field="page_size", minimum=1, maximum=100)

        response = await self._request(
            "GET",
            "/spend/logs/v2",
            stage="list_spend",
            params={
                "user_id": _tenant_user_id(canonical_tenant_id),
                "start_date": start_utc.strftime("%Y-%m-%d %H:%M:%S"),
                "end_date": end_utc.strftime("%Y-%m-%d %H:%M:%S"),
                "page": str(checked_page),
                "page_size": str(checked_page_size),
            },
        )
        payload = _response_object(response, stage="list_spend")
        data = payload.get("data")
        if not isinstance(data, list):
            raise LiteLLMAdminError(stage="list_spend", status_code=response.status_code)
        total = _nonnegative_int(payload.get("total"), stage="list_spend")
        response_page = _positive_payload_int(payload.get("page"), stage="list_spend")
        response_page_size = _positive_payload_int(payload.get("page_size"), stage="list_spend")
        total_pages = _nonnegative_int(payload.get("total_pages"), stage="list_spend")
        expected_total_pages = (total + checked_page_size - 1) // checked_page_size
        offset = (checked_page - 1) * checked_page_size
        expected_items = max(0, min(checked_page_size, total - offset))
        if (
            response_page != checked_page
            or response_page_size != checked_page_size
            or total_pages != expected_total_pages
            or len(data) > checked_page_size
            or len(data) != expected_items
            or (total == 0 and checked_page != 1)
            or (total > 0 and checked_page > total_pages)
        ):
            raise LiteLLMAdminError(stage="list_spend", status_code=response.status_code)
        usage = tuple(
            _redacted_spend_usage(
                item,
                expected_tenant_id=canonical_tenant_id,
                status_code=response.status_code,
            )
            for item in data
        )
        return RedactedSpendPage(
            usage=usage,
            total=total,
            page=response_page,
            page_size=response_page_size,
            total_pages=total_pages,
        )

    async def _get_tenant_aggregate(
        self,
        tenant_id: str,
        *,
        allow_missing: bool,
    ) -> TenantAggregate | None:
        response = await self._request(
            "GET",
            "/user/info",
            stage="get_tenant",
            params={"user_id": _tenant_user_id(tenant_id)},
            allowed_statuses={200, 404} if allow_missing else {200},
        )
        if response.status_code == 404:
            return None
        payload = _response_object(response, stage="get_tenant")
        user_info = payload.get("user_info")
        if not isinstance(user_info, dict):
            raise LiteLLMAdminError(stage="get_tenant", status_code=response.status_code)
        try:
            payload_tenant_id = _parse_tenant_user_id(
                _payload_string(payload.get("user_id"), stage="get_tenant")
            )
        except (LiteLLMAdminValidationError, LiteLLMAdminError):
            raise LiteLLMAdminError(stage="get_tenant", status_code=response.status_code) from None
        if payload_tenant_id != tenant_id:
            raise LiteLLMAdminError(stage="get_tenant", status_code=response.status_code)
        return TenantAggregate(
            tenant_id=tenant_id,
            max_budget_usd=_payload_decimal(user_info.get("max_budget"), stage="get_tenant"),
            budget_duration=_payload_string(user_info.get("budget_duration"), stage="get_tenant"),
            rpm_limit=_positive_payload_int(user_info.get("rpm_limit"), stage="get_tenant"),
            tpm_limit=_positive_payload_int(user_info.get("tpm_limit"), stage="get_tenant"),
            max_parallel_requests=_positive_payload_int(
                user_info.get("max_parallel_requests"), stage="get_tenant"
            ),
            models=_payload_string_tuple(user_info.get("models"), stage="get_tenant"),
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        stage: str,
        params: Mapping[str, str] | None = None,
        json_body: Mapping[str, object] | None = None,
        allowed_statuses: set[int] | None = None,
        outcome_unknown: bool = False,
        key_hash: str | None = None,
    ) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self._master_key.get_secret_value()}"}
        content: bytes | None = None
        if json_body is not None:
            headers["Content-Type"] = "application/json"
            content = _json_bytes(json_body)
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_seconds,
                transport=self._transport,
            ) as client:
                response = await client.request(
                    method,
                    f"{self._base_url}{path}",
                    headers=headers,
                    params=params,
                    content=content,
                )
        except httpx.HTTPError:
            if outcome_unknown:
                raise LiteLLMAdminOutcomeUnknown(
                    stage=stage,
                    key_hash=key_hash,
                ) from None
            raise LiteLLMAdminError(stage=stage, status_code=None) from None

        accepted = allowed_statuses or {200, 201}
        if response.status_code not in accepted:
            if outcome_unknown and (
                response.status_code in _UNCERTAIN_MUTATION_STATUSES or response.status_code >= 500
            ):
                raise LiteLLMAdminOutcomeUnknown(stage=stage, key_hash=key_hash)
            raise LiteLLMAdminError(stage=stage, status_code=response.status_code)
        return response


def _validate_base_url(base_url: SecretStr) -> str:
    if not isinstance(base_url, SecretStr):
        raise LiteLLMAdminConfigurationError("invalid litellm admin URL")
    try:
        parsed = urlsplit(base_url.get_secret_value())
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path.rstrip("/")
        ):
            raise ValueError
        host = parsed.hostname
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        netloc = host if parsed.port is None else f"{host}:{parsed.port}"
        return urlunsplit((parsed.scheme, netloc, "", "", ""))
    except (TypeError, ValueError):
        raise LiteLLMAdminConfigurationError("invalid litellm admin URL") from None


def _validate_master_key(master_key: SecretStr) -> None:
    if not isinstance(master_key, SecretStr) or not master_key.get_secret_value():
        raise LiteLLMAdminConfigurationError("litellm admin master key is required")


def _canonical_tenant_id(value: UUID | str) -> str:
    if isinstance(value, UUID):
        return str(value)
    if not isinstance(value, str):
        raise LiteLLMAdminValidationError("invalid tenant_id")
    try:
        canonical = str(UUID(value))
    except ValueError:
        raise LiteLLMAdminValidationError("invalid tenant_id") from None
    if value != canonical:
        raise LiteLLMAdminValidationError("invalid tenant_id")
    return canonical


def _tenant_user_id(tenant_id: str) -> str:
    return f"{_TENANT_USER_PREFIX}{tenant_id}"


def _parse_tenant_user_id(value: str) -> str:
    if not value.startswith(_TENANT_USER_PREFIX):
        raise LiteLLMAdminValidationError("invalid tenant user_id")
    return _canonical_tenant_id(value.removeprefix(_TENANT_USER_PREFIX))


def _microusd_to_usd(value: int) -> Decimal:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
        or value > MAX_BUDGET_MICROUSD
    ):
        raise LiteLLMAdminValidationError("invalid max_budget_microusd")
    return Decimal(value) / _MICROUSD_PER_USD


def _positive_int(value: int, *, field: str) -> int:
    return _bounded_int(value, field=field, minimum=1, maximum=MAX_SIGNED_INT32)


def _bounded_int(value: int, *, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum or value > maximum:
        raise LiteLLMAdminValidationError(f"invalid {field}")
    return value


def _budget_duration(value: str) -> str:
    if not isinstance(value, str) or not _BUDGET_DURATION_PATTERN.fullmatch(value):
        raise LiteLLMAdminValidationError("invalid budget_duration")
    return value


def _key_alias(value: str) -> str:
    if not isinstance(value, str) or not _KEY_ALIAS_PATTERN.fullmatch(value):
        raise LiteLLMAdminValidationError("invalid key_alias")
    return value


def _model_aliases(values: Sequence[str]) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise LiteLLMAdminValidationError("invalid models")
    checked = tuple(values)
    if not checked:
        raise LiteLLMAdminValidationError("invalid models")
    try:
        validated = tuple(validate_gateway_alias(value) for value in checked)
    except (TypeError, ValueError):
        raise LiteLLMAdminValidationError("invalid models") from None
    if len(set(validated)) != len(validated):
        raise LiteLLMAdminValidationError("invalid models")
    if not set(validated).issubset(DEFAULT_MODEL_ALIASES):
        raise LiteLLMAdminValidationError("invalid models")
    return tuple(sorted(validated))


def _allowed_routes(values: Sequence[str]) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise LiteLLMAdminValidationError("invalid allowed_routes")
    checked = tuple(values)
    if len(set(checked)) != len(checked) or set(checked) != _TENANT_ROUTES:
        raise LiteLLMAdminValidationError("invalid allowed_routes")
    return tuple(sorted(checked))


def _raw_key(value: SecretStr) -> str:
    if not isinstance(value, SecretStr):
        raise LiteLLMAdminValidationError("invalid raw_key")
    raw = value.get_secret_value()
    suffix = raw.removeprefix("sk-")
    if not _RAW_KEY_PATTERN.fullmatch(raw) or len(set(suffix)) < 12:
        raise LiteLLMAdminValidationError("invalid raw_key")
    return raw


def _key_hash(value: str) -> str:
    if not isinstance(value, str) or not _KEY_HASH_PATTERN.fullmatch(value):
        raise LiteLLMAdminValidationError("invalid key_hash")
    return value


def _utc_datetime(value: datetime, *, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise LiteLLMAdminValidationError(f"invalid {field}")
    return value.astimezone(UTC)


def _json_bytes(value: object) -> bytes:
    return _json_text(value).encode()


def _json_text(value: object) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=True)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise LiteLLMAdminValidationError("invalid decimal JSON value")
        return format(value, "f")
    if isinstance(value, Mapping):
        fields: list[str] = []
        for key, item in value.items():
            if not isinstance(key, str):
                raise LiteLLMAdminValidationError("invalid JSON object")
            fields.append(f"{json.dumps(key)}:{_json_text(item)}")
        return "{" + ",".join(fields) + "}"
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return "[" + ",".join(_json_text(item) for item in value) + "]"
    raise LiteLLMAdminValidationError("invalid JSON value")


def _response_object(response: httpx.Response, *, stage: str) -> dict[str, Any]:
    try:
        payload = json.loads(response.content, parse_float=Decimal)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise LiteLLMAdminError(stage=stage, status_code=response.status_code) from None
    if not isinstance(payload, dict):
        raise LiteLLMAdminError(stage=stage, status_code=response.status_code)
    return payload


def _virtual_key_record(payload: Mapping[str, object], *, expected_hash: str) -> VirtualKeyRecord:
    try:
        key_hash = _key_hash(_payload_string(payload.get("token"), stage="get_key"))
        if key_hash != expected_hash:
            raise ValueError
        return VirtualKeyRecord(
            key_hash=key_hash,
            tenant_id=_parse_tenant_user_id(
                _payload_string(payload.get("user_id"), stage="get_key")
            ),
            key_alias=_key_alias(_payload_string(payload.get("key_alias"), stage="get_key")),
            models=_model_aliases(_payload_string_tuple(payload.get("models"), stage="get_key")),
            allowed_routes=_allowed_routes(
                _payload_string_tuple(payload.get("allowed_routes"), stage="get_key")
            ),
            blocked=_payload_bool(payload.get("blocked"), stage="get_key"),
        )
    except (LiteLLMAdminValidationError, ValueError):
        raise LiteLLMAdminError(stage="get_key", status_code=200) from None


def _redacted_spend_usage(
    payload: object,
    *,
    expected_tenant_id: str,
    status_code: int,
) -> RedactedSpendUsage:
    try:
        if not isinstance(payload, dict):
            raise ValueError
        tenant_id = _parse_tenant_user_id(_payload_string(payload.get("user"), stage="list_spend"))
        if tenant_id != expected_tenant_id:
            raise ValueError
        input_tokens = _nonnegative_int(payload.get("prompt_tokens"), stage="list_spend")
        output_tokens = _nonnegative_int(payload.get("completion_tokens"), stage="list_spend")
        total_tokens = _nonnegative_int(payload.get("total_tokens"), stage="list_spend")
        if total_tokens != input_tokens + output_tokens:
            raise ValueError
        return RedactedSpendUsage(
            request_id=_payload_string(payload.get("request_id"), stage="list_spend"),
            key_hash=_key_hash(_payload_string(payload.get("api_key"), stage="list_spend")),
            tenant_id=tenant_id,
            spend_usd=_payload_decimal(payload.get("spend"), stage="list_spend"),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            status=_spend_status(payload.get("status")),
        )
    except (LiteLLMAdminError, LiteLLMAdminValidationError, ValueError):
        raise LiteLLMAdminError(stage="list_spend", status_code=status_code) from None


def _key_action_reached(
    record: VirtualKeyRecord | None,
    *,
    action: str,
    tenant_id: str,
) -> bool:
    if action == "delete":
        return record is None
    if record is None or record.tenant_id != tenant_id:
        return action == "block" and record is None
    if action == "block":
        return record.blocked
    if action == "unblock":
        return not record.blocked
    raise AssertionError("unsupported key action")


def _payload_string(value: object, *, stage: str) -> str:
    if not isinstance(value, str) or not value:
        raise LiteLLMAdminError(stage=stage, status_code=200)
    return value


def _payload_bool(value: object, *, stage: str) -> bool:
    if not isinstance(value, bool):
        raise LiteLLMAdminError(stage=stage, status_code=200)
    return value


def _payload_string_tuple(value: object, *, stage: str) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item for item in value)
    ):
        raise LiteLLMAdminError(stage=stage, status_code=200)
    return tuple(value)


def _spend_status(value: object) -> str:
    if value == "success":
        return "success"
    if value == "failure":
        return "failure"
    raise LiteLLMAdminError(stage="list_spend", status_code=200)


def _payload_decimal(value: object, *, stage: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, str, Decimal)):
        raise LiteLLMAdminError(stage=stage, status_code=200)
    try:
        decimal_value = Decimal(value)
    except (InvalidOperation, ValueError):
        raise LiteLLMAdminError(stage=stage, status_code=200) from None
    if not decimal_value.is_finite() or decimal_value < 0:
        raise LiteLLMAdminError(stage=stage, status_code=200)
    return decimal_value


def _positive_payload_int(value: object, *, stage: str) -> int:
    number = _nonnegative_int(value, stage=stage)
    if number == 0:
        raise LiteLLMAdminError(stage=stage, status_code=200)
    return number


def _nonnegative_int(value: object, *, stage: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise LiteLLMAdminError(stage=stage, status_code=200)
    return value
