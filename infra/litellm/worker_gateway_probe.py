"""Run real gateway checks from the production worker image and network."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

from pydantic import SecretStr

from agent_platform.infrastructure.llm.litellm import (
    LiteLLMChatModelFactory,
    LiteLLMGatewayReadinessProbe,
)


BASE_URL = os.environ["AGENT_PLATFORM_LLM_GATEWAY_URL"].rstrip("/")
API_KEY = os.environ["AGENT_PLATFORM_LLM_GATEWAY_API_KEY"]
MODELS_URL = f"{BASE_URL}/models"  # Worker base is /v1, so this is /v1/models.


def request(payload: dict[str, Any]) -> dict[str, Any]:
    call = urllib.request.Request(
        f"{BASE_URL}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(call, timeout=20) as response:
        return json.load(response)


def readiness() -> None:
    probe = LiteLLMGatewayReadinessProbe(
        base_url=SecretStr(BASE_URL),
        api_key=SecretStr(API_KEY),
        timeout_seconds=10,
    )
    asyncio.run(probe.assert_ready(frozenset({"general-purpose"})))

    models_request = urllib.request.Request(
        MODELS_URL,
        headers={"Authorization": f"Bearer {API_KEY}"},
    )
    with urllib.request.urlopen(models_request, timeout=10) as response:
        models = json.load(response)
    model_ids = {item.get("id") for item in models.get("data", [])}
    if model_ids != {"general-purpose"}:
        raise SystemExit("scoped worker models response was not exact")

    def expect_forbidden(request: urllib.request.Request, label: str) -> None:
        try:
            urllib.request.urlopen(request, timeout=10)
        except urllib.error.HTTPError as exc:
            if exc.code != 403:
                raise
        else:
            raise SystemExit(f"scoped worker key unexpectedly reached {label}")

    fallback = urllib.request.Request(
        f"{BASE_URL}/chat/completions",
        data=json.dumps(
            {
                "model": "general-purpose-fallback",
                "messages": [{"role": "user", "content": "scope check"}],
            }
        ).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    expect_forbidden(fallback, "test-only fallback model")

    embeddings = urllib.request.Request(
        f"{BASE_URL}/embeddings",
        data=json.dumps({"model": "general-purpose", "input": "scope check"}).encode(
            "utf-8"
        ),
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    expect_forbidden(embeddings, "unallowed embeddings route")

    forbidden = urllib.request.Request(
        f"{BASE_URL.removesuffix('/v1')}/key/generate",
        data=b"{}",
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    expect_forbidden(forbidden, "key management")
    print("Worker-network scoped-key /v1/models readiness passed for general-purpose")


def chat() -> None:
    factory = LiteLLMChatModelFactory(
        base_url=SecretStr(BASE_URL),
        api_key=SecretStr(API_KEY),
        timeout_seconds=20,
        max_retries=2,
    )
    response = factory("general-purpose").invoke("ping")
    if response.content != "local stub completion":
        raise SystemExit(f"unexpected ChatOpenAI response: {response.content!r}")
    print("Production worker ChatOpenAI -> LiteLLM -> stub passed")


def matrix() -> None:
    normal = request(
        {
            "model": "general-purpose",
            "messages": [{"role": "user", "content": "usage"}],
        }
    )
    usage = normal.get("usage", {})
    if usage.get("total_tokens") != 4:
        raise SystemExit(f"usage passthrough mismatch: {usage!r}")

    tools = request(
        {
            "model": "general-purpose",
            "messages": [{"role": "user", "content": "use a tool"}],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "get_status",
                        "description": "Return status",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ],
        }
    )
    tool_calls = tools["choices"][0]["message"].get("tool_calls", [])
    if not tool_calls or tool_calls[0]["function"]["name"] != "get_status":
        raise SystemExit(f"tool_calls passthrough mismatch: {tool_calls!r}")

    structured = request(
        {
            "model": "general-purpose",
            "messages": [{"role": "user", "content": "return json"}],
            "response_format": {"type": "json_object"},
        }
    )
    content = structured["choices"][0]["message"]["content"]
    if json.loads(content) != {"status": "ok"}:
        raise SystemExit(f"structured output mismatch: {content!r}")

    retry = request(
        {
            "model": "general-purpose",
            "messages": [{"role": "user", "content": "retry"}],
        }
    )
    if retry["choices"][0]["message"]["content"] != "local stub completion":
        raise SystemExit("retry response mismatch")

    stream_request = urllib.request.Request(
        f"{BASE_URL}/chat/completions",
        data=json.dumps(
            {
                "model": "general-purpose",
                "messages": [{"role": "user", "content": "stream"}],
                "stream": True,
                "stream_options": {"include_usage": True},
            }
        ).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    chunks: list[dict[str, Any]] = []
    with urllib.request.urlopen(stream_request, timeout=20) as response:
        for raw_line in response:
            line = raw_line.decode("utf-8").strip()
            if line.startswith("data: ") and line != "data: [DONE]":
                chunks.append(json.loads(line.removeprefix("data: ")))
    streamed = "".join(
        choice.get("delta", {}).get("content", "")
        for chunk in chunks
        for choice in chunk.get("choices", [])
    )
    if streamed != "local stub completion":
        raise SystemExit(f"streaming mismatch: {streamed!r}")
    if not any(chunk.get("usage", {}).get("total_tokens") == 4 for chunk in chunks):
        raise SystemExit("streaming usage missing")

    fallback = request(
        {
            "model": "general-purpose",
            "messages": [{"role": "user", "content": "fallback"}],
        }
    )
    if fallback["choices"][0]["message"]["content"] != "local fallback completion":
        raise SystemExit("fallback response mismatch")
    print("Stub streaming/tool/structured/retry/fallback/usage matrix passed")


MODES = {"readiness": readiness, "chat": chat, "matrix": matrix}
if len(sys.argv) != 2 or sys.argv[1] not in MODES:
    raise SystemExit(f"usage: {sys.argv[0]} {{{'|'.join(MODES)}}}")
MODES[sys.argv[1]]()
