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
