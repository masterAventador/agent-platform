#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PROFILE_NAME="agent-platform-mvp-test-$$"
TEST_RUNTIME_ROOT="${ROOT_DIR}/.local/mvp-profile-tests"
RUNTIME_DIR="${TEST_RUNTIME_ROOT}/${PROFILE_NAME}"
MVP_SCRIPT="${ROOT_DIR}/infra/platform/mvp-profile.sh"
PORT_HOLDER_NAME="${PROFILE_NAME}-port-holder"

allocate_ports() {
  python3 - <<'PY'
import socket
import random

listeners = []
try:
    candidates = list(range(20000, 40000))
    random.SystemRandom().shuffle(candidates)
    for candidate in candidates:
        listener = socket.socket()
        try:
            listener.bind(("127.0.0.1", candidate))
        except OSError:
            listener.close()
            continue
        listeners.append(listener)
        if len(listeners) == 7:
            break
    if len(listeners) != 7:
        raise SystemExit("could not reserve seven unique MVP test ports")
    print(" ".join(str(listener.getsockname()[1]) for listener in listeners))
finally:
    for listener in listeners:
        listener.close()
PY
}

assert_ports_are_unique() {
  local unique_count
  unique_count="$(printf '%s\n' \
    "${POSTGRES_PORT}" "${REDIS_PORT}" "${MINIO_API_PORT}" "${MINIO_CONSOLE_PORT}" \
    "${LITELLM_PORT}" "${PLATFORM_API_PORT}" "${PLATFORM_WEB_PORT}" | sort -u | wc -l | tr -d ' ')"
  if [[ "${unique_count}" != "7" ]]; then
    printf 'MVP acceptance ports must be unique\n' >&2
    return 1
  fi
}

read_env_value() {
  local path="$1"
  local expected_key="$2"
  python3 - "${path}" "${expected_key}" <<'PY'
import re
import sys

path, expected_key = sys.argv[1:]
values = {}
with open(path, encoding="utf-8") as env_file:
    for raw_line in env_file:
        line = raw_line.rstrip("\n")
        match = re.fullmatch(r"([A-Z][A-Z0-9_]*)=([A-Za-z0-9_./:@+-]+)", line)
        if match is None or match.group(1) in values:
            raise SystemExit(f"invalid generated dotenv file: {path}")
        values[match.group(1)] = match.group(2)
if expected_key not in values:
    raise SystemExit(f"missing {expected_key} in {path}")
print(values[expected_key])
PY
}

profile_volumes() {
  local project
  for project in "${PROFILE_NAME}-core" "${PROFILE_NAME}-litellm" "${PROFILE_NAME}-app"; do
    docker volume ls --quiet --filter "label=com.docker.compose.project=${project}"
  done | sort -u
}

assert_profile_volumes_exist() {
  if ! profile_volumes | grep -q '.'; then
    printf 'MVP test expected retained profile volumes\n' >&2
    return 1
  fi
}

assert_profile_volumes_absent() {
  if profile_volumes | grep -q '.'; then
    printf 'MVP test left profile volumes behind\n' >&2
    return 1
  fi
}

assert_profile_containers_absent() {
  local project
  for project in "${PROFILE_NAME}-core" "${PROFILE_NAME}-litellm" "${PROFILE_NAME}-app"; do
    if docker ps --all --quiet --filter "label=com.docker.compose.project=${project}" | grep -q '.'; then
      printf 'MVP test left containers behind for project: %s\n' "${project}" >&2
      return 1
    fi
  done
}

assert_profile_runtime_is_safe_to_remove() {
  case "${RUNTIME_DIR}" in
    "${ROOT_DIR}"/.local/mvp-profile-tests/agent-platform-mvp-test-*) return 0 ;;
    *)
      printf 'refusing to remove unsafe test runtime directory: %s\n' "${RUNTIME_DIR}" >&2
      return 1
      ;;
  esac
}

print_failure_diagnostics() {
  printf 'MVP acceptance failed; collecting sanitized service diagnostics\n' >&2
  if [[ -f "${RUNTIME_DIR}/platform.env" ]]; then
    docker compose -p "${PROFILE_NAME}-app" \
      --env-file "${RUNTIME_DIR}/platform.env" \
      -f "${ROOT_DIR}/infra/compose/platform.yml" \
      --profile worker ps >&2 || true
    docker compose -p "${PROFILE_NAME}-app" \
      --env-file "${RUNTIME_DIR}/platform.env" \
      -f "${ROOT_DIR}/infra/compose/platform.yml" \
      --profile worker logs --no-color --tail 200 dispatcher worker api >&2 || true
    docker compose -p "${PROFILE_NAME}-app" \
      --env-file "${RUNTIME_DIR}/platform.env" \
      -f "${ROOT_DIR}/infra/compose/platform.yml" \
      --profile worker logs --no-color --tail 200 sandbox-controller >&2 || true
  fi
  if [[ -f "${RUNTIME_DIR}/litellm.env" ]]; then
    docker compose -p "${PROFILE_NAME}-litellm" \
      --env-file "${RUNTIME_DIR}/litellm.env" \
      -f "${ROOT_DIR}/infra/compose/litellm.yml" \
      -f "${ROOT_DIR}/infra/litellm/compose.stub.yml" \
      logs --no-color --tail 100 litellm openai-stub >&2 || true
  fi
  if [[ -f "${RUNTIME_DIR}/core.env" ]]; then
    docker compose -p "${PROFILE_NAME}-core" \
      --env-file "${RUNTIME_DIR}/core.env" \
      -f "${ROOT_DIR}/infra/compose/core.yml" \
      logs --no-color --tail 100 postgres redis >&2 || true
  fi
}

export MVP_PROFILE_NAME="${PROFILE_NAME}"
export MVP_PROFILE_RUNTIME_DIR="${RUNTIME_DIR}"
export MVP_PROFILE_REMOVE_VOLUMES=false
read -r POSTGRES_PORT REDIS_PORT MINIO_API_PORT MINIO_CONSOLE_PORT \
  LITELLM_PORT PLATFORM_API_PORT PLATFORM_WEB_PORT < <(allocate_ports)
export POSTGRES_PORT REDIS_PORT MINIO_API_PORT MINIO_CONSOLE_PORT
export LITELLM_PORT PLATFORM_API_PORT PLATFORM_WEB_PORT
export PLATFORM_FRONTEND_BUILD_MODE=tauri-test
assert_ports_are_unique

cleanup() {
  local original_exit=$?
  local cleanup_exit=0
  trap - EXIT
  if [[ "${original_exit}" -ne 0 ]]; then
    print_failure_diagnostics || true
  fi
  if docker ps --all --quiet --filter "name=^/${PORT_HOLDER_NAME}$" | grep -q '.'; then
    if ! docker rm --force "${PORT_HOLDER_NAME}" >/dev/null; then
      printf 'MVP acceptance cleanup failed to remove port holder: %s\n' "${PORT_HOLDER_NAME}" >&2
      cleanup_exit=1
    fi
  fi
  if [[ -d "${RUNTIME_DIR}" ]]; then
    if ! MVP_PROFILE_REMOVE_VOLUMES=true bash "${MVP_SCRIPT}" stop; then
      printf 'MVP acceptance cleanup failed; preserving runtime env: %s\n' "${RUNTIME_DIR}" >&2
      cleanup_exit=1
    elif ! assert_profile_volumes_absent; then
      printf 'MVP acceptance cleanup left volumes; preserving runtime env: %s\n' "${RUNTIME_DIR}" >&2
      cleanup_exit=1
    elif ! assert_profile_runtime_is_safe_to_remove; then
      cleanup_exit=1
    elif [[ "${cleanup_exit}" == "0" ]]; then
      rm -rf "${RUNTIME_DIR}"
      printf 'MVP acceptance cleanup completed: %s\n' "${PROFILE_NAME}"
    fi
  fi
  if [[ "${original_exit}" -ne 0 ]]; then
    exit "${original_exit}"
  fi
  exit "${cleanup_exit}"
}
trap cleanup EXIT

mkdir -p "${TEST_RUNTIME_ROOT}"
chmod 700 "${TEST_RUNTIME_ROOT}"
UNSAFE_TARGET="${TEST_RUNTIME_ROOT}/${PROFILE_NAME}-unsafe-target"
UNSAFE_LINK="${TEST_RUNTIME_ROOT}/${PROFILE_NAME}-symlink"
mkdir -p "${UNSAFE_TARGET}"
chmod 700 "${UNSAFE_TARGET}"
ln -s "${UNSAFE_TARGET}" "${UNSAFE_LINK}"
if MVP_PROFILE_RUNTIME_DIR="${UNSAFE_LINK}" bash "${MVP_SCRIPT}" status >/dev/null 2>&1; then
  printf 'MVP profile must reject a symlinked runtime directory\n' >&2
  exit 1
fi
rm "${UNSAFE_LINK}"
rmdir "${UNSAFE_TARGET}"

bash "${MVP_SCRIPT}" start
bash "${MVP_SCRIPT}" status
bash "${MVP_SCRIPT}" start
bash "${MVP_SCRIPT}" health

POSTGRES_PASSWORD="$(read_env_value "${RUNTIME_DIR}/core.env" POSTGRES_PASSWORD)"
MINIO_ROOT_USER="$(read_env_value "${RUNTIME_DIR}/core.env" MINIO_ROOT_USER)"
MINIO_ROOT_PASSWORD="$(read_env_value "${RUNTIME_DIR}/core.env" MINIO_ROOT_PASSWORD)"
AGENT_PLATFORM_APP_ENVIRONMENT=development \
  AGENT_PLATFORM_DATABASE_URL="postgresql+asyncpg://agent_platform:${POSTGRES_PASSWORD}@127.0.0.1:${POSTGRES_PORT}/agent_platform" \
  AGENT_PLATFORM_MINIO_ENDPOINT="127.0.0.1:${MINIO_API_PORT}" \
  AGENT_PLATFORM_MINIO_ACCESS_KEY="${MINIO_ROOT_USER}" \
  AGENT_PLATFORM_MINIO_SECRET_KEY="${MINIO_ROOT_PASSWORD}" \
  uv run --project "${ROOT_DIR}/backend" --frozen --no-dev \
    python -m agent_platform.bootstrap.demo_seed
unset POSTGRES_PASSWORD MINIO_ROOT_USER MINIO_ROOT_PASSWORD

LITELLM_WORKER_API_KEY="$(read_env_value "${RUNTIME_DIR}/litellm.env" LITELLM_WORKER_API_KEY)"
AGENT_PLATFORM_LLM_GATEWAY_URL="http://127.0.0.1:${LITELLM_PORT}/v1" \
  AGENT_PLATFORM_LLM_GATEWAY_API_KEY="${LITELLM_WORKER_API_KEY}" \
  uv run --project "${ROOT_DIR}/backend" --frozen --no-dev \
    python "${ROOT_DIR}/infra/litellm/worker_gateway_probe.py" "chat"

MVP_WEB_FLOW_RESULT_FILE="${RUNTIME_DIR}/mvp-web-flow-run-id"
MVP_WEB_FLOW_FAILURE_RESULT_FILE="${RUNTIME_DIR}/mvp-web-flow-failure-run-id"
MVP_WEB_FLOW_ARTIFACT_RESULT_FILE="${RUNTIME_DIR}/mvp-web-flow-artifact-result"
PLAYWRIGHT_BIN="${ROOT_DIR}/frontend/node_modules/.bin/playwright"
if [[ ! -x "${PLAYWRIGHT_BIN}" ]]; then
  printf 'MVP Web flow requires installed frontend dependencies; run pnpm install explicitly\n' >&2
  exit 2
fi
rm -f "${MVP_WEB_FLOW_RESULT_FILE}" "${MVP_WEB_FLOW_FAILURE_RESULT_FILE}" \
  "${MVP_WEB_FLOW_ARTIFACT_RESULT_FILE}"
(
  cd "${ROOT_DIR}/frontend"
  PLAYWRIGHT_MVP_BASE_URL="http://127.0.0.1:${PLATFORM_WEB_PORT}" \
    PLAYWRIGHT_MVP_RESULT_FILE="${MVP_WEB_FLOW_RESULT_FILE}" \
    PLAYWRIGHT_MVP_FAILURE_RESULT_FILE="${MVP_WEB_FLOW_FAILURE_RESULT_FILE}" \
    PLAYWRIGHT_MVP_ARTIFACT_RESULT_FILE="${MVP_WEB_FLOW_ARTIFACT_RESULT_FILE}" \
    "${PLAYWRIGHT_BIN}" test --config playwright.mvp-profile.config.ts
)

if [[ "${MVP_PROFILE_SKIP_TAURI:-false}" != "true" ]]; then
  (
    cd "${ROOT_DIR}/frontend"
    TAURI_MVP_WEB_URL="http://127.0.0.1:${PLATFORM_WEB_PORT}" \
      pnpm test:tauri
  )
fi
if [[ ! -f "${MVP_WEB_FLOW_RESULT_FILE}" ]]; then
  printf 'MVP Web flow did not record its run ID\n' >&2
  exit 1
fi
MVP_WEB_FLOW_RUN_ID="$(sed -n '1p' "${MVP_WEB_FLOW_RESULT_FILE}")"
if [[ ! "${MVP_WEB_FLOW_RUN_ID}" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]]; then
  printf 'MVP Web flow recorded an invalid run ID\n' >&2
  exit 1
fi
MVP_WEB_FLOW_DATABASE_STATE="$(docker compose -p "${PROFILE_NAME}-core" \
  --env-file "${RUNTIME_DIR}/core.env" \
  -f "${ROOT_DIR}/infra/compose/core.yml" \
  exec -T postgres psql -U agent_platform -d agent_platform -At \
  -v ON_ERROR_STOP=1 -c \
  "SELECT r.status || '|' ||
     EXISTS (
       SELECT 1 FROM run_events e
       WHERE e.run_id = r.id
         AND e.event_type = 'message.output'
         AND e.payload ->> 'content' = 'local stub completion'
     )::text || '|' ||
     EXISTS (
       SELECT 1 FROM run_events e
       WHERE e.run_id = r.id AND e.event_type = 'run.completed'
     )::text || '|' ||
     COALESCE((
       SELECT MIN(e.sequence) = 1
         AND MAX(e.sequence) = COUNT(*)
         AND COUNT(DISTINCT e.sequence) = COUNT(*)
       FROM run_events e WHERE e.run_id = r.id
     ), false)::text || '|' ||
     EXISTS (
       SELECT 1 FROM run_commands c
       WHERE c.run_id = r.id AND c.action = 'start'
         AND c.dispatched_at IS NOT NULL AND c.processed_at IS NOT NULL
     )::text
   FROM runs r WHERE r.id = '${MVP_WEB_FLOW_RUN_ID}'::uuid")"
if [[ "${MVP_WEB_FLOW_DATABASE_STATE}" != "completed|true|true|true|true" ]]; then
  printf 'MVP Web flow persistence check failed: %s\n' "${MVP_WEB_FLOW_DATABASE_STATE}" >&2
  exit 1
fi
printf 'MVP Web flow persisted terminal events through the production Worker and LiteLLM Stub\n'

if [[ ! -f "${MVP_WEB_FLOW_FAILURE_RESULT_FILE}" ]]; then
  printf 'MVP Web failure flow did not record its run ID\n' >&2
  exit 1
fi
MVP_WEB_FLOW_FAILURE_RUN_ID="$(sed -n '1p' "${MVP_WEB_FLOW_FAILURE_RESULT_FILE}")"
if [[ ! "${MVP_WEB_FLOW_FAILURE_RUN_ID}" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]]; then
  printf 'MVP Web failure flow recorded an invalid run ID\n' >&2
  exit 1
fi
MVP_WEB_FLOW_FAILURE_DATABASE_STATE="$(docker compose -p "${PROFILE_NAME}-core" \
  --env-file "${RUNTIME_DIR}/core.env" \
  -f "${ROOT_DIR}/infra/compose/core.yml" \
  exec -T postgres psql -U agent_platform -d agent_platform -At \
  -v ON_ERROR_STOP=1 -c \
  "SELECT r.status || '|' ||
     (r.error_code IS NOT NULL)::text || '|' ||
     EXISTS (
       SELECT 1 FROM run_events e
       WHERE e.run_id = r.id AND e.event_type = 'run.failed'
     )::text || '|' ||
     EXISTS (
       SELECT 1 FROM run_commands c
       WHERE c.run_id = r.id AND c.action = 'start'
         AND c.dispatched_at IS NOT NULL AND c.processed_at IS NOT NULL
     )::text
   FROM runs r WHERE r.id = '${MVP_WEB_FLOW_FAILURE_RUN_ID}'::uuid")"
if [[ "${MVP_WEB_FLOW_FAILURE_DATABASE_STATE}" != "failed|true|true|true" ]]; then
  printf 'MVP Web failure flow persistence check failed: %s\n' \
    "${MVP_WEB_FLOW_FAILURE_DATABASE_STATE}" >&2
  exit 1
fi
printf 'MVP Web failure flow persisted a controlled Worker failure and workbench projection\n'

if [[ ! -f "${MVP_WEB_FLOW_ARTIFACT_RESULT_FILE}" ]]; then
  printf 'MVP artifact flow did not record run and artifact IDs\n' >&2
  exit 1
fi
if ! IFS='|' read -r MVP_ARTIFACT_RUN_ID MVP_ARTIFACT_ID \
  <"${MVP_WEB_FLOW_ARTIFACT_RESULT_FILE}"; then
  printf 'MVP artifact flow could not read its result IDs\n' >&2
  exit 1
fi
UUID_PATTERN='^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
if [[ ! "${MVP_ARTIFACT_RUN_ID}" =~ ${UUID_PATTERN} || \
  ! "${MVP_ARTIFACT_ID}" =~ ${UUID_PATTERN} ]]; then
  printf 'MVP artifact flow recorded invalid IDs\n' >&2
  exit 1
fi
if ! MVP_ARTIFACT_DATABASE_STATE="$(docker compose -p "${PROFILE_NAME}-core" \
  --env-file "${RUNTIME_DIR}/core.env" \
  -f "${ROOT_DIR}/infra/compose/core.yml" \
  exec -T postgres psql -U agent_platform -d agent_platform -At \
  -v ON_ERROR_STOP=1 -c \
  "SELECT r.status || '|' ||
     EXISTS (
       SELECT 1 FROM run_events e
       WHERE e.run_id = r.id AND e.event_type = 'artifact.created'
         AND e.payload ->> 'artifact_id' = '${MVP_ARTIFACT_ID}'
     )::text || '|' ||
     (NOT EXISTS (SELECT 1 FROM artifacts a WHERE a.id = '${MVP_ARTIFACT_ID}'::uuid))::text || '|' ||
     EXISTS (
       SELECT 1 FROM artifact_storage_operations o
       WHERE o.entity_id = '${MVP_ARTIFACT_ID}'::uuid
         AND o.action = 'put' AND o.status = 'completed'
     )::text || '|' ||
     EXISTS (
       SELECT 1 FROM artifact_storage_operations o
       WHERE o.entity_id = '${MVP_ARTIFACT_ID}'::uuid
         AND o.action = 'delete' AND o.status = 'completed'
     )::text || '|' ||
     EXISTS (
       SELECT 1 FROM task_attachments ta
       JOIN files f ON f.tenant_id = ta.tenant_id AND f.id = ta.file_id
       WHERE ta.run_id = r.id
         AND f.name = 'brief.txt'
         AND ta.workspace_path LIKE 'inputs/%/brief.txt'
     )::text
   FROM runs r WHERE r.id = '${MVP_ARTIFACT_RUN_ID}'::uuid")"; then
  printf 'MVP artifact persistence/deletion query failed\n' >&2
  exit 1
fi
if [[ "${MVP_ARTIFACT_DATABASE_STATE}" != "completed|true|true|true|true|true" ]]; then
  printf 'MVP artifact persistence/deletion check failed: %s\n' \
    "${MVP_ARTIFACT_DATABASE_STATE}" >&2
  exit 1
fi
MVP_ARTIFACT_TENANT_ID="$(docker compose -p "${PROFILE_NAME}-core" \
  --env-file "${RUNTIME_DIR}/core.env" \
  -f "${ROOT_DIR}/infra/compose/core.yml" \
  exec -T postgres psql -U agent_platform -d agent_platform -At \
  -v ON_ERROR_STOP=1 -c \
  "SELECT tenant_id FROM runs WHERE id = '${MVP_ARTIFACT_RUN_ID}'::uuid")"
if [[ ! "${MVP_ARTIFACT_TENANT_ID}" =~ ${UUID_PATTERN} ]]; then
  printf 'MVP artifact flow could not resolve its tenant\n' >&2
  exit 1
fi
MVP_ATTACHMENT_FILE_ID="$(docker compose -p "${PROFILE_NAME}-core" \
  --env-file "${RUNTIME_DIR}/core.env" \
  -f "${ROOT_DIR}/infra/compose/core.yml" \
  exec -T postgres psql -U agent_platform -d agent_platform -At \
  -v ON_ERROR_STOP=1 -c \
  "SELECT file_id FROM task_attachments
   WHERE run_id = '${MVP_ARTIFACT_RUN_ID}'::uuid
   ORDER BY created_at LIMIT 1")"
if [[ ! "${MVP_ATTACHMENT_FILE_ID}" =~ ${UUID_PATTERN} ]]; then
  printf 'MVP artifact flow could not resolve its attachment file\n' >&2
  exit 1
fi
MINIO_ROOT_USER="$(read_env_value "${RUNTIME_DIR}/core.env" MINIO_ROOT_USER)"
MINIO_ROOT_PASSWORD="$(read_env_value "${RUNTIME_DIR}/core.env" MINIO_ROOT_PASSWORD)"
if ! MVP_MINIO_ENDPOINT="127.0.0.1:${MINIO_API_PORT}" \
  MVP_MINIO_ACCESS_KEY="${MINIO_ROOT_USER}" \
  MVP_MINIO_SECRET_KEY="${MINIO_ROOT_PASSWORD}" \
  MVP_ATTACHMENT_OBJECT_KEY="tenants/${MVP_ARTIFACT_TENANT_ID}/files/${MVP_ATTACHMENT_FILE_ID}" \
  MVP_DELETED_ARTIFACT_OBJECT_KEY="tenants/${MVP_ARTIFACT_TENANT_ID}/runs/${MVP_ARTIFACT_RUN_ID}/artifacts/${MVP_ARTIFACT_ID}" \
  uv run --project "${ROOT_DIR}/backend" --frozen --no-dev python - <<'PY'
import os

from minio import Minio
from minio.error import S3Error

client = Minio(
    os.environ["MVP_MINIO_ENDPOINT"],
    access_key=os.environ["MVP_MINIO_ACCESS_KEY"],
    secret_key=os.environ["MVP_MINIO_SECRET_KEY"],
    secure=False,
)
bucket = "agent-platform-artifacts"
client.stat_object(bucket, os.environ["MVP_ATTACHMENT_OBJECT_KEY"])
try:
    client.stat_object(bucket, os.environ["MVP_DELETED_ARTIFACT_OBJECT_KEY"])
except S3Error as exc:
    if exc.code != "NoSuchKey":
        raise
else:
    raise SystemExit("deleted artifact object still exists")
PY
then
  printf 'MVP artifact MinIO object lifecycle check failed\n' >&2
  exit 1
fi
unset MINIO_ROOT_USER MINIO_ROOT_PASSWORD
printf 'MVP artifact flow persisted event/saga state and removed metadata/object through the real UI\n'

POSTGRES_PASSWORD="$(read_env_value "${RUNTIME_DIR}/core.env" POSTGRES_PASSWORD)"
TEST_DATABASE_URL="postgresql+asyncpg://agent_platform:${POSTGRES_PASSWORD}@127.0.0.1:${POSTGRES_PORT}/agent_platform" \
  uv run --project "${ROOT_DIR}/backend" --frozen \
  pytest -q \
  "${ROOT_DIR}/backend/tests/integration/storage/test_artifact_repository.py::test_real_postgres_artifact_repositories_enforce_composite_tenant_boundaries" \
  "${ROOT_DIR}/backend/tests/integration/storage/test_artifact_repository.py::test_real_postgres_storage_operation_claim_is_exclusive_and_cas_protected"
unset POSTGRES_PASSWORD
printf 'MVP artifact tenant boundary and Saga claim/CAS/renewal passed under real PostgreSQL concurrency\n'

docker compose -p "${PROFILE_NAME}-litellm" \
  --env-file "${RUNTIME_DIR}/litellm.env" \
  -f "${ROOT_DIR}/infra/compose/litellm.yml" \
  -f "${ROOT_DIR}/infra/litellm/compose.stub.yml" \
  ps --status running --services | grep -q '^openai-stub$'

docker compose -p "${PROFILE_NAME}-app" \
  --env-file "${RUNTIME_DIR}/platform.env" \
  -f "${ROOT_DIR}/infra/compose/platform.yml" \
  --profile worker ps --status running --services | grep -q '^sandbox-controller$'
docker compose -p "${PROFILE_NAME}-app" \
  --env-file "${RUNTIME_DIR}/platform.env" \
  -f "${ROOT_DIR}/infra/compose/platform.yml" \
  --profile worker ps --status running --services | grep -q '^sandbox-janitor$'

if docker ps --filter "label=com.docker.compose.project=${PROFILE_NAME}-core" \
  --filter "label=com.docker.compose.service=ragflow" --quiet | grep -q '.'; then
  printf 'RAGFlow must not be part of the MVP profile\n' >&2
  exit 1
fi

docker compose -p "${PROFILE_NAME}-app" \
  --env-file "${RUNTIME_DIR}/platform.env" \
  -f "${ROOT_DIR}/infra/compose/platform.yml" \
  --profile worker stop api
if bash "${MVP_SCRIPT}" health >/dev/null 2>&1; then
  printf 'MVP health must fail when API is stopped\n' >&2
  exit 1
fi
bash "${MVP_SCRIPT}" start

bash "${MVP_SCRIPT}" stop
bash "${MVP_SCRIPT}" status
assert_profile_containers_absent
assert_profile_volumes_exist

CORE_ENV_BACKUP="${RUNTIME_DIR}/core.env.backup"
cp -p "${RUNTIME_DIR}/core.env" "${CORE_ENV_BACKUP}"
MALICIOUS_MARKER="${RUNTIME_DIR}/dotenv-was-executed"
printf 'UNEXPECTED=$(%s)\n' "touch ${MALICIOUS_MARKER}" >>"${RUNTIME_DIR}/core.env"
if bash "${MVP_SCRIPT}" start >/dev/null 2>&1; then
  printf 'MVP profile must reject unexpected dotenv content\n' >&2
  exit 1
fi
if [[ -e "${MALICIOUS_MARKER}" ]]; then
  printf 'MVP profile executed dotenv content\n' >&2
  exit 1
fi
mv "${CORE_ENV_BACKUP}" "${RUNTIME_DIR}/core.env"
chmod 600 "${RUNTIME_DIR}/core.env"
assert_profile_volumes_exist

FAILURE_LOG="${RUNTIME_DIR}/failed-restart.log"
PLATFORM_BACKEND_IMAGE="agent-platform-backend:mvp-$(python3 -c \
  'import hashlib, sys; print(hashlib.sha256(sys.argv[1].encode()).hexdigest()[:12])' "${ROOT_DIR}")"
docker run --detach --rm --name "${PORT_HOLDER_NAME}" \
  --publish "127.0.0.1:${POSTGRES_PORT}:8000" \
  "${PLATFORM_BACKEND_IMAGE}" python -m http.server 8000 >/dev/null
if bash "${MVP_SCRIPT}" start >"${FAILURE_LOG}" 2>&1; then
  printf 'MVP failed restart must return non-zero when a port is occupied\n' >&2
  exit 1
fi
docker rm --force "${PORT_HOLDER_NAME}" >/dev/null
if ! grep -q 'failed-start cleanup completed' "${FAILURE_LOG}"; then
  printf 'MVP failed restart did not report cleanup completion\n' >&2
  exit 1
fi
assert_profile_containers_absent
assert_profile_volumes_exist
if docker network inspect "${PROFILE_NAME}-llm" >/dev/null 2>&1; then
  printf 'MVP failed restart left its LiteLLM network behind\n' >&2
  exit 1
fi

bash "${MVP_SCRIPT}" start

# concurrent start: the first process holds the same-profile lock while rebuilding.
FIRST_START_LOG="${RUNTIME_DIR}/concurrent-first.log"
SECOND_START_LOG="${RUNTIME_DIR}/concurrent-second.log"
bash "${MVP_SCRIPT}" start >"${FIRST_START_LOG}" 2>&1 &
FIRST_START_PID=$!
for _ in $(seq 1 100); do
  if [[ -d "${ROOT_DIR}/.local/mvp-profile-locks/${PROFILE_NAME}.lock" ]]; then
    break
  fi
  sleep 0.05
done
if bash "${MVP_SCRIPT}" start >"${SECOND_START_LOG}" 2>&1; then
  printf 'concurrent start must be rejected for the same MVP profile\n' >&2
  wait "${FIRST_START_PID}"
  exit 1
fi
if ! grep -q 'operation already in progress' "${SECOND_START_LOG}"; then
  printf 'concurrent start rejection did not report the profile lock\n' >&2
  wait "${FIRST_START_PID}"
  exit 1
fi
wait "${FIRST_START_PID}"
bash "${MVP_SCRIPT}" health

API_CONTAINER_ID="$(docker compose -p "${PROFILE_NAME}-app" \
  --env-file "${RUNTIME_DIR}/platform.env" -f "${ROOT_DIR}/infra/compose/platform.yml" \
  --profile worker ps --quiet api)"
FRONTEND_CONTAINER_ID="$(docker compose -p "${PROFILE_NAME}-app" \
  --env-file "${RUNTIME_DIR}/platform.env" -f "${ROOT_DIR}/infra/compose/platform.yml" \
  --profile worker ps --quiet frontend)"
API_IMAGE="$(docker inspect --format '{{.Config.Image}}' "${API_CONTAINER_ID}")"
FRONTEND_IMAGE="$(docker inspect --format '{{.Config.Image}}' "${FRONTEND_CONTAINER_ID}")"
if [[ ! "${API_IMAGE}" =~ ^agent-platform-backend:mvp-[a-f0-9]{12}$ || \
  ! "${FRONTEND_IMAGE}" =~ ^agent-platform-frontend:mvp-[a-f0-9]{12}$ ]]; then
  printf 'MVP profile did not use worktree-specific images: %s %s\n' "${API_IMAGE}" "${FRONTEND_IMAGE}" >&2
  exit 1
fi

bash "${MVP_SCRIPT}" stop
assert_profile_containers_absent
assert_profile_volumes_exist
if docker network inspect "${PROFILE_NAME}-llm" >/dev/null 2>&1; then
  printf 'MVP test left its LiteLLM network behind\n' >&2
  exit 1
fi
printf 'MVP profile status, retained volumes, failed restart cleanup, concurrent start and image isolation passed\n'
