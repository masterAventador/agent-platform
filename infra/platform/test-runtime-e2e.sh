#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COMPOSE_FILE="${ROOT_DIR}/infra/compose/core.yml"
COMPOSE_ENV="${ROOT_DIR}/infra/compose/.env.example"
DATABASE_NAME="agent_platform_runtime_e2e"
COMPOSE_PROJECT_NAME="${PLAYWRIGHT_COMPOSE_PROJECT_NAME:-agent-platform-runtime-e2e}"

pick_port() {
  python3 - <<'PY'
import socket

with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1])
PY
}

export COMPOSE_PROJECT_NAME
export POSTGRES_PORT="${PLAYWRIGHT_POSTGRES_PORT:-${POSTGRES_PORT:-$(pick_port)}}"
export REDIS_PORT="${PLAYWRIGHT_REDIS_PORT:-${REDIS_PORT:-$(pick_port)}}"
export MINIO_API_PORT="${PLAYWRIGHT_MINIO_API_PORT:-${MINIO_API_PORT:-$(pick_port)}}"
export MINIO_CONSOLE_PORT="${PLAYWRIGHT_MINIO_CONSOLE_PORT:-${MINIO_CONSOLE_PORT:-$(pick_port)}}"
export PLAYWRIGHT_RUNTIME_API_PORT="${PLAYWRIGHT_RUNTIME_API_PORT:-$(pick_port)}"
export PLAYWRIGHT_RUNTIME_SANDBOX_PORT="${PLAYWRIGHT_RUNTIME_SANDBOX_PORT:-$(pick_port)}"
export PLAYWRIGHT_RUNTIME_RAGFLOW_PORT="${PLAYWRIGHT_RUNTIME_RAGFLOW_PORT:-$(pick_port)}"
export PLAYWRIGHT_RUNTIME_FRONTEND_PORT="${PLAYWRIGHT_RUNTIME_FRONTEND_PORT:-$(pick_port)}"
export PLAYWRIGHT_RUNTIME_MCP_STUB_PORT="${PLAYWRIGHT_RUNTIME_MCP_STUB_PORT:-$(pick_port)}"
export PLAYWRIGHT_RUNTIME_BASE_URL="${PLAYWRIGHT_RUNTIME_BASE_URL:-http://127.0.0.1:${PLAYWRIGHT_RUNTIME_FRONTEND_PORT}}"
export PLAYWRIGHT_POSTGRES_PORT="${POSTGRES_PORT}"
export PLAYWRIGHT_REDIS_PORT="${REDIS_PORT}"
export PLAYWRIGHT_MINIO_API_PORT="${MINIO_API_PORT}"
export PLAYWRIGHT_MINIO_CONSOLE_PORT="${MINIO_CONSOLE_PORT}"

COMPOSE=(docker compose --project-name "${COMPOSE_PROJECT_NAME}" --env-file "${COMPOSE_ENV}" -f "${COMPOSE_FILE}")

# Playwright 以 reuseExistingServer=false 拉起 7 个本地 webServer（uv run uvicorn /
# pnpm dev），它们是「uv/sh -c → python/node」多层子进程。Playwright 正常退出会收掉，
# 但异常终止（驱动进程被 SIGKILL、trap 未及、SIGTERM 未传到孙子进程）时这些本地
# server 会残留成孤儿。以下按本轮随机分配的端口兜底清理——只杀监听本轮端口的进程，
# 不触碰 dev 常驻栈或其他并行栈（它们用不同端口）。
runtime_webserver_ports() {
  printf '%s\n' \
    "${PLAYWRIGHT_RUNTIME_API_PORT}" \
    "${PLAYWRIGHT_RUNTIME_SANDBOX_PORT}" \
    "${PLAYWRIGHT_RUNTIME_RAGFLOW_PORT}" \
    "${PLAYWRIGHT_RUNTIME_FRONTEND_PORT}" \
    "${PLAYWRIGHT_RUNTIME_MCP_STUB_PORT}"
}

kill_local_webservers() {
  local port pid
  while IFS= read -r port; do
    [[ -n "${port}" ]] || continue
    while IFS= read -r pid; do
      [[ -n "${pid}" ]] && kill -9 "${pid}" >/dev/null 2>&1 || true
    done < <(lsof -ti "tcp:${port}" -sTCP:LISTEN 2>/dev/null || true)
  done < <(runtime_webserver_ports)
}

cleanup() {
  local lease_id container_id
  kill_local_webservers
  while IFS= read -r lease_id; do
    [[ -n "${lease_id}" ]] || continue
    while IFS= read -r container_id; do
      [[ -n "${container_id}" ]] && docker rm -f "${container_id}" >/dev/null 2>&1 || true
    done < <(docker ps -aq \
      --filter "label=agent-platform.sandbox.managed=true" \
      --filter "label=agent-platform.sandbox.lease-id=${lease_id}")
  done < <("${COMPOSE[@]}" exec -T postgres \
    psql -U agent_platform -d "${DATABASE_NAME}" -Atc 'select id from sandbox_leases' \
    2>/dev/null || true)
  "${COMPOSE[@]}" exec -T \
    -e REDISCLI_AUTH=agent-platform-local-redis redis redis-cli -n 3 FLUSHDB \
    >/dev/null 2>&1 || true
  "${COMPOSE[@]}" exec -T postgres \
    dropdb --force --if-exists -U agent_platform "${DATABASE_NAME}" \
    >/dev/null 2>&1 || true
  "${COMPOSE[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
  # docker down 之后再兜底一次，捕获 compose 拆栈期间才退出的本地 server 残留。
  kill_local_webservers
  rm -f /tmp/agent-platform-runtime-e2e-dispatcher-ready \
    /tmp/agent-platform-runtime-e2e-worker-ready \
    /tmp/agent-platform-runtime-e2e-slow-model-started \
    /tmp/agent-platform-runtime-e2e-slow-model-stopped \
    /tmp/agent-platform-runtime-e2e-slow-model-side-effect
}
trap cleanup EXIT INT TERM

# 启动前预清：若调用方复用固定 PLAYWRIGHT_RUNTIME_* 端口，先清掉上一轮可能残留的
# 本地 webServer，避免端口占用或误连旧 server。随机端口场景下为空操作。
kill_local_webservers

rm -f /tmp/agent-platform-runtime-e2e-dispatcher-ready \
  /tmp/agent-platform-runtime-e2e-worker-ready \
  /tmp/agent-platform-runtime-e2e-slow-model-started \
  /tmp/agent-platform-runtime-e2e-slow-model-stopped \
  /tmp/agent-platform-runtime-e2e-slow-model-side-effect

export DOCKER_HOST="${DOCKER_HOST:-$(docker context inspect --format '{{.Endpoints.docker.Host}}')}"
if [[ "${DOCKER_HOST}" != unix://* ]]; then
  echo "Runtime E2E requires a unix Docker context" >&2
  exit 2
fi

"${COMPOSE[@]}" up -d --wait \
  postgres redis minio

"${COMPOSE[@]}" exec -T postgres \
  dropdb --force --if-exists -U agent_platform "${DATABASE_NAME}"
"${COMPOSE[@]}" exec -T postgres \
  createdb -U agent_platform "${DATABASE_NAME}"
"${COMPOSE[@]}" exec -T \
  -e REDISCLI_AUTH=agent-platform-local-redis redis redis-cli -n 3 FLUSHDB \
  >/dev/null
(
  cd "${ROOT_DIR}/backend"
  AGENT_PLATFORM_DATABASE_URL="postgresql+asyncpg://agent_platform:agent-platform-local-postgres@127.0.0.1:${POSTGRES_PORT}/${DATABASE_NAME}" \
    uv run alembic upgrade head
)
HEAD_REVISION="$(cd "${ROOT_DIR}/backend" && uv run alembic heads | sed -E 's/ .*//')"
DATABASE_REVISION="$("${COMPOSE[@]}" exec -T \
  postgres psql -U agent_platform -d "${DATABASE_NAME}" -Atc 'select version_num from alembic_version')"
if [[ "${DATABASE_REVISION}" != "${HEAD_REVISION}" ]]; then
  echo "Runtime E2E database is not at Alembic head" >&2
  exit 2
fi

cd "${ROOT_DIR}/frontend"
PLAYWRIGHT_RUNTIME_EXPECTED_OUTPUT="Runtime E2E completed in the real worker." \
PLAYWRIGHT_RUNTIME_EXPECTED_SCHEMA_VERSION="${HEAD_REVISION}" \
pnpm exec playwright test --config playwright.runtime.config.ts ${PLAYWRIGHT_RUNTIME_TEST_ARGS:-}
