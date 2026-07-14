"""Deterministic test-only OpenAI-compatible endpoint for LiteLLM acceptance."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


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
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            self._send_json(400, {"error": str(exc)})
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
        if request.get("tools"):
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
            self.wfile.write(f"data: {json.dumps(payload)}\n\n".encode("utf-8"))
        final = {
            "id": "chatcmpl-local-stub-stream",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": request.get("model", "local-test"),
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 3, "total_tokens": 4},
        }
        self.wfile.write(f"data: {json.dumps(final)}\n\n".encode("utf-8"))
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def log_message(self, _format: str, *_args: object) -> None:
        return


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 4010), Handler).serve_forever()
