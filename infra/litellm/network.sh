#!/usr/bin/env bash
set -euo pipefail

NETWORK_NAME="${LITELLM_NETWORK_NAME:-agent-platform-llm}"

if [[ ! "${NETWORK_NAME}" =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]*$ ]]; then
  echo "Invalid LiteLLM network name" >&2
  exit 2
fi

if [[ ${1:-} != "ensure" || $# -ne 1 ]]; then
  echo "Usage: bash infra/litellm/network.sh ensure" >&2
  exit 2
fi

if docker network inspect "${NETWORK_NAME}" >/dev/null 2>&1; then
  exit 0
fi

if ! docker network create --driver bridge "${NETWORK_NAME}" >/dev/null; then
  docker network inspect "${NETWORK_NAME}" >/dev/null
fi

echo "Docker network ready: ${NETWORK_NAME}"
