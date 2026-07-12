#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COMPOSE_FILE="${ROOT_DIR}/infra/compose/core.yml"
COMPOSE_ENV="${ROOT_DIR}/infra/compose/.env.example"
DATABASE_NAME="agent_platform_runtime_recovery_e2e"

cleanup() {
  local lease_id container_id
  while IFS= read -r lease_id; do
    [[ -n "${lease_id}" ]] || continue
    while IFS= read -r container_id; do
      [[ -n "${container_id}" ]] && docker rm -f "${container_id}" >/dev/null 2>&1 || true
    done < <(docker ps -aq --filter "label=agent-platform.sandbox.managed=true" \
      --filter "label=agent-platform.sandbox.lease-id=${lease_id}")
  done < <(docker compose --env-file "${COMPOSE_ENV}" -f "${COMPOSE_FILE}" exec -T postgres \
    psql -U agent_platform -d "${DATABASE_NAME}" -Atc 'select id from sandbox_leases' \
    2>/dev/null || true)
  docker compose --env-file "${COMPOSE_ENV}" -f "${COMPOSE_FILE}" exec -T \
    -e REDISCLI_AUTH=agent-platform-local-redis redis redis-cli -n 4 FLUSHDB \
    >/dev/null 2>&1 || true
  docker compose --env-file "${COMPOSE_ENV}" -f "${COMPOSE_FILE}" exec -T postgres \
    dropdb --force --if-exists -U agent_platform "${DATABASE_NAME}" \
    >/dev/null 2>&1 || true
  rm -f /tmp/agent-platform-runtime-recovery-e2e-*
}
trap cleanup EXIT INT TERM

rm -f /tmp/agent-platform-runtime-recovery-e2e-*
export DOCKER_HOST="${DOCKER_HOST:-$(docker context inspect --format '{{.Endpoints.docker.Host}}')}"
if [[ "${DOCKER_HOST}" != unix://* ]]; then
  echo "Runtime recovery E2E requires a unix Docker context" >&2
  exit 2
fi

docker compose --env-file "${COMPOSE_ENV}" -f "${COMPOSE_FILE}" up -d --wait \
  postgres redis minio
docker compose --env-file "${COMPOSE_ENV}" -f "${COMPOSE_FILE}" exec -T postgres \
  dropdb --force --if-exists -U agent_platform "${DATABASE_NAME}"
docker compose --env-file "${COMPOSE_ENV}" -f "${COMPOSE_FILE}" exec -T postgres \
  createdb -U agent_platform "${DATABASE_NAME}"
docker compose --env-file "${COMPOSE_ENV}" -f "${COMPOSE_FILE}" exec -T \
  -e REDISCLI_AUTH=agent-platform-local-redis redis redis-cli -n 4 FLUSHDB >/dev/null
(
  cd "${ROOT_DIR}/backend"
  AGENT_PLATFORM_DATABASE_URL="postgresql+asyncpg://agent_platform:agent-platform-local-postgres@127.0.0.1:5432/${DATABASE_NAME}" \
    uv run alembic upgrade head
)
HEAD_REVISION="$(cd "${ROOT_DIR}/backend" && uv run alembic heads | sed -E 's/ .*//')"
DATABASE_REVISION="$(docker compose --env-file "${COMPOSE_ENV}" -f "${COMPOSE_FILE}" exec -T \
  postgres psql -U agent_platform -d "${DATABASE_NAME}" -Atc 'select version_num from alembic_version')"
if [[ "${DATABASE_REVISION}" != "${HEAD_REVISION}" ]]; then
  echo "Runtime recovery database is not at Alembic head" >&2
  exit 2
fi

cd "${ROOT_DIR}/frontend"
PLAYWRIGHT_RUNTIME_RECOVERY_BASE_URL="http://127.0.0.1:15175" \
PLAYWRIGHT_RUNTIME_EXPECTED_SCHEMA_VERSION="${HEAD_REVISION}" \
pnpm exec playwright test --config playwright.runtime-recovery.config.ts
