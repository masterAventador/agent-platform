#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export RUN_LOCAL_DOCKER_SANDBOX_TESTS=1
export SANDBOX_CONTROLLER_IMAGE="${SANDBOX_CONTROLLER_IMAGE:-python:3.12.13-slim-bookworm@sha256:8a7e7cc04fd3e2bd787f7f24e22d5d119aa590d429b50c95dfe12b3abe52f48b}"
export DOCKER_HOST="${DOCKER_HOST:-$(docker context inspect --format '{{.Endpoints.docker.Host}}')}"
if [[ "${DOCKER_HOST}" != unix://* ]]; then
  echo "Local sandbox integration requires a unix Docker context" >&2
  exit 2
fi
export DOCKER_SOCKET_PATH="${DOCKER_SOCKET_PATH:-${DOCKER_HOST#unix://}}"

uv --directory "${ROOT_DIR}/backend" run pytest \
  tests/integration/sandbox/test_local_docker_controller.py -q
