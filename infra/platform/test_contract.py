from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import tempfile
import textwrap
import unittest
import uuid
from pathlib import Path

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
        self.assertIn("libpq5", dockerfile)
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
        self.assertRegex(compose, r"127\.0\.0\.1:\$\{PLATFORM_API_PORT:-8000\}:8000")
        self.assertRegex(compose, r"127\.0\.0\.1:\$\{PLATFORM_WEB_PORT:-8080\}:8080")
        self.assertGreaterEqual(compose.count("healthcheck:"), 2)
        sensitive_assignment = re.compile(r"^[A-Z0-9_]*(?:API_KEY|SECRET|PASSWORD)$")
        for line in compose.splitlines():
            key, separator, value = line.strip().partition(":")
            if separator and sensitive_assignment.fullmatch(key):
                self.assertTrue(value.strip().startswith("${"), f"literal secret in: {line}")

    def test_compose_uses_external_network_for_separate_core_stack(self) -> None:
        compose = self.read("infra/compose/platform.yml")
        self.assertRegex(compose, r"external:\s*true")
        self.assertIn("name: ${CORE_NETWORK_NAME:-agent-platform_default}", compose)
        self.assertIn("@postgres:5432", compose)
        self.assertIn("@redis:6379", compose)
        self.assertIn("minio:9000", compose)
        self.assertIn("host.docker.internal:host-gateway", compose)
        self.assertIn("AGENT_PLATFORM_DATABASE_URL", compose)
        self.assertIn("AGENT_PLATFORM_REDIS_URL", compose)
        self.assertIn("AGENT_PLATFORM_MINIO_ENDPOINT", compose)
        self.assertIn("name: ${LITELLM_NETWORK_NAME:-agent-platform-llm}", compose)
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

    def test_mvp_profile_is_stub_only_ragflow_free_and_failure_safe(self) -> None:
        script = self.read("infra/platform/mvp-profile.sh")
        self.assertIn("set -euo pipefail", script)
        self.assertIn("infra/compose/core.yml", script)
        self.assertIn("infra/compose/litellm.yml", script)
        self.assertIn("infra/litellm/compose.stub.yml", script)
        self.assertIn("infra/compose/platform.yml", script)
        self.assertIn("--profile worker", script)
        self.assertIn("openai-stub", script)
        self.assertIn("worker-key-bootstrap", script)
        self.assertIn("--wait", script)
        self.assertIn("cleanup_failed_start", script)
        self.assertNotRegex(
            script,
            r"(?:app|litellm|core)_compose down[^\n]*\|\| true",
        )
        self.assertNotIn("infra/ragflow", script)
        self.assertNotIn("TokenHub", script)
        self.assertNotIn("LITELLM_UPSTREAM_API_KEY", script)
        self.assertNotRegex(script, r'(?m)^\s*source\s+"\$\{[^}]*ENV_FILE\}"')
        self.assertIn("load_dotenv_file", script)
        self.assertIn("validate_runtime_directory", script)
        self.assertIn("acquire_profile_lock", script)
        self.assertIn("release_profile_lock", script)
        self.assertIn("cleanup_failed_start", script)
        self.assertIn("PREEXISTING_VOLUME_NAMES", script)
        self.assertNotIn("stop_profile >/dev/null 2>&1 || true", script)
        self.assertNotIn("|| true", script)
        for mode in ("start", "stop", "health", "status"):
            self.assertRegex(script, rf"(?m)^  {mode}\)")

    def test_mvp_profile_uses_worktree_specific_images(self) -> None:
        script = self.read("infra/platform/mvp-profile.sh")
        compose = self.read("infra/compose/platform.yml")
        self.assertIn("WORKTREE_IMAGE_ID", script)
        self.assertIn("PLATFORM_BACKEND_IMAGE", script)
        self.assertIn("PLATFORM_FRONTEND_IMAGE", script)
        self.assertIn("app_compose build", script)
        self.assertIn("--no-build", script)
        self.assertIn("image: ${PLATFORM_BACKEND_IMAGE", compose)
        self.assertEqual(compose.count("image: ${PLATFORM_BACKEND_IMAGE"), 6)
        self.assertIn("image: ${PLATFORM_FRONTEND_IMAGE", compose)

    def test_mvp_profile_supports_isolated_compose_projects_and_networks(self) -> None:
        script = self.read("infra/platform/mvp-profile.sh")
        self.assertIn("MVP_PROFILE_NAME", script)
        self.assertIn("MVP_PROFILE_RUNTIME_DIR", script)
        self.assertIn('CORE_NETWORK_NAME="${CORE_PROJECT}_default"', script)
        self.assertIn('LITELLM_NETWORK_NAME="${PROFILE_NAME}-llm"', script)
        self.assertIn('-p "${CORE_PROJECT}"', script)
        self.assertIn('-p "${LITELLM_PROJECT}"', script)
        self.assertIn('-p "${APP_PROJECT}"', script)

        compose = self.read("infra/compose/platform.yml")
        self.assertIn("name: ${CORE_NETWORK_NAME:-agent-platform_default}", compose)
        self.assertIn("name: ${LITELLM_NETWORK_NAME:-agent-platform-llm}", compose)

    def test_mvp_profile_has_real_repeatable_acceptance_entry(self) -> None:
        acceptance = self.read("infra/platform/test-mvp-profile.sh")
        self.assertIn("mvp-profile.sh", acceptance)
        self.assertIn("start", acceptance)
        self.assertIn("health", acceptance)
        self.assertIn("stop", acceptance)
        self.assertIn("trap cleanup EXIT", acceptance)
        self.assertIn("openai-stub", acceptance)
        self.assertIn("worker_gateway_probe.py", acceptance)
        self.assertIn('"chat"', acceptance)
        self.assertIn("sandbox-controller", acceptance)
        self.assertIn("sandbox-janitor", acceptance)
        self.assertIn("ragflow", acceptance.lower())
        self.assertIn("status", acceptance)
        self.assertIn("assert_ports_are_unique", acceptance)
        self.assertIn("assert_profile_volumes_exist", acceptance)
        self.assertIn("assert_profile_volumes_absent", acceptance)
        self.assertIn("concurrent start", acceptance)
        self.assertIn("failed restart", acceptance)
        self.assertRegex(
            acceptance,
            r'elif \[\[ "\$\{cleanup_exit\}" == "0" \]\]; then\s+rm -rf',
        )
        self.assertNotIn('source "${RUNTIME_DIR}/litellm.env"', acceptance)


class MvpProfileLifecycleBehaviorTest(unittest.TestCase):
    def setUp(self) -> None:
        local_root = ROOT / ".local"
        local_root.mkdir(mode=0o700, exist_ok=True)
        self.test_root = Path(tempfile.mkdtemp(prefix="mvp-profile-contract-", dir=local_root))
        self.test_root.chmod(0o700)
        self.runtime_dir = self.test_root / "runtime"
        self.runtime_dir.mkdir(mode=0o700)
        self.fake_bin = self.test_root / "bin"
        self.fake_bin.mkdir(mode=0o700)
        self.docker_log = self.test_root / "docker.log"
        self.docker_state = self.test_root / "docker-state"
        self.docker_state.mkdir(mode=0o700)
        fake_docker = self.fake_bin / "docker"
        fake_docker.write_text(
            textwrap.dedent(
                """\
                #!/bin/bash
                set -eu
                printf '%s\\n' "$*" >>"${FAKE_DOCKER_LOG}"
                state_dir="${FAKE_DOCKER_STATE}"
                project_from_args() {
                  local previous=""
                  local argument
                  for argument in "$@"; do
                    if [[ "${previous}" == "-p" ]]; then
                      printf '%s\\n' "${argument}"
                      return
                    fi
                    previous="${argument}"
                  done
                }
                project_from_filter() {
                  local argument
                  for argument in "$@"; do
                    case "${argument}" in
                      label=com.docker.compose.project=*)
                        printf '%s\\n' "${argument##*=}"
                        return
                        ;;
                    esac
                  done
                }
                stack_from_project() {
                  case "$1" in
                    *-core) printf 'core\\n' ;;
                    *-litellm) printf 'litellm\\n' ;;
                    *-app) printf 'app\\n' ;;
                    *) printf 'unknown\\n' ;;
                  esac
                }
                stack_is_preexisting() {
                  case " ${FAKE_PREEXISTING_STACKS:-} " in
                    *" $1 "*) return 0 ;;
                    *) return 1 ;;
                  esac
                }
                list_stack_resource() {
                  local kind="$1"
                  local project="$2"
                  local stack
                  stack="$(stack_from_project "${project}")"
                  if stack_is_preexisting "${stack}"; then
                    printf 'old-%s-%s\\n' "${stack}" "${kind}"
                  fi
                  if [[ -f "${state_dir}/${project}.created" ]]; then
                    printf 'new-%s-%s\\n' "${stack}" "${kind}"
                  fi
                }
                if [[ "${1:-}" == "info" ]]; then
                  exit 0
                fi
                if [[ "${1:-}" == "volume" && "${2:-}" == "ls" ]]; then
                  if [[ "${FAKE_VOLUME_LS_FAIL_WHEN_CREATED:-0}" == "1" ]] &&
                    find "${state_dir}" -name '*.created' -print -quit | grep -q .; then
                    exit 71
                  fi
                  project="$(project_from_filter "$@")"
                  if [[ -n "${project}" ]]; then
                    list_stack_resource volume "${project}"
                  fi
                  if [[ -n "${FAKE_VOLUME_NAMES:-}" ]]; then
                    printf '%s\\n' "${FAKE_VOLUME_NAMES}"
                  fi
                  exit 0
                fi
                if [[ "${1:-}" == "volume" && "${2:-}" == "rm" ]]; then
                  exit 0
                fi
                if [[ "${1:-}" == "ps" ]]; then
                  project="$(project_from_filter "$@")"
                  if [[ -n "${project}" ]]; then
                    list_stack_resource container "${project}"
                    if [[ "${FAKE_PROFILE_CONTAINER:-0}" == "1" ]]; then
                      printf 'existing-container\\n'
                    fi
                  elif [[ "${FAKE_PROFILE_CONTAINER:-0}" == "1" ]]; then
                    printf 'existing-container\\n'
                  fi
                  exit 0
                fi
                if [[ "${1:-}" == "rm" ]]; then
                  exit 0
                fi
                if [[ "${1:-}" == "network" && "${2:-}" == "inspect" ]]; then
                  if [[ "${FAKE_NETWORK_INSPECT_ERROR:-0}" == "1" ]]; then
                    exit 125
                  fi
                  if [[ "${FAKE_NETWORK_INSPECT_RC_ONE:-0}" == "1" ]]; then
                    exit 1
                  fi
                  if [[ -f "${state_dir}/external-network.name" ]]; then
                    if [[ " $* " == *" --format "* ]]; then
                      if [[ "${FAKE_NETWORK_LABEL_INSPECT_ERROR:-0}" == "1" ]]; then
                        exit 125
                      fi
                      printf '%s\\n' "${FAKE_NETWORK_OWNER:-${MVP_PROFILE_NAME}}"
                    fi
                    exit 0
                  fi
                  exit 1
                fi
                if [[ "${1:-}" == "network" && "${2:-}" == "ls" ]]; then
                  if [[ "${FAKE_LLM_NETWORK_LS_FAIL:-0}" == "1" ]] &&
                    [[ " $* " == *" name=^"* ]]; then
                    exit 73
                  fi
                  if [[ "${FAKE_NETWORK_LS_FAIL:-0}" == "1" ]]; then
                    exit 72
                  fi
                  project="$(project_from_filter "$@")"
                  if [[ -n "${project}" ]]; then
                    list_stack_resource network "${project}"
                  elif [[ -f "${state_dir}/external-network.name" ]]; then
                    cat "${state_dir}/external-network.name"
                  fi
                  exit 0
                fi
                if [[ "${1:-}" == "network" && "${2:-}" == "create" ]]; then
                  printf '%s\\n' "${!#}" >"${state_dir}/external-network.name"
                  exit 0
                fi
                if [[ "${1:-}" == "network" && "${2:-}" == "rm" ]]; then
                  [[ "${FAKE_NETWORK_RM_FAIL:-0}" != "1" ]]
                  exit
                fi
                if [[ "${1:-}" == "inspect" ]]; then
                  if [[ "${FAKE_DOCKER_INSPECT_FAIL_RC:-0}" != "0" ]]; then
                    exit "${FAKE_DOCKER_INSPECT_FAIL_RC}"
                  fi
                  printf 'healthy\\n'
                  exit 0
                fi
                if [[ "${1:-}" == "compose" ]]; then
                  project="$(project_from_args "$@")"
                  stack="$(stack_from_project "${project}")"
                  case " $* " in
                    *" config --quiet "*)
                      [[ "${FAKE_COMPOSE_CONFIG_FAIL:-0}" != "1" ]]
                      exit
                      ;;
                    *" build "*)
                      [[ "${FAKE_COMPOSE_BUILD_FAIL:-0}" != "1" ]]
                      exit
                      ;;
                    *" ps --quiet "*)
                      if [[ "${FAKE_COMPOSE_PS_FAIL_STACK:-}" == "${stack}" ]]; then
                        exit "${FAKE_COMPOSE_PS_FAIL_RC:-1}"
                      fi
                      if [[ "${FAKE_COMPOSE_PS_IDS:-0}" == "1" ]]; then
                        printf 'existing-container\\n'
                      fi
                      exit 0
                      ;;
                    *" up "*)
                      touch "${state_dir}/${project}.created"
                      if [[ "${FAKE_START_SIGNAL_STACK:-}" == "${stack}" ]]; then
                        kill -s "${FAKE_START_SIGNAL}" "${PPID}"
                        exit 0
                      fi
                      if [[ "${FAKE_FAIL_UP_STACK:-}" == "${stack}" ]]; then
                        exit "${FAKE_FAIL_UP_RC:-1}"
                      fi
                      exit
                      ;;
                    *) exit 0 ;;
                  esac
                fi
                exit 0
                """
            ),
            encoding="utf-8",
        )
        fake_docker.chmod(0o700)
        fake_curl = self.fake_bin / "curl"
        fake_curl.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        fake_curl.chmod(0o700)

    def tearDown(self) -> None:
        shutil.rmtree(self.test_root, ignore_errors=True)

    def run_profile(
        self,
        command: str,
        *,
        extra_env: dict[str, str] | None = None,
        profile_name: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        profile_name = profile_name or f"mvp-contract-{uuid.uuid4().hex[:12]}"
        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{self.fake_bin}:/usr/bin:/bin",
                "FAKE_DOCKER_LOG": str(self.docker_log),
                "FAKE_DOCKER_STATE": str(self.docker_state),
                "MVP_PROFILE_NAME": profile_name,
                "MVP_PROFILE_RUNTIME_DIR": str(self.runtime_dir),
                "POSTGRES_PORT": "22001",
                "REDIS_PORT": "22002",
                "MINIO_API_PORT": "22003",
                "MINIO_CONSOLE_PORT": "22004",
                "LITELLM_PORT": "22005",
                "PLATFORM_API_PORT": "22006",
                "PLATFORM_WEB_PORT": "22007",
            }
        )
        if extra_env is not None:
            env.update(extra_env)
        return subprocess.run(
            ["/bin/bash", str(ROOT / "infra/platform/mvp-profile.sh"), command],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )

    def initialize_profile_environment(self) -> str:
        profile_name = f"mvp-contract-{uuid.uuid4().hex[:12]}"
        result = self.run_profile(
            "start",
            extra_env={"FAKE_COMPOSE_PS_IDS": "1"},
            profile_name=profile_name,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return profile_name

    def docker_calls(self) -> list[str]:
        if not self.docker_log.exists():
            return []
        return self.docker_log.read_text(encoding="utf-8").splitlines()

    def assert_failed_start_cleanup(
        self,
        result: subprocess.CompletedProcess[str],
        *,
        profile_name: str,
    ) -> None:
        calls = self.docker_calls()
        self.assertIn("cleanup completed", result.stderr)
        self.assertEqual(
            result.stderr.count("MVP profile failed-start cleanup completed"),
            1,
            result.stderr,
        )
        self.assertEqual(
            result.stderr.count("MVP profile start failed safely"),
            1,
            result.stderr,
        )
        self.assertNotIn("MVP profile started", result.stdout)
        self.assertTrue(
            any("rm" in call and "new-app-container" in call for call in calls),
            calls,
        )
        self.assertEqual(
            sum("rm" in call and "new-app-container" in call for call in calls),
            1,
            calls,
        )
        self.assertTrue(
            any("volume rm" in call and "new-app-volume" in call for call in calls),
            calls,
        )
        self.assertFalse((ROOT / ".local/mvp-profile-locks" / f"{profile_name}.lock").exists())

    def test_start_sigint_exits_130_after_cleanup_and_releases_lock(self) -> None:
        profile_name = f"mvp-contract-{uuid.uuid4().hex[:12]}"

        result = self.run_profile(
            "start",
            extra_env={
                "FAKE_START_SIGNAL_STACK": "app",
                "FAKE_START_SIGNAL": "INT",
            },
            profile_name=profile_name,
        )

        self.assertEqual(result.returncode, 130, result.stderr)
        self.assert_failed_start_cleanup(result, profile_name=profile_name)

    def test_start_sigterm_exits_143_after_cleanup_and_releases_lock(self) -> None:
        profile_name = f"mvp-contract-{uuid.uuid4().hex[:12]}"

        result = self.run_profile(
            "start",
            extra_env={
                "FAKE_START_SIGNAL_STACK": "app",
                "FAKE_START_SIGNAL": "TERM",
            },
            profile_name=profile_name,
        )

        self.assertEqual(result.returncode, 143, result.stderr)
        self.assert_failed_start_cleanup(result, profile_name=profile_name)

    def test_start_err_preserves_original_status_and_cleans_created_resources(
        self,
    ) -> None:
        profile_name = f"mvp-contract-{uuid.uuid4().hex[:12]}"

        result = self.run_profile(
            "start",
            extra_env={
                "FAKE_FAIL_UP_STACK": "app",
                "FAKE_FAIL_UP_RC": "47",
            },
            profile_name=profile_name,
        )

        self.assertEqual(result.returncode, 47, result.stderr)
        self.assert_failed_start_cleanup(result, profile_name=profile_name)

    def test_start_err_from_compose_ps_cleans_once_and_preserves_status(self) -> None:
        profile_name = f"mvp-contract-{uuid.uuid4().hex[:12]}"

        result = self.run_profile(
            "start",
            extra_env={
                "FAKE_COMPOSE_PS_FAIL_STACK": "core",
                "FAKE_COMPOSE_PS_FAIL_RC": "48",
            },
            profile_name=profile_name,
        )

        self.assertEqual(result.returncode, 48, result.stderr)
        self.assert_failed_start_cleanup(result, profile_name=profile_name)

    def test_start_err_from_docker_inspect_cleans_once_and_preserves_status(
        self,
    ) -> None:
        profile_name = f"mvp-contract-{uuid.uuid4().hex[:12]}"

        result = self.run_profile(
            "start",
            extra_env={
                "FAKE_COMPOSE_PS_IDS": "1",
                "FAKE_DOCKER_INSPECT_FAIL_RC": "49",
            },
            profile_name=profile_name,
        )

        self.assertEqual(result.returncode, 49, result.stderr)
        self.assert_failed_start_cleanup(result, profile_name=profile_name)

    def test_failed_start_without_rg_preserves_preexisting_volume(self) -> None:
        result = self.run_profile(
            "start",
            extra_env={
                "FAKE_VOLUME_NAMES": "preexisting-volume",
                "FAKE_COMPOSE_CONFIG_FAIL": "1",
            },
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(
            "volume rm preexisting-volume",
            self.docker_calls(),
            result.stderr,
        )

    def test_stop_fails_closed_when_environment_is_missing_but_resources_exist(self) -> None:
        result = self.run_profile(
            "stop",
            extra_env={"FAKE_PROFILE_CONTAINER": "1"},
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("already stopped", result.stdout)
        self.assertFalse(any(" down " in f" {call} " for call in self.docker_calls()))

    def test_failed_repeat_start_does_not_take_down_existing_stack(self) -> None:
        result = self.run_profile(
            "start",
            extra_env={
                "FAKE_PROFILE_CONTAINER": "1",
                "FAKE_COMPOSE_CONFIG_FAIL": "1",
            },
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(
            any(" down " in f" {call} " for call in self.docker_calls()),
            result.stderr,
        )

    def test_start_rejects_occupied_ports_before_compose_up(self) -> None:
        listeners = []
        try:
            ports = []
            for _ in range(7):
                listener = socket.socket()
                listener.bind(("127.0.0.1", 0))
                listener.listen()
                listeners.append(listener)
                ports.append(str(listener.getsockname()[1]))
            port_names = (
                "POSTGRES_PORT",
                "REDIS_PORT",
                "MINIO_API_PORT",
                "MINIO_CONSOLE_PORT",
                "LITELLM_PORT",
                "PLATFORM_API_PORT",
                "PLATFORM_WEB_PORT",
            )
            self.assertEqual(len(port_names), len(ports))
            result = self.run_profile(
                "start",
                extra_env={name: ports[index] for index, name in enumerate(port_names)},
            )
        finally:
            for listener in listeners:
                listener.close()

        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(
            any(" up " in f" {call} " for call in self.docker_calls()),
            result.stderr,
        )

    def test_failed_partial_start_removes_only_resources_created_this_run(self) -> None:
        result = self.run_profile(
            "start",
            extra_env={
                "FAKE_PREEXISTING_STACKS": "core",
                "FAKE_FAIL_UP_STACK": "app",
            },
        )

        calls = self.docker_calls()
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(
            any("rm" in call and "new-litellm-container" in call for call in calls),
            f"stderr={result.stderr!r}; docker_calls={calls!r}",
        )
        self.assertTrue(any("rm" in call and "new-app-container" in call for call in calls))
        self.assertTrue(
            any("network rm" in call and "new-litellm-network" in call for call in calls)
        )
        self.assertTrue(any("network rm" in call and "new-app-network" in call for call in calls))
        self.assertTrue(any("network rm" in call and call.endswith("-llm") for call in calls))
        self.assertTrue(any("volume rm" in call and "new-litellm-volume" in call for call in calls))
        self.assertTrue(any("volume rm" in call and "new-app-volume" in call for call in calls))
        self.assertFalse(any("old-core" in call and " rm " in f" {call} " for call in calls))
        self.assertIn("cleanup completed", result.stderr)

    def test_stop_reports_network_enumeration_failure(self) -> None:
        result = self.run_profile(
            "stop",
            extra_env={"FAKE_LLM_NETWORK_LS_FAIL": "1"},
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("already stopped", result.stdout)

    def test_start_fails_closed_when_llm_network_existence_inspect_errors(self) -> None:
        profile_name = f"mvp-contract-{uuid.uuid4().hex[:12]}"
        (self.docker_state / "external-network.name").write_text(
            f"{profile_name}-llm\n",
            encoding="utf-8",
        )

        result = self.run_profile(
            "start",
            extra_env={"FAKE_NETWORK_INSPECT_ERROR": "1"},
            profile_name=profile_name,
        )

        calls = self.docker_calls()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("failed to inspect MVP LiteLLM network owner", result.stderr)
        self.assertFalse(any("network create" in call for call in calls), calls)
        self.assertFalse(any(" up " in f" {call} " for call in calls), calls)

    def test_start_fails_closed_when_list_finds_network_but_inspect_returns_one(
        self,
    ) -> None:
        profile_name = f"mvp-contract-{uuid.uuid4().hex[:12]}"
        (self.docker_state / "external-network.name").write_text(
            f"{profile_name}-llm\n",
            encoding="utf-8",
        )

        result = self.run_profile(
            "start",
            extra_env={"FAKE_NETWORK_INSPECT_RC_ONE": "1"},
            profile_name=profile_name,
        )

        calls = self.docker_calls()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("failed to inspect MVP LiteLLM network owner", result.stderr)
        self.assertFalse(any("network create" in call for call in calls), calls)
        self.assertFalse(any(" up " in f" {call} " for call in calls), calls)

    def test_stop_fails_closed_when_llm_network_existence_inspect_errors(self) -> None:
        profile_name = self.initialize_profile_environment()

        result = self.run_profile(
            "stop",
            extra_env={"FAKE_NETWORK_INSPECT_ERROR": "1"},
            profile_name=profile_name,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("failed to remove MVP LiteLLM network", result.stderr)
        self.assertNotIn("MVP profile stopped", result.stdout)

    def test_stop_fails_closed_when_list_finds_network_but_inspect_returns_one(
        self,
    ) -> None:
        profile_name = self.initialize_profile_environment()

        result = self.run_profile(
            "stop",
            extra_env={"FAKE_NETWORK_INSPECT_RC_ONE": "1"},
            profile_name=profile_name,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("failed to remove MVP LiteLLM network", result.stderr)
        self.assertFalse(any("network rm" in call for call in self.docker_calls()))
        self.assertNotIn("MVP profile stopped", result.stdout)

    def test_stop_fails_closed_when_llm_network_label_inspect_errors(self) -> None:
        profile_name = self.initialize_profile_environment()

        result = self.run_profile(
            "stop",
            extra_env={"FAKE_NETWORK_LABEL_INSPECT_ERROR": "1"},
            profile_name=profile_name,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("failed to remove MVP LiteLLM network", result.stderr)
        self.assertNotIn("MVP profile stopped", result.stdout)

    def test_stop_fails_closed_and_preserves_foreign_llm_network(self) -> None:
        profile_name = self.initialize_profile_environment()

        result = self.run_profile(
            "stop",
            extra_env={"FAKE_NETWORK_OWNER": "another-profile"},
            profile_name=profile_name,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("refusing to remove unmanaged network", result.stderr)
        self.assertFalse(any("network rm" in call for call in self.docker_calls()))
        self.assertNotIn("MVP profile stopped", result.stdout)

    def test_stop_propagates_llm_network_remove_failure(self) -> None:
        profile_name = self.initialize_profile_environment()

        result = self.run_profile(
            "stop",
            extra_env={"FAKE_NETWORK_RM_FAIL": "1"},
            profile_name=profile_name,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("failed to remove MVP LiteLLM network", result.stderr)
        self.assertNotIn("MVP profile stopped", result.stdout)

    def test_stop_is_idempotent_when_llm_network_does_not_exist(self) -> None:
        profile_name = self.initialize_profile_environment()
        (self.docker_state / "external-network.name").unlink()

        result = self.run_profile("stop", profile_name=profile_name)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("MVP profile stopped", result.stdout)

    def test_failed_start_reports_incomplete_when_volume_enumeration_fails(self) -> None:
        result = self.run_profile(
            "start",
            extra_env={
                "FAKE_FAIL_UP_STACK": "core",
                "FAKE_VOLUME_LS_FAIL_WHEN_CREATED": "1",
            },
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cleanup was incomplete", result.stderr)
        self.assertNotIn("cleanup completed", result.stderr)

    def test_preexisting_core_still_checks_litellm_port_before_litellm_up(self) -> None:
        listener = socket.socket()
        try:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            result = self.run_profile(
                "start",
                extra_env={
                    "FAKE_PREEXISTING_STACKS": "core",
                    "LITELLM_PORT": str(listener.getsockname()[1]),
                },
            )
        finally:
            listener.close()

        calls = self.docker_calls()
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(any("-core" in call and " up " in f" {call} " for call in calls))
        self.assertFalse(any("-litellm" in call and " up " in f" {call} " for call in calls))

    def test_preexisting_core_and_litellm_still_check_app_port_before_app_up(self) -> None:
        listener = socket.socket()
        try:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            result = self.run_profile(
                "start",
                extra_env={
                    "FAKE_PREEXISTING_STACKS": "core litellm",
                    "PLATFORM_API_PORT": str(listener.getsockname()[1]),
                },
            )
        finally:
            listener.close()

        calls = self.docker_calls()
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(any("-core" in call and " up " in f" {call} " for call in calls))
        self.assertTrue(any("-litellm" in call and " up " in f" {call} " for call in calls))
        self.assertFalse(any("-app" in call and " up " in f" {call} " for call in calls))


if __name__ == "__main__":
    unittest.main()
