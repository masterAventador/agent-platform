#!/usr/bin/env bash

set -euo pipefail

API_PORT="${PLATFORM_API_PORT:-8000}"
WEB_PORT="${PLATFORM_WEB_PORT:-8080}"

check() {
  local name="$1"
  local url="$2"
  curl --fail --silent --show-error --max-time 5 --output /dev/null "${url}"
  printf '%s healthy: %s\n' "${name}" "${url}"
}

check "API" "http://127.0.0.1:${API_PORT}/api/v1/health/live"
check "frontend" "http://127.0.0.1:${WEB_PORT}/healthz"
check "frontend API proxy" "http://127.0.0.1:${WEB_PORT}/api/v1/health/live"
