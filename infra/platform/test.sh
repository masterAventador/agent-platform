#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COMPOSE_FILE="${ROOT_DIR}/infra/compose/platform.yml"
EXAMPLE_ENV_FILE="${ROOT_DIR}/infra/compose/.env.platform.example"
ENV_FILE="${PLATFORM_ENV_FILE:-${EXAMPLE_ENV_FILE}}"
MODE="${1:-config}"

compose() {
  docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" "$@"
}

contract() {
  python3 "${ROOT_DIR}/infra/platform/test_contract.py"
}

config() {
  contract
  compose config --quiet
}

build() {
  config
  docker build --tag agent-platform-backend:local "${ROOT_DIR}/backend"
  docker build --tag agent-platform-frontend:local "${ROOT_DIR}/frontend"
}

start() {
  if [[ ! -f "${ENV_FILE}" ]]; then
    printf 'platform env file not found: %s\n' "${ENV_FILE}" >&2
    return 2
  fi
  if grep -Eq '^[[:space:]]*[A-Z0-9_]+[[:space:]]*=[[:space:]]*CHANGE_ME' "${ENV_FILE}"; then
    printf 'replace every CHANGE_ME value in platform env file: %s\n' "${ENV_FILE}" >&2
    return 2
  fi
  config
  compose up --detach migrate api dispatcher frontend
}

health() {
  bash "${ROOT_DIR}/infra/platform/health.sh"
  compose ps --status running --services dispatcher | grep -q '^dispatcher$'
  compose exec --no-TTY dispatcher test -f /tmp/agent-platform-dispatcher-ready
  printf 'dispatcher healthy: ready file present\n'
}

case "${MODE}" in
  contract) contract ;;
  config) config ;;
  build) build ;;
  start) start ;;
  health) health ;;
  *)
    printf 'usage: %s {contract|config|build|start|health}\n' "$0" >&2
    exit 2
    ;;
esac
