from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]


class PlatformContainerContractTest(unittest.TestCase):
    def read(self, relative_path: str) -> str:
        path = ROOT / relative_path
        self.assertTrue(path.is_file(), f"missing required file: {relative_path}")
        return path.read_text(encoding="utf-8")

    def test_backend_image_is_reproducible_multistage_and_non_root(self) -> None:
        dockerfile = self.read("backend/Dockerfile")
        self.assertGreaterEqual(len(re.findall(r"(?m)^FROM ", dockerfile)), 2)
        self.assertIn("uv.lock", dockerfile)
        self.assertRegex(dockerfile, r"uv sync[^\n]*--frozen[^\n]*--no-dev")
        self.assertRegex(dockerfile, r"(?m)^USER (?!0$|root$).+$")
        self.assertNotRegex(dockerfile, r"(?im)^(?:ARG|ENV)\s+.*(?:SECRET|PASSWORD|API_KEY)")

    def test_frontend_image_is_reproducible_multistage_and_non_root(self) -> None:
        dockerfile = self.read("frontend/Dockerfile")
        self.assertGreaterEqual(len(re.findall(r"(?m)^FROM ", dockerfile)), 2)
        self.assertIn("pnpm-lock.yaml", dockerfile)
        self.assertRegex(dockerfile, r"pnpm install[^\n]*--frozen-lockfile")
        self.assertRegex(dockerfile, r"(?m)^USER (?!0$|root$).+$")
        self.assertNotRegex(dockerfile, r"(?im)^(?:ARG|ENV)\s+.*(?:SECRET|PASSWORD|API_KEY)")

    def test_web_server_proxies_api_and_supports_spa_routes(self) -> None:
        nginx = self.read("frontend/nginx.conf")
        self.assertRegex(nginx, r"listen\s+8080")
        self.assertRegex(nginx, r"location\s+/api/")
        self.assertIn("proxy_pass http://api:8000", nginx)
        self.assertRegex(nginx, r"try_files\s+\$uri\s+\$uri/\s+/index\.html")
        self.assertRegex(nginx, r"map\s+\$http_upgrade\s+\$connection_upgrade\s*{")
        self.assertRegex(nginx, r"default\s+upgrade;")
        self.assertRegex(nginx, r"''\s+close;")
        self.assertRegex(nginx, r"proxy_set_header\s+Connection\s+\$connection_upgrade;")
        self.assertNotIn('proxy_set_header Connection "upgrade"', nginx)

    def test_compose_has_migration_gate_profiles_health_and_loopback_ports(self) -> None:
        compose = self.read("infra/compose/platform.yml")
        for service in (
            "migrate",
            "api",
            "dispatcher",
            "worker",
            "sandbox-controller",
            "sandbox-janitor",
            "frontend",
        ):
            self.assertRegex(compose, rf"(?m)^  {service}:$")
        self.assertRegex(compose, r'command:\s*\["alembic",\s*"upgrade",\s*"head"\]')
        self.assertRegex(compose, r"condition:\s*service_completed_successfully")
        self.assertRegex(compose, r"profiles:\s*\[\"worker\"\]")
        self.assertIn("python", compose)
        self.assertIn("agent_platform.workers.main", compose)
        self.assertIn("agent_platform.workers.dispatcher_main", compose)
        self.assertIn('AGENT_PLATFORM_DISPATCHER_REPLICAS: "1"', compose)
        self.assertRegex(
            compose,
            r"test:\s*\[\"CMD\",\s*\"test\",\s*\"-f\",\s*"
            r"\"/tmp/agent-platform-dispatcher-ready\"\]",
        )
        self.assertNotIn("AGENT_PLATFORM_RUNTIME_ADAPTER_FACTORY", compose)
        self.assertIn('AGENT_PLATFORM_WORKER_REPLICAS: "1"', compose)
        self.assertIn('AGENT_PLATFORM_SANDBOX_JANITOR_REPLICAS: "1"', compose)
        self.assertIn("/tmp/agent-platform-worker-ready", compose)
        self.assertIn("/tmp/agent-platform-sandbox-janitor-ready", compose)
        self.assertEqual(compose.count(":/var/run/docker.sock"), 1)
        self.assertRegex(compose, r'127\.0\.0\.1:\$\{PLATFORM_API_PORT:-8000\}:8000')
        self.assertRegex(compose, r'127\.0\.0\.1:\$\{PLATFORM_WEB_PORT:-8080\}:8080')
        self.assertGreaterEqual(compose.count("healthcheck:"), 2)
        sensitive_assignment = re.compile(r"^[A-Z0-9_]*(?:API_KEY|SECRET|PASSWORD)$")
        for line in compose.splitlines():
            key, separator, value = line.strip().partition(":")
            if separator and sensitive_assignment.fullmatch(key):
                self.assertTrue(value.strip().startswith("${"), f"literal secret in: {line}")

    def test_compose_uses_external_network_for_separate_core_stack(self) -> None:
        compose = self.read("infra/compose/platform.yml")
        self.assertRegex(compose, r"external:\s*true")
        self.assertIn("name: agent-platform_default", compose)
        self.assertIn("@postgres:5432", compose)
        self.assertIn("@redis:6379", compose)
        self.assertIn("minio:9000", compose)
        self.assertIn("host.docker.internal:host-gateway", compose)
        self.assertIn("AGENT_PLATFORM_DATABASE_URL", compose)
        self.assertIn("AGENT_PLATFORM_REDIS_URL", compose)
        self.assertIn("AGENT_PLATFORM_MINIO_ENDPOINT", compose)
        self.assertIn("name: agent-platform-llm", compose)
        self.assertRegex(compose, r"llm:\s*\n\s+external:\s*true")

    def test_examples_do_not_contain_real_secrets(self) -> None:
        env_example = self.read("infra/compose/.env.platform.example")
        self.assertIn("CHANGE_ME", env_example)
        self.assertNotIn("ANTHROPIC_API_KEY", env_example)
        self.assertNotIn("OPENAI_API_KEY", env_example)
        self.assertIn(
            "AGENT_PLATFORM_LLM_GATEWAY_URL=http://litellm:4000/v1",
            env_example,
        )
        self.assertIn(
            "AGENT_PLATFORM_LLM_GATEWAY_API_KEY=sk-CHANGE_ME_LITELLM_WORKER_KEY",
            env_example,
        )
        self.assertNotRegex(env_example, r"(?m)^LITELLM_MASTER_KEY=")
        self.assertNotIn("AGENT_PLATFORM_RUNTIME_ADAPTER_FACTORY", env_example)

    def test_worker_only_receives_provider_neutral_llm_gateway_configuration(self) -> None:
        compose = self.read("infra/compose/platform.yml")
        worker = compose.split("\n  worker:\n", 1)[1].split("\n  sandbox-janitor:\n", 1)[0]
        self.assertNotIn("ANTHROPIC_API_KEY", worker)
        self.assertNotIn("OPENAI_API_KEY", worker)
        self.assertIn(
            "AGENT_PLATFORM_LLM_GATEWAY_URL: "
            "${AGENT_PLATFORM_LLM_GATEWAY_URL:-http://litellm:4000/v1}",
            worker,
        )
        self.assertIn(
            "AGENT_PLATFORM_LLM_GATEWAY_API_KEY: "
            "${AGENT_PLATFORM_LLM_GATEWAY_API_KEY:?set AGENT_PLATFORM_LLM_GATEWAY_API_KEY}",
            worker,
        )
        self.assertIn("networks: [core, sandbox-control, llm]", worker)
        self.assertNotIn("LITELLM_MASTER_KEY", worker)


if __name__ == "__main__":
    unittest.main()
