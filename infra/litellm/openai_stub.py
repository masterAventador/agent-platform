"""Deterministic test-only OpenAI-compatible endpoint for LiteLLM acceptance."""

from __future__ import annotations

import ast
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import PurePosixPath
from typing import Any


_ATTACHMENT_ROOT = PurePosixPath("/workspace/inputs")
_MISSING_ATTACHMENT = _ATTACHMENT_ROOT / "missing" / "brief.txt"


def _attachment_path_from_glob(content: object) -> str:
    """Resolve Deep Agents' relative glob result inside the attachment root."""
    try:
        candidates = ast.literal_eval(str(content))
    except (SyntaxError, ValueError):
        return str(_MISSING_ATTACHMENT)
    if not isinstance(candidates, list):
        return str(_MISSING_ATTACHMENT)
    for candidate in candidates:
        if not isinstance(candidate, str):
            continue
        candidate_path = PurePosixPath(candidate)
        if candidate_path.is_absolute():
            resolved = candidate_path
        else:
            resolved = _ATTACHMENT_ROOT / candidate_path
        if (
            ".." not in resolved.parts
            and resolved.name == "brief.txt"
            and resolved.is_relative_to(_ATTACHMENT_ROOT)
        ):
            return str(resolved)
    return str(_MISSING_ATTACHMENT)


class Handler(BaseHTTPRequestHandler):
    retry_counts: dict[str, int] = {}
    retry_lock = threading.Lock()

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        if self.path == "/health":
            self._send_json(200, {"status": "ok"})
            return
        self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        completion_paths = {
            "/primary/v1/chat/completions",
            "/fallback/v1/chat/completions",
        }
        if self.path not in completion_paths:
            self._send_json(404, {"error": "not found"})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            request = json.loads(self.rfile.read(length))
            messages = request.get("messages")
            if not isinstance(messages, list) or not messages:
                raise ValueError("messages must be a list")
            last_message = messages[-1]
            if not isinstance(last_message, dict):
                raise ValueError("last message must be an object")
            scenario = last_message.get("content")
            if isinstance(scenario, str):
                try:
                    structured_scenario = json.loads(scenario)
                except json.JSONDecodeError:
                    structured_scenario = None
                if isinstance(structured_scenario, dict) and isinstance(
                    structured_scenario.get("message"), str
                ):
                    scenario = structured_scenario["message"]
            for candidate in messages:
                if not isinstance(candidate, dict):
                    continue
                candidate_content = candidate.get("content")
                if not isinstance(candidate_content, str):
                    continue
                try:
                    candidate_scenario = json.loads(candidate_content)
                except json.JSONDecodeError:
                    continue
                if (
                    isinstance(candidate_scenario, dict)
                    and candidate_scenario.get("message") == "mvp-artifact-flow"
                ):
                    scenario = "mvp-artifact-flow"
                    break
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            self._send_json(400, {"error": str(exc)})
            return

        if scenario == "mvp-web-flow-failure":
            self._send_json(
                500,
                {
                    "error": {
                        "message": "deterministic MVP controlled failure",
                        "type": "mvp_controlled_failure",
                    }
                },
            )
            return

        if scenario == "retry":
            with self.retry_lock:
                attempts = self.retry_counts.get("retry-once", 0)
                self.retry_counts["retry-once"] = attempts + 1
            if attempts == 0:
                self._send_json(
                    500,
                    {
                        "error": {
                            "message": "deterministic retry",
                            "type": "server_error",
                        }
                    },
                )
                return

        if scenario == "fallback" and self.path.startswith("/primary/"):
            self._send_json(
                500,
                {
                    "error": {
                        "message": "deterministic primary failure",
                        "type": "server_error",
                    }
                },
            )
            return

        if request.get("stream") is True:
            self._send_stream(request)
            return

        message: dict[str, Any]
        finish_reason = "stop"
        if scenario == "mvp-web-flow":
            message = {"role": "assistant", "content": "local stub completion"}
        elif scenario == "mvp-artifact-flow":
            tool_messages = [
                candidate
                for candidate in messages
                if isinstance(candidate, dict) and candidate.get("role") == "tool"
            ]
            completed_tool_calls = len(tool_messages)
            if completed_tool_calls == 0:
                message = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call-find-attachment",
                            "type": "function",
                            "function": {
                                "name": "glob",
                                "arguments": json.dumps(
                                    {
                                        "path": "/workspace/inputs",
                                        "pattern": "**/brief.txt",
                                    }
                                ),
                            },
                        }
                    ],
                }
                finish_reason = "tool_calls"
            elif completed_tool_calls == 1:
                attachment_path = _attachment_path_from_glob(
                    tool_messages[-1].get("content", "")
                )
                message = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call-read-attachment",
                            "type": "function",
                            "function": {
                                "name": "read_file",
                                "arguments": json.dumps(
                                    {"file_path": attachment_path}
                                ),
                            },
                        }
                    ],
                }
                finish_reason = "tool_calls"
            elif completed_tool_calls == 2:
                attachment_content = str(tool_messages[-1].get("content", ""))
                message = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call-write-artifact",
                            "type": "function",
                            "function": {
                                "name": "write_file",
                                "arguments": json.dumps(
                                    {
                                        "file_path": "/workspace/result.txt",
                                        "content": (
                                            "attachment-derived result:\n"
                                            f"{attachment_content}"
                                        ),
                                    }
                                ),
                            },
                        }
                    ],
                }
                finish_reason = "tool_calls"
            elif completed_tool_calls == 3:
                message = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call-create-artifact",
                            "type": "function",
                            "function": {
                                "name": "create_artifact",
                                "arguments": json.dumps(
                                    {
                                        "name": "result.txt",
                                        "media_type": "text/plain",
                                        "workspace_path": "result.txt",
                                    }
                                ),
                            },
                        }
                    ],
                }
                finish_reason = "tool_calls"
            else:
                message = {
                    "role": "assistant",
                    "content": "artifact published from the real sandbox",
                }
        elif any(
            isinstance(tool, dict)
            and isinstance(tool.get("function"), dict)
            and tool["function"].get("name") == "get_status"
            for tool in request.get("tools", [])
        ):
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-local-stub",
                        "type": "function",
                        "function": {"name": "get_status", "arguments": "{}"},
                    }
                ],
            }
            finish_reason = "tool_calls"
        elif request.get("response_format"):
            message = {"role": "assistant", "content": json.dumps({"status": "ok"})}
        else:
            content = (
                "local fallback completion"
                if self.path.startswith("/fallback/")
                else "local stub completion"
            )
            message = {"role": "assistant", "content": content}

        self._send_json(
            200,
            {
                "id": "chatcmpl-local-stub",
                "object": "chat.completion",
                "created": 0,
                "model": request.get("model", "local-test"),
                "choices": [
                    {
                        "index": 0,
                        "message": message,
                        "finish_reason": finish_reason,
                    }
                ],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 3,
                    "total_tokens": 4,
                },
            },
        )

    def _send_stream(self, request: dict[str, Any]) -> None:
        chunks = ["local stub ", "completion"]
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        for index, content in enumerate(chunks):
            payload = {
                "id": "chatcmpl-local-stub-stream",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": request.get("model", "local-test"),
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            **({"role": "assistant"} if index == 0 else {}),
                            "content": content,
                        },
                        "finish_reason": None,
                    }
                ],
            }
            self.wfile.write(f"data: {json.dumps(payload)}\n\n".encode())
        final = {
            "id": "chatcmpl-local-stub-stream",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": request.get("model", "local-test"),
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 3, "total_tokens": 4},
        }
        self.wfile.write(f"data: {json.dumps(final)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def log_message(self, _format: str, *_args: object) -> None:
        return


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 4010), Handler).serve_forever()
