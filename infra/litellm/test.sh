#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COMPOSE_FILE="${ROOT_DIR}/infra/compose/litellm.yml"
COMPOSE_ENV="${ROOT_DIR}/infra/compose/.env.litellm.example"
STUB_COMPOSE_FILE="${ROOT_DIR}/infra/litellm/compose.stub.yml"
LITELLM_IMAGE="ghcr.io/berriai/litellm-non_root:v1.86.2@sha256:511b513bc68956793433d62c1812daff56984325543f6a15431c622823fd90cb"
LITELLM_PORT="${LITELLM_PORT:-4000}"
COMPOSE_PROJECT_NAME="agent-platform-litellm-test-$$"
case "${COMPOSE_PROJECT_NAME}" in
  agent-platform-litellm-test-*) ;;
  *)
    echo "Refusing unsafe LiteLLM test project: ${COMPOSE_PROJECT_NAME}" >&2
    exit 2
    ;;
esac
LITELLM_NETWORK_NAME="${COMPOSE_PROJECT_NAME}-llm"
export LITELLM_NETWORK_NAME
MIN_DOCKER_COMPOSE_VERSION="2.20.0"

usage() {
  echo "Usage: bash infra/litellm/test.sh {config|image-platform|start-health|stub-completion|worker-readiness|worker-chat|stub-matrix}" >&2
}

if [[ $# -ne 1 ]]; then
  usage
  exit 2
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker Compose v2 is required, but the docker command was not found." >&2
  exit 1
fi

if ! compose_version="$(docker compose version --short 2>/dev/null)"; then
  echo "Docker Compose v2 is required, but 'docker compose version' failed." >&2
  exit 1
fi

if ! python3 - "${compose_version}" "${MIN_DOCKER_COMPOSE_VERSION}" <<'PY'
import re
import sys


def parse_version(value: str) -> tuple[int, int, int]:
    match = re.match(r"^v?(\d+)\.(\d+)\.(\d+)", value.strip())
    if match is None:
        raise ValueError(value)
    return tuple(int(part) for part in match.groups())


try:
    actual = parse_version(sys.argv[1])
    minimum = parse_version(sys.argv[2])
except ValueError:
    raise SystemExit(1)
raise SystemExit(0 if actual >= minimum else 1)
PY
then
  echo "LiteLLM compose tests require Docker Compose >= ${MIN_DOCKER_COMPOSE_VERSION}; found '${compose_version}'." >&2
  exit 1
fi

compose() {
  docker compose -p "${COMPOSE_PROJECT_NAME}" --env-file "${COMPOSE_ENV}" -f "${COMPOSE_FILE}" "$@"
}

compose_stub() {
  docker compose -p "${COMPOSE_PROJECT_NAME}" \
    --env-file "${COMPOSE_ENV}" \
    -f "${COMPOSE_FILE}" \
    -f "${STUB_COMPOSE_FILE}" \
    "$@"
}

prepare_runtime() {
  export LITELLM_PORT="${LITELLM_TEST_PORT:-$(python3 - <<'PY'
import secrets
import socket


for _ in range(100):
    candidate = 20_000 + secrets.randbelow(10_000)
    with socket.socket() as sock:
        try:
            sock.bind(("127.0.0.1", candidate))
        except OSError:
            continue
    print(candidate)
    break
else:
    raise SystemExit("could not find an unused LiteLLM test port")
PY
)}"
  export LITELLM_MASTER_KEY="sk-$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
  export LITELLM_WORKER_API_KEY="sk-$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
  export LITELLM_DB_PASSWORD="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
  bash "${ROOT_DIR}/infra/litellm/network.sh" ensure
}

assert_test_scope() {
  case "${COMPOSE_PROJECT_NAME}" in
    agent-platform-litellm-test-*) ;;
    *)
      echo "Refusing cleanup for unsafe project" >&2
      return 1
      ;;
  esac
  if [[ "${LITELLM_NETWORK_NAME}" != "${COMPOSE_PROJECT_NAME}-llm" ]]; then
    echo "Refusing cleanup for mismatched test network" >&2
    return 1
  fi
  if [[ "${LITELLM_NETWORK_NAME}" == "agent-platform-llm" ]]; then
    echo "Refusing cleanup of production LiteLLM network" >&2
    return 1
  fi
}

cleanup_test_network() {
  assert_test_scope || return 1
  if docker network inspect "${LITELLM_NETWORK_NAME}" >/dev/null 2>&1; then
    docker network rm "${LITELLM_NETWORK_NAME}" >/dev/null
  fi
}

cleanup_runtime() {
  assert_test_scope || return 1
  compose_stub --profile worker-e2e down --volumes --remove-orphans >/dev/null 2>&1 || true
  cleanup_test_network
}

cleanup_on_exit() {
  local status=$?
  trap - EXIT INT TERM
  if (( status != 0 )); then
    compose_stub --profile worker-e2e logs --no-color --tail 80 >&2 || true
  fi
  if ! cleanup_runtime; then
    echo "LiteLLM test cleanup failed" >&2
    if (( status == 0 )); then
      status=1
    fi
  fi
  exit "${status}"
}

install_cleanup_trap() {
  trap cleanup_on_exit EXIT
  trap 'exit 130' INT TERM
}

run_worker_probe() {
  local mode="$1"
  compose_stub --profile worker-e2e build worker-gateway-probe
  compose_stub --profile worker-e2e run --rm --no-deps worker-gateway-probe "${mode}"
}

run_host_probe() {
  local mode="$1"
  AGENT_PLATFORM_LLM_GATEWAY_URL="http://127.0.0.1:${LITELLM_PORT}/v1" \
    AGENT_PLATFORM_LLM_GATEWAY_API_KEY="${LITELLM_WORKER_API_KEY}" \
    uv run --project "${ROOT_DIR}/backend" --frozen --no-dev \
      python "${ROOT_DIR}/infra/litellm/worker_gateway_probe.py" "${mode}"
}

case "$1" in
  config)
    python3 "${ROOT_DIR}/infra/litellm/test_config.py"
    compose config --quiet
    compose_stub config --quiet
    echo "LiteLLM compose and configuration contracts passed"
    ;;
  image-platform)
    manifest="$(docker manifest inspect "${LITELLM_IMAGE}")"
    python3 - "${manifest}" <<'PY'
import json
import sys


manifest = json.loads(sys.argv[1])
platforms = {
    (item.get("platform", {}).get("os"), item.get("platform", {}).get("architecture"))
    for item in manifest.get("manifests", [])
}
required = {("linux", "amd64"), ("linux", "arm64")}
missing = required - platforms
if missing:
    raise SystemExit(f"LiteLLM image is missing required platforms: {sorted(missing)}")
print("LiteLLM image manifest includes linux/amd64 and linux/arm64")
PY
    ;;
  start-health)
    install_cleanup_trap
    prepare_runtime
    compose up -d --wait --wait-timeout 180 litellm
    python3 - "${LITELLM_PORT}" <<'PY'
import sys
import urllib.request


url = f"http://127.0.0.1:{sys.argv[1]}/health/liveliness"
with urllib.request.urlopen(url, timeout=5) as response:
    if response.status != 200:
        raise SystemExit(f"unexpected LiteLLM health status: {response.status}")
print(f"LiteLLM health passed: {url}")
PY
    ;;
  stub-completion)
    install_cleanup_trap
    prepare_runtime
    compose_stub up -d --wait --wait-timeout 180 litellm openai-stub
    compose_stub run --rm --no-deps worker-key-bootstrap
    python3 - "${LITELLM_PORT}" <<'PY'
import json
import os
import sys
import urllib.request


url = f"http://127.0.0.1:{sys.argv[1]}/v1/chat/completions"
api_key = os.environ["LITELLM_WORKER_API_KEY"]
request = urllib.request.Request(
    url,
    data=json.dumps(
        {
            "model": "general-purpose",
            "messages": [{"role": "user", "content": "ping"}],
        }
    ).encode("utf-8"),
    headers={
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    },
    method="POST",
)
with urllib.request.urlopen(request, timeout=15) as response:
    payload = json.load(response)
    if response.status != 200:
        raise SystemExit(f"unexpected LiteLLM completion status: {response.status}")
content = payload["choices"][0]["message"]["content"]
if content != "local stub completion":
    raise SystemExit(f"unexpected LiteLLM completion content: {content!r}")
print(f"LiteLLM local stub completion passed through alias general-purpose: {url}")
PY
    ;;
  worker-readiness|worker-chat|stub-matrix)
    install_cleanup_trap
    prepare_runtime
    compose_stub up -d --wait --wait-timeout 240 litellm openai-stub
    compose_stub --profile worker-e2e run --rm --no-deps legacy-worker-key-seed
    compose_stub run --rm --no-deps worker-key-bootstrap
    compose_stub run --rm --no-deps worker-key-bootstrap
    case "$1" in
      worker-readiness)
        run_host_probe readiness
        ;;
      worker-chat)
        run_worker_probe chat
        ;;
      stub-matrix)
        run_host_probe matrix
        ;;
    esac
    ;;
  *)
    usage
    exit 2
    ;;
esac
