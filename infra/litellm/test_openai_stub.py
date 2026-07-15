from __future__ import annotations

import importlib.util
import json
import threading
import unittest
from urllib.error import HTTPError
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
STUB_PATH = ROOT / "infra/litellm/openai_stub.py"


def _load_handler() -> type[Any]:
    spec = importlib.util.spec_from_file_location("agent_platform_openai_stub", STUB_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load OpenAI Stub")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Handler


class OpenAiStubProtocolTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _load_handler())
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def post_completion(self, payload: dict[str, object]) -> dict[str, Any]:
        request = Request(
            f"http://127.0.0.1:{self.server.server_port}/primary/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=5) as response:
            return json.loads(response.read())

    def test_mvp_web_flow_returns_terminal_text_even_when_agent_tools_are_bound(self) -> None:
        response = self.post_completion(
            {
                "model": "primary-test",
                "messages": [
                    {
                        "role": "user",
                        "content": json.dumps({"message": "mvp-web-flow"}),
                    }
                ],
                "tools": [
                    {
                        "type": "function",
                        "function": {"name": "write_todos", "parameters": {}},
                    }
                ],
            }
        )

        choice = response["choices"][0]
        self.assertEqual(choice["finish_reason"], "stop")
        self.assertEqual(
            choice["message"],
            {"role": "assistant", "content": "local stub completion"},
        )

    def test_generic_tool_probe_keeps_returning_a_tool_call(self) -> None:
        response = self.post_completion(
            {
                "model": "primary-test",
                "messages": [{"role": "user", "content": "tool"}],
                "tools": [
                    {
                        "type": "function",
                        "function": {"name": "get_status", "parameters": {}},
                    }
                ],
            }
        )

        choice = response["choices"][0]
        self.assertEqual(choice["finish_reason"], "tool_calls")
        self.assertEqual(choice["message"]["tool_calls"][0]["function"]["name"], "get_status")

    def test_artifact_flow_writes_then_publishes_the_same_sandbox_file(self) -> None:
        messages: list[dict[str, object]] = [
            {"role": "user", "content": json.dumps({"message": "mvp-artifact-flow"})}
        ]
        first = self.post_completion({"model": "primary-test", "messages": messages})
        first_call = first["choices"][0]["message"]["tool_calls"][0]
        self.assertEqual(first_call["function"]["name"], "write_file")
        self.assertEqual(
            json.loads(first_call["function"]["arguments"])["file_path"],
            "/workspace/result.txt",
        )

        messages.extend(
            [
                first["choices"][0]["message"],
                {
                    "role": "tool",
                    "tool_call_id": first_call["id"],
                    "content": "Updated file /workspace/result.txt",
                },
            ]
        )
        second = self.post_completion({"model": "primary-test", "messages": messages})
        second_call = second["choices"][0]["message"]["tool_calls"][0]
        self.assertEqual(second_call["function"]["name"], "create_artifact")
        self.assertEqual(
            json.loads(second_call["function"]["arguments"])["workspace_path"],
            "result.txt",
        )

    def test_generic_request_never_calls_a_tool_that_was_not_declared(self) -> None:
        response = self.post_completion(
            {
                "model": "primary-test",
                "messages": [{"role": "user", "content": "ordinary agent task"}],
                "tools": [
                    {
                        "type": "function",
                        "function": {"name": "write_todos", "parameters": {}},
                    }
                ],
            }
        )

        choice = response["choices"][0]
        self.assertEqual(choice["finish_reason"], "stop")
        self.assertEqual(
            choice["message"],
            {"role": "assistant", "content": "local stub completion"},
        )

    def test_mvp_failure_scenario_returns_a_deterministic_upstream_error(self) -> None:
        with self.assertRaises(HTTPError) as raised:
            self.post_completion(
                {
                    "model": "primary-test",
                    "messages": [
                        {
                            "role": "user",
                            "content": json.dumps({"message": "mvp-web-flow-failure"}),
                        }
                    ],
                }
            )

        self.assertEqual(raised.exception.code, 500)
        error = json.loads(raised.exception.read())
        self.assertEqual(error["error"]["type"], "mvp_controlled_failure")


if __name__ == "__main__":
    unittest.main()
