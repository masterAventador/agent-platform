#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COMPOSE_FILE="${ROOT_DIR}/infra/compose/core.yml"
COMPOSE_ENV="${ROOT_DIR}/infra/compose/.env.example"
MIN_DOCKER_COMPOSE_VERSION="2.20.0"

usage() {
  echo "Usage: bash infra/compose/test.sh config" >&2
}

if [[ $# -ne 1 || "$1" != "config" ]]; then
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
  echo "core compose test entry requires Docker Compose >= ${MIN_DOCKER_COMPOSE_VERSION}; found '${compose_version}'." >&2
  exit 1
fi

CORE_COMPOSE_TEST_ENTRY=1 python3 "${ROOT_DIR}/infra/compose/test_core_config.py"
docker compose --env-file "${COMPOSE_ENV}" -f "${COMPOSE_FILE}" config --quiet
echo "core compose contract passed"
