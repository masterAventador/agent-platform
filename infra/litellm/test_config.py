from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = ROOT / "infra/compose/litellm.yml"
COMPOSE_ENV = ROOT / "infra/compose/.env.litellm.example"
LITELLM_CONFIG = ROOT / "infra/litellm/config.yaml"
STUB_COMPOSE_FILE = ROOT / "infra/litellm/compose.stub.yml"
STUB_CONFIG = ROOT / "infra/litellm/config.stub.yaml"
STUB_SERVER = ROOT / "infra/litellm/openai_stub.py"
STUB_PROTOCOL_TEST = ROOT / "infra/litellm/test_openai_stub.py"
BOOTSTRAP_SCRIPT = ROOT / "infra/litellm/bootstrap_key.py"
NETWORK_SCRIPT = ROOT / "infra/litellm/network.sh"
WORKER_PROBE = ROOT / "infra/litellm/worker_gateway_probe.py"
LEGACY_KEY_SEED = ROOT / "infra/litellm/seed_legacy_key.py"
TEST_SCRIPT = ROOT / "infra/litellm/test.sh"
EXPECTED_IMAGE = (
    "ghcr.io/berriai/litellm-non_root:v1.86.2@"
    "sha256:511b513bc68956793433d62c1812daff56984325543f6a15431c622823fd90cb"
)


class LiteLlmComposeContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        compose_env = os.environ.copy()
        compose_env.pop("LITELLM_NETWORK_NAME", None)
        result = subprocess.run(
            [
                "docker",
                "compose",
                "--env-file",
                str(COMPOSE_ENV),
                "-f",
                str(COMPOSE_FILE),
                "config",
                "--format",
                "json",
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            env=compose_env,
        )
        cls.config: dict[str, Any] = json.loads(result.stdout)
        cls.service: dict[str, Any] = cls.config["services"]["litellm"]

    def test_is_an_independent_compose_project(self) -> None:
        self.assertEqual(self.config["name"], "agent-platform-litellm")
        self.assertEqual(
            set(self.config["services"]),
            {"litellm", "litellm-db", "worker-key-bootstrap"},
        )
        self.assertEqual(set(self.service.get("networks", {})), {"database", "llm"})
        self.assertTrue(self.config["networks"]["database"]["internal"])
        self.assertTrue(self.config["networks"]["llm"]["external"])
        self.assertEqual(self.config["networks"]["llm"]["name"], "agent-platform-llm")

    def test_uses_only_the_pinned_official_image(self) -> None:
        self.assertEqual(self.service["image"], EXPECTED_IMAGE)
        self.assertNotIn("build", self.service)
        self.assertEqual(self.service.get("restart"), "no")

    def test_proxy_hardening_is_proven_and_explicit(self) -> None:
        self.assertTrue(self.service["read_only"])
        self.assertEqual(self.service["cap_drop"], ["ALL"])
        self.assertNotIn("user", self.service)
        self.assertEqual(
            {item.split(":", 1)[0] for item in self.service["tmpfs"]},
            {"/tmp", "/app/migrations"},
        )
        migration_tmpfs = next(
            item for item in self.service["tmpfs"] if item.startswith("/app/migrations:")
        )
        self.assertIn("uid=65534", migration_tmpfs)
        self.assertIn("mode=0700", migration_tmpfs)

    def test_only_publishes_the_proxy_on_loopback(self) -> None:
        self.assertEqual(
            self.service["ports"],
            [
                {
                    "mode": "ingress",
                    "target": 4000,
                    "published": "4000",
                    "protocol": "tcp",
                    "host_ip": "127.0.0.1",
                }
            ],
        )

    def test_mounts_only_the_public_config_read_only(self) -> None:
        self.assertEqual(len(self.service["volumes"]), 1)
        mount = self.service["volumes"][0]
        self.assertEqual(mount["type"], "bind")
        self.assertEqual(Path(mount["source"]), LITELLM_CONFIG)
        self.assertEqual(mount["target"], "/app/config.yaml")
        self.assertTrue(mount["read_only"])

    def test_starts_through_the_public_cli_and_has_a_real_healthcheck(self) -> None:
        self.assertEqual(
            self.service["command"],
            ["--config", "/app/config.yaml", "--port", "4000"],
        )
        health_test = self.service["healthcheck"]["test"]
        self.assertEqual(health_test[:3], ["CMD", "python3", "-c"])
        self.assertIn("http://127.0.0.1:4000/health/liveliness", health_test[3])

    def test_credentials_are_environment_only_placeholders(self) -> None:
        environment = self.service["environment"]
        self.assertEqual(
            set(environment),
            {
                "LITELLM_MASTER_KEY",
                "LITELLM_UPSTREAM_API_BASE",
                "LITELLM_UPSTREAM_API_KEY",
                "LITELLM_UPSTREAM_MODEL",
                "DATABASE_URL",
                "ENFORCE_PRISMA_MIGRATION_CHECK",
                "LITELLM_MIGRATION_DIR",
            },
        )
        self.assertEqual(environment["LITELLM_MIGRATION_DIR"], "/app/migrations")

        example = COMPOSE_ENV.read_text(encoding="utf-8")
        self.assertIn("LITELLM_MASTER_KEY=sk-CHANGE_ME_LITELLM_MASTER_KEY", example)
        self.assertIn("LITELLM_WORKER_API_KEY=sk-CHANGE_ME_LITELLM_WORKER_KEY", example)
        self.assertIn("LITELLM_UPSTREAM_MODEL=dashscope/qwen-plus", example)
        self.assertIn("LITELLM_UPSTREAM_API_KEY=", example)
        self.assertIn("LITELLM_UPSTREAM_API_BASE=", example)
        self.assertNotRegex(example, r"(?m)^LITELLM_UPSTREAM_API_KEY=\S+")

    def test_has_an_internal_database_and_idempotent_scoped_key_bootstrap(self) -> None:
        database = self.config["services"]["litellm-db"]
        self.assertRegex(
            database["image"],
            r"^postgres:\d+\.\d+-alpine\d+\.\d+@sha256:[0-9a-f]{64}$",
        )
        self.assertNotIn("ports", database)
        self.assertEqual(database["restart"], "no")
        self.assertEqual(len(database["volumes"]), 1)
        self.assertIn("litellm_db_data", self.config["volumes"])

        self.assertEqual(
            self.service["depends_on"]["litellm-db"]["condition"],
            "service_healthy",
        )
        self.assertEqual(self.service["environment"]["ENFORCE_PRISMA_MIGRATION_CHECK"], "true")
        self.assertIn("@litellm-db:5432/", self.service["environment"]["DATABASE_URL"])

        bootstrap = self.config["services"]["worker-key-bootstrap"]
        self.assertEqual(bootstrap["image"], EXPECTED_IMAGE)
        self.assertNotIn("ports", bootstrap)
        self.assertEqual(bootstrap["restart"], "no")
        self.assertEqual(bootstrap["depends_on"]["litellm"]["condition"], "service_healthy")
        self.assertEqual(Path(bootstrap["volumes"][0]["source"]), BOOTSTRAP_SCRIPT)
        self.assertTrue(bootstrap["volumes"][0]["read_only"])

        self.assertTrue(BOOTSTRAP_SCRIPT.is_file(), "missing scoped-key bootstrap")
        script = BOOTSTRAP_SCRIPT.read_text(encoding="utf-8")
        # LiteLLM v1.86.2 rewrites key_type=llm_api to the broad
        # ["llm_api_routes"] preset, overriding explicit route restrictions.
        self.assertNotIn('"key_type":', script)
        self.assertIn('"models": ["general-purpose"]', script)
        self.assertIn('"allowed_routes"', script)
        self.assertIn('"metadata"', script)
        self.assertIn('"environment": "local"', script)
        self.assertNotIn('"tags"', script)
        self.assertIn("worker key must differ from master key", script)
        self.assertIn("/key/generate", script)
        self.assertIn("/key/update", script)
        self.assertGreaterEqual(script.count("/key/list"), 2)
        self.assertIn('hashlib.sha256(WORKER_KEY.encode("utf-8"))', script)
        self.assertNotIn("authorization=WORKER_KEY", script)
        self.assertIn('field in {"models", "allowed_routes"}', script)
        self.assertIn("set(actual) == set(value)", script)
        self.assertIn("len(actual) == len(set(actual))", script)
        self.assertNotIn('urlencode({"key": WORKER_KEY})', script)
        self.assertNotRegex(script, r"SystemExit\([^\n]*(?:response|WORKER_KEY|MASTER_KEY)")
        self.assertNotIn("LITELLM_WORKER_API_KEY", self.service["environment"])

    def test_external_network_creation_and_worker_probe_are_reproducible(self) -> None:
        self.assertTrue(NETWORK_SCRIPT.is_file(), "missing external network helper")
        network = NETWORK_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("agent-platform-llm", network)
        self.assertIn("docker network inspect", network)
        self.assertIn("docker network create", network)

        self.assertTrue(WORKER_PROBE.is_file(), "missing production worker image probe")
        probe = WORKER_PROBE.read_text(encoding="utf-8")
        self.assertIn("LiteLLMChatModelFactory", probe)
        self.assertIn("general-purpose", probe)
        self.assertIn("/v1/models", probe)
        self.assertIn("tool_calls", probe)
        self.assertIn("response_format", probe)
        self.assertIn("usage", probe)

    def test_dynamic_tests_use_a_unique_project_port_and_scoped_cleanup(self) -> None:
        script = TEST_SCRIPT.read_text(encoding="utf-8")
        self.assertIn(
            'COMPOSE_PROJECT_NAME="agent-platform-litellm-test-$$"',
            script,
        )
        self.assertNotIn("LITELLM_TEST_PROJECT_NAME", script)
        self.assertIn("20_000 + secrets.randbelow(10_000)", script)
        self.assertIn('sock.bind(("127.0.0.1", candidate))', script)
        self.assertIn('docker compose -p "${COMPOSE_PROJECT_NAME}"', script)
        self.assertIn("down --volumes --remove-orphans", script)
        self.assertIn("logs --no-color", script)
        self.assertIn("trap cleanup_on_exit EXIT", script)
        self.assertNotIn('COMPOSE_PROJECT_NAME="agent-platform-litellm"', script)
        self.assertNotIn('python3 - "${LITELLM_PORT}" "${LITELLM_WORKER_API_KEY}"', script)
        self.assertIn('os.environ["LITELLM_WORKER_API_KEY"]', script)

    def test_dynamic_tests_isolate_and_guard_the_external_network(self) -> None:
        compose_source = COMPOSE_FILE.read_text(encoding="utf-8")
        self.assertIn("name: ${LITELLM_NETWORK_NAME:-agent-platform-llm}", compose_source)
        self.assertEqual(self.config["networks"]["llm"]["name"], "agent-platform-llm")
        self.assertTrue(self.config["networks"]["database"]["internal"])
        self.assertEqual(set(self.config["services"]["litellm-db"]["networks"]), {"database"})

        network = NETWORK_SCRIPT.read_text(encoding="utf-8")
        self.assertIn('NETWORK_NAME="${LITELLM_NETWORK_NAME:-agent-platform-llm}"', network)

        script = TEST_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("agent-platform-litellm-test-*", script)
        self.assertIn('LITELLM_NETWORK_NAME="${COMPOSE_PROJECT_NAME}-llm"', script)
        self.assertIn("cleanup_test_network", script)
        self.assertIn(
            '[[ "${LITELLM_NETWORK_NAME}" != "${COMPOSE_PROJECT_NAME}-llm" ]]',
            script,
        )
        self.assertIn('[[ "${LITELLM_NETWORK_NAME}" == "agent-platform-llm" ]]', script)
        self.assertIn('docker network rm "${LITELLM_NETWORK_NAME}"', script)
        cleanup_body = script.split("cleanup_runtime() {", 1)[1].split("\n}", 1)[0]
        self.assertLess(
            cleanup_body.index("down --volumes --remove-orphans"),
            cleanup_body.index("cleanup_test_network"),
        )

    def test_public_config_exposes_one_provider_neutral_alias(self) -> None:
        config = LITELLM_CONFIG.read_text(encoding="utf-8")
        self.assertEqual(config.count("model_name:"), 1)
        self.assertIn("model_name: general-purpose", config)
        self.assertIn("model: os.environ/LITELLM_UPSTREAM_MODEL", config)
        self.assertIn("api_key: os.environ/LITELLM_UPSTREAM_API_KEY", config)
        self.assertIn("api_base: os.environ/LITELLM_UPSTREAM_API_BASE", config)
        self.assertIn("master_key: os.environ/LITELLM_MASTER_KEY", config)
        self.assertNotIn("model_name: openai", config.lower())
        self.assertNotIn("model_name: anthropic", config.lower())
        self.assertNotRegex(config, r"(?i)(?:api_key|master_key):\s+sk-")

    def test_local_stub_override_is_test_only_and_not_published(self) -> None:
        result = subprocess.run(
            [
                "docker",
                "compose",
                "--env-file",
                str(COMPOSE_ENV),
                "-f",
                str(COMPOSE_FILE),
                "-f",
                str(STUB_COMPOSE_FILE),
                "--profile",
                "worker-e2e",
                "config",
                "--format",
                "json",
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        config = json.loads(result.stdout)
        self.assertEqual(
            set(config["services"]),
            {
                "litellm",
                "litellm-db",
                "worker-key-bootstrap",
                "openai-stub",
                "legacy-worker-key-seed",
                "worker-gateway-probe",
            },
        )
        stub = config["services"]["openai-stub"]
        self.assertEqual(stub["image"], EXPECTED_IMAGE)
        self.assertNotIn("ports", stub)
        self.assertEqual(stub["restart"], "no")
        self.assertTrue(stub["read_only"])
        self.assertEqual(len(stub["volumes"]), 1)
        self.assertEqual(Path(stub["volumes"][0]["source"]), STUB_SERVER)
        self.assertTrue(stub["volumes"][0]["read_only"])

        environment = config["services"]["litellm"]["environment"]
        self.assertEqual(environment["LITELLM_UPSTREAM_MODEL"], "openai/local-test")
        self.assertEqual(environment["LITELLM_UPSTREAM_API_BASE"], "http://openai-stub:4010/v1")
        self.assertEqual(environment["LITELLM_UPSTREAM_API_KEY"], "stub-not-a-provider-key")

        config_mount = next(
            volume
            for volume in config["services"]["litellm"]["volumes"]
            if volume["target"] == "/app/config.yaml"
        )
        self.assertEqual(Path(config_mount["source"]), STUB_CONFIG)
        self.assertTrue(config_mount["read_only"])

        self.assertTrue(STUB_CONFIG.is_file(), "missing test-only fallback config")
        stub_config = STUB_CONFIG.read_text(encoding="utf-8")
        self.assertIn("model_name: general-purpose-fallback", stub_config)
        self.assertIn("fallbacks:", stub_config)
        self.assertIn("http://openai-stub:4010/primary/v1", stub_config)
        self.assertIn("http://openai-stub:4010/fallback/v1", stub_config)
        stub_script = STUB_SERVER.read_text(encoding="utf-8")
        self.assertIn('"/primary/v1/chat/completions"', stub_script)
        self.assertIn('"/fallback/v1/chat/completions"', stub_script)
        probe = WORKER_PROBE.read_text(encoding="utf-8")
        self.assertIn('"content": "fallback"', probe)
        self.assertIn("local fallback completion", probe)

    def test_local_stub_protocol_regressions_are_part_of_the_config_gate(self) -> None:
        self.assertTrue(STUB_PROTOCOL_TEST.is_file(), "missing local Stub protocol tests")
        script = TEST_SCRIPT.read_text(encoding="utf-8")
        self.assertIn('python3 "${ROOT_DIR}/infra/litellm/test_openai_stub.py"', script)

    def test_dynamic_worker_flow_runs_one_shot_bootstrap_twice_explicitly(self) -> None:
        script = TEST_SCRIPT.read_text(encoding="utf-8")
        self.assertIn(
            "compose_stub up -d --wait --wait-timeout 240 litellm openai-stub",
            script,
        )
        self.assertGreaterEqual(
            script.count("compose_stub run --rm --no-deps worker-key-bootstrap"),
            2,
        )

    def test_readiness_and_matrix_use_locked_host_probe(self) -> None:
        script = TEST_SCRIPT.read_text(encoding="utf-8")
        self.assertIn('uv run --project "${ROOT_DIR}/backend" --frozen --no-dev', script)
        self.assertIn("worker-readiness)\n        run_host_probe readiness", script)
        self.assertNotIn("worker-readiness)\n        run_worker_probe readiness", script)
        self.assertIn("worker-chat)\n        run_worker_probe chat", script)
        self.assertIn("stub-matrix)\n        run_host_probe matrix", script)
        self.assertNotIn("stub-matrix)\n        run_worker_probe matrix", script)

        stub = STUB_SERVER.read_text(encoding="utf-8")
        self.assertIn("last_message = messages[-1]", stub)
        self.assertIn('scenario = last_message.get("content")', stub)
        self.assertNotIn('request.get("user")', stub)

    def test_legacy_broad_key_is_recreated_and_scope_is_probed(self) -> None:
        bootstrap = BOOTSTRAP_SCRIPT.read_text(encoding="utf-8")
        self.assertGreaterEqual(bootstrap.count('info.get("key_type")'), 2)
        self.assertIn('"llm_api_routes"', bootstrap)
        self.assertIn('call("POST", "/key/delete", {"keys": [key_hash]})', bootstrap)
        self.assertIn("worker key bootstrap verification mismatch for key_type", bootstrap)
        self.assertNotIn('{"keys": [WORKER_KEY]}', bootstrap)

        self.assertTrue(LEGACY_KEY_SEED.is_file(), "missing legacy-key seed helper")
        seed = LEGACY_KEY_SEED.read_text(encoding="utf-8")
        self.assertIn('os.environ["LITELLM_MASTER_KEY"]', seed)
        self.assertIn('os.environ["LITELLM_WORKER_API_KEY"]', seed)
        self.assertIn('"key_type": "llm_api"', seed)
        self.assertNotIn("sys.argv", seed)
        self.assertNotRegex(seed, r"SystemExit\([^\n]*(?:response|WORKER_KEY|MASTER_KEY)")

        script = TEST_SCRIPT.read_text(encoding="utf-8")
        worker_flow = script.split("worker-readiness|worker-chat|stub-matrix)", 1)[1]
        self.assertLess(
            worker_flow.index("legacy-worker-key-seed"),
            worker_flow.index("worker-key-bootstrap"),
        )

        probe = WORKER_PROBE.read_text(encoding="utf-8")
        self.assertIn('model_ids != {"general-purpose"}', probe)
        self.assertIn('"model": "general-purpose-fallback"', probe)
        self.assertIn('f"{BASE_URL}/embeddings"', probe)


if __name__ == "__main__":
    unittest.main()
