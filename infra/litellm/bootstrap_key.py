"""Idempotently register the local worker's least-privilege LiteLLM key."""

from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


BASE_URL = os.environ.get("LITELLM_BOOTSTRAP_URL", "http://litellm:4000").rstrip("/")
MASTER_KEY = os.environ["LITELLM_MASTER_KEY"]
WORKER_KEY = os.environ["LITELLM_WORKER_API_KEY"]
ALLOWED_ROUTES = [
    "/chat/completions",
    "/v1/chat/completions",
    "/models",
    "/v1/models",
]
KEY_METADATA = {
    "application": "agent-platform-worker",
    "environment": "local",
    "credential_scope": "local-shared-worker",
    "tenant_attribution": "not-enabled",
}


def validate_local_secret(name: str, value: str) -> None:
    if not value.startswith("sk-") or len(value) < 32 or "CHANGE_ME" in value:
        raise SystemExit(f"{name} must be a locally generated high-entropy sk- secret")


validate_local_secret("master key", MASTER_KEY)
validate_local_secret("worker key", WORKER_KEY)
if WORKER_KEY == MASTER_KEY:
    raise SystemExit("worker key must differ from master key")


def call(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    authorization: str = MASTER_KEY,
) -> tuple[int, dict[str, Any]]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=body,
        headers={
            "Authorization": f"Bearer {authorization}",
            "Content-Type": "application/json",
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            error = json.load(exc)
        except json.JSONDecodeError:
            error = {"error": exc.read().decode("utf-8", errors="replace")}
        return exc.code, error


key_hash = hashlib.sha256(WORKER_KEY.encode("utf-8")).hexdigest()
key_query = urllib.parse.urlencode(
    {"key_hash": key_hash, "return_full_object": "true", "size": "1"}
)
status, existing = call("GET", f"/key/list?{key_query}")
if status != 200:
    raise SystemExit(f"worker key bootstrap failed during lookup (status={status})")
existing_keys = existing.get("keys", [])
existing_count = existing.get("total_count", 0)
if existing_count not in {0, 1} or len(existing_keys) != existing_count:
    raise SystemExit("worker key bootstrap lookup returned an unexpected key count")
payload: dict[str, Any] = {
    "key": WORKER_KEY,
    "key_alias": "agent-platform-worker",
    "models": ["general-purpose"],
    "allowed_routes": ALLOWED_ROUTES,
    "user_id": "agent-platform-worker",
    "metadata": KEY_METADATA,
}
legacy_broad_key = False
if existing_count == 1:
    info = existing_keys[0]
    legacy_broad_key = info.get("key_type") is not None or "llm_api_routes" in (
        info.get("allowed_routes") or []
    )

if legacy_broad_key:
    status, _ = call("POST", "/key/delete", {"keys": [key_hash]})
    if status not in {200, 201}:
        raise SystemExit(
            f"worker key bootstrap failed during legacy delete (status={status})"
        )
    status, _ = call("POST", "/key/generate", payload)
    stage = "legacy recreate"
elif existing_count == 0:
    status, _ = call("POST", "/key/generate", payload)
    stage = "generate"
else:
    status, _ = call("POST", "/key/update", payload)
    stage = "update"
if status not in {200, 201}:
    raise SystemExit(f"worker key bootstrap failed during {stage} (status={status})")

status, response = call("GET", f"/key/list?{key_query}")
if status != 200:
    raise SystemExit(f"worker key bootstrap failed during verify (status={status})")
keys = response.get("keys", [])
if response.get("total_count") != 1 or len(keys) != 1:
    raise SystemExit("worker key bootstrap verification returned an unexpected key count")
info = keys[0]
if info.get("key_type") is not None:
    raise SystemExit("worker key bootstrap verification mismatch for key_type")
expected = {
    "models": ["general-purpose"],
    "allowed_routes": ALLOWED_ROUTES,
    "metadata": KEY_METADATA,
}
for field, value in expected.items():
    actual = info.get(field)
    if field in {"models", "allowed_routes"}:
        matches = (
            isinstance(actual, list)
            and all(isinstance(item, str) for item in actual)
            and len(actual) == len(set(actual))
            and set(actual) == set(value)
        )
    else:
        matches = actual == value
    if not matches:
        raise SystemExit(f"worker key bootstrap verification mismatch for {field}")
print("LiteLLM worker virtual key is scoped and ready")
