#!/usr/bin/env bash

set -euo pipefail
set -E

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LOCAL_ROOT="${ROOT_DIR}/.local"
PROFILE_NAME="${MVP_PROFILE_NAME:-agent-platform-mvp}"
RUNTIME_DIR="${MVP_PROFILE_RUNTIME_DIR:-${ROOT_DIR}/.local/mvp-profile/${PROFILE_NAME}}"
LOCK_ROOT="${LOCAL_ROOT}/mvp-profile-locks"
LOCK_DIR="${LOCK_ROOT}/${PROFILE_NAME}.lock"
CORE_PROJECT="${PROFILE_NAME}-core"
LITELLM_PROJECT="${PROFILE_NAME}-litellm"
APP_PROJECT="${PROFILE_NAME}-app"
CORE_NETWORK_NAME="${CORE_PROJECT}_default"
LITELLM_NETWORK_NAME="${PROFILE_NAME}-llm"
CORE_COMPOSE_FILE="${ROOT_DIR}/infra/compose/core.yml"
LITELLM_COMPOSE_FILE="${ROOT_DIR}/infra/compose/litellm.yml"
LITELLM_STUB_COMPOSE_FILE="${ROOT_DIR}/infra/litellm/compose.stub.yml"
PLATFORM_COMPOSE_FILE="${ROOT_DIR}/infra/compose/platform.yml"
CORE_ENV_FILE="${RUNTIME_DIR}/core.env"
LITELLM_ENV_FILE="${RUNTIME_DIR}/litellm.env"
PLATFORM_ENV_FILE="${RUNTIME_DIR}/platform.env"
SECRETS_ENV_FILE="${RUNTIME_DIR}/secrets.env"
WORKTREE_IMAGE_ID="$(python3 -c 'import hashlib, sys; print(hashlib.sha256(sys.argv[1].encode()).hexdigest()[:12])' "${ROOT_DIR}")"
PLATFORM_BACKEND_IMAGE="agent-platform-backend:mvp-${WORKTREE_IMAGE_ID}"
PLATFORM_FRONTEND_IMAGE="agent-platform-frontend:mvp-${WORKTREE_IMAGE_ID}"
PREEXISTING_VOLUME_NAMES=""
LOCK_HELD=false

export CORE_NETWORK_NAME LITELLM_NETWORK_NAME PLATFORM_BACKEND_IMAGE PLATFORM_FRONTEND_IMAGE

validate_profile_name() {
  if [[ ! "${PROFILE_NAME}" =~ ^[a-z0-9][a-z0-9_-]*$ ]]; then
    printf 'invalid MVP profile name: %s\n' "${PROFILE_NAME}" >&2
    return 2
  fi
}

initialize_runtime_paths() {
  CORE_ENV_FILE="${RUNTIME_DIR}/core.env"
  LITELLM_ENV_FILE="${RUNTIME_DIR}/litellm.env"
  PLATFORM_ENV_FILE="${RUNTIME_DIR}/platform.env"
  SECRETS_ENV_FILE="${RUNTIME_DIR}/secrets.env"
}

validate_runtime_directory() {
  python3 - "${LOCAL_ROOT}" "${RUNTIME_DIR}" <<'PY'
import os
import stat
import sys

local_root = os.path.abspath(sys.argv[1])
runtime_dir = os.path.abspath(sys.argv[2])
try:
    if os.path.commonpath((local_root, runtime_dir)) != local_root or runtime_dir == local_root:
        raise ValueError
except ValueError:
    raise SystemExit("MVP runtime directory must be below the repository .local directory")

current = local_root
relative_parts = os.path.relpath(runtime_dir, local_root).split(os.sep)
for part in ("", *relative_parts):
    if part:
        current = os.path.join(current, part)
    if not os.path.lexists(current):
        continue
    details = os.lstat(current)
    if stat.S_ISLNK(details.st_mode):
        raise SystemExit(f"MVP runtime path must not contain symlinks: {current}")

if os.path.exists(runtime_dir):
    details = os.stat(runtime_dir)
    if not stat.S_ISDIR(details.st_mode):
        raise SystemExit("MVP runtime path is not a directory")
    if details.st_uid != os.getuid() or stat.S_IMODE(details.st_mode) & 0o077:
        raise SystemExit("MVP runtime directory must be owned by the current user with mode 0700")
print(runtime_dir)
PY
}

ensure_private_directory() {
  local directory="$1"
  if [[ -L "${directory}" ]]; then
    printf 'refusing symlinked private directory: %s\n' "${directory}" >&2
    return 2
  fi
  if [[ ! -e "${directory}" ]]; then
    mkdir -p "${directory}"
    chmod 700 "${directory}"
  fi
  python3 - "${directory}" <<'PY'
import os
import stat
import sys

path = sys.argv[1]
details = os.stat(path)
if not stat.S_ISDIR(details.st_mode):
    raise SystemExit(f"private path is not a directory: {path}")
if details.st_uid != os.getuid() or stat.S_IMODE(details.st_mode) & 0o077:
    raise SystemExit(f"private directory must be owned by the current user with mode 0700: {path}")
PY
}

validate_private_file() {
  local path="$1"
  python3 - "${path}" <<'PY'
import os
import stat
import sys

path = sys.argv[1]
details = os.lstat(path)
if not stat.S_ISREG(details.st_mode):
    raise SystemExit(f"MVP environment path must be a regular file, not a symlink: {path}")
if details.st_uid != os.getuid() or stat.S_IMODE(details.st_mode) & 0o077:
    raise SystemExit(f"MVP environment file must be owned by the current user with mode 0600: {path}")
PY
}

acquire_profile_lock() {
  local attempt owner_pid
  if [[ ! -e "${LOCAL_ROOT}" ]]; then
    mkdir -p "${LOCAL_ROOT}"
    chmod 700 "${LOCAL_ROOT}"
  fi
  python3 - "${LOCAL_ROOT}" <<'PY'
import os
import stat
import sys

path = sys.argv[1]
details = os.lstat(path)
if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode):
    raise SystemExit(f"unsafe repository .local path: {path}")
if details.st_uid != os.getuid() or stat.S_IMODE(details.st_mode) & 0o022:
    raise SystemExit(f"repository .local path must not be writable by group or others: {path}")
PY
  ensure_private_directory "${LOCK_ROOT}"
  for attempt in 1 2; do
    if mkdir "${LOCK_DIR}" 2>/dev/null; then
      printf '%s\n' "$$" >"${LOCK_DIR}/pid"
      chmod 600 "${LOCK_DIR}/pid"
      LOCK_HELD=true
      trap release_profile_lock EXIT
      return
    fi
    if [[ -L "${LOCK_DIR}" || ! -d "${LOCK_DIR}" ]]; then
      printf 'unsafe MVP profile lock path: %s\n' "${LOCK_DIR}" >&2
      return 2
    fi
    owner_pid=""
    if [[ -f "${LOCK_DIR}/pid" ]]; then
      owner_pid="$(sed -n '1p' "${LOCK_DIR}/pid")"
    fi
    if [[ "${owner_pid}" =~ ^[0-9]+$ ]] && kill -0 "${owner_pid}" 2>/dev/null; then
      printf 'MVP profile operation already in progress: %s (pid %s)\n' "${PROFILE_NAME}" "${owner_pid}" >&2
      return 1
    fi
    rm -f "${LOCK_DIR}/pid"
    if ! rmdir "${LOCK_DIR}"; then
      printf 'failed to remove stale MVP profile lock: %s\n' "${LOCK_DIR}" >&2
      return 1
    fi
  done
  printf 'could not acquire MVP profile lock: %s\n' "${PROFILE_NAME}" >&2
  return 1
}

release_profile_lock() {
  if [[ "${LOCK_HELD}" != "true" ]]; then
    return
  fi
  rm -f "${LOCK_DIR}/pid"
  if ! rmdir "${LOCK_DIR}"; then
    printf 'failed to release MVP profile lock: %s\n' "${LOCK_DIR}" >&2
    return 1
  fi
  LOCK_HELD=false
}

random_secret() {
  python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
}

write_environment() {
  local existing_files=0
  local path
  for path in "${SECRETS_ENV_FILE}" "${CORE_ENV_FILE}" "${LITELLM_ENV_FILE}" "${PLATFORM_ENV_FILE}"; do
    if [[ -e "${path}" || -L "${path}" ]]; then
      existing_files=$((existing_files + 1))
    fi
  done
  if [[ "${existing_files}" -eq 4 ]]; then
    return
  fi
  if [[ "${existing_files}" -ne 0 ]]; then
    printf 'MVP profile has an incomplete environment set: %s\n' "${RUNTIME_DIR}" >&2
    return 2
  fi

  ensure_private_directory "$(dirname "${RUNTIME_DIR}")"
  ensure_private_directory "${RUNTIME_DIR}"
  umask 077

  local postgres_password redis_password minio_password
  local litellm_master_key litellm_worker_key litellm_db_password sandbox_secret
  postgres_password="$(random_secret)"
  redis_password="$(random_secret)"
  minio_password="$(random_secret)"
  litellm_master_key="sk-$(random_secret)"
  litellm_worker_key="sk-$(random_secret)"
  litellm_db_password="$(random_secret)"
  sandbox_secret="$(random_secret)"
  {
    printf 'POSTGRES_PASSWORD=%s\n' "${postgres_password}"
    printf 'REDIS_PASSWORD=%s\n' "${redis_password}"
    printf 'MINIO_ROOT_PASSWORD=%s\n' "${minio_password}"
    printf 'LITELLM_MASTER_KEY=%s\n' "${litellm_master_key}"
    printf 'LITELLM_WORKER_API_KEY=%s\n' "${litellm_worker_key}"
    printf 'LITELLM_DB_PASSWORD=%s\n' "${litellm_db_password}"
    printf 'SANDBOX_CONTROLLER_BEARER_SECRET=%s\n' "${sandbox_secret}"
  } >"${SECRETS_ENV_FILE}"

  {
    printf 'POSTGRES_DB=agent_platform\n'
    printf 'POSTGRES_USER=agent_platform\n'
    printf 'POSTGRES_PASSWORD=%s\n' "${postgres_password}"
    printf 'POSTGRES_PORT=%s\n' "${POSTGRES_PORT:-5432}"
    printf 'REDIS_PASSWORD=%s\n' "${redis_password}"
    printf 'REDIS_PORT=%s\n' "${REDIS_PORT:-6379}"
    printf 'MINIO_ROOT_USER=agent_platform\n'
    printf 'MINIO_ROOT_PASSWORD=%s\n' "${minio_password}"
    printf 'MINIO_API_PORT=%s\n' "${MINIO_API_PORT:-9000}"
    printf 'MINIO_CONSOLE_PORT=%s\n' "${MINIO_CONSOLE_PORT:-9001}"
  } >"${CORE_ENV_FILE}"

  {
    printf 'LITELLM_MASTER_KEY=%s\n' "${litellm_master_key}"
    printf 'LITELLM_WORKER_API_KEY=%s\n' "${litellm_worker_key}"
    printf 'LITELLM_PORT=%s\n' "${LITELLM_PORT:-4000}"
    printf 'LITELLM_DB_NAME=litellm\n'
    printf 'LITELLM_DB_USER=litellm\n'
    printf 'LITELLM_DB_PASSWORD=%s\n' "${litellm_db_password}"
    printf 'LITELLM_UPSTREAM_MODEL=openai/local-test\n'
    printf 'LITELLM_NETWORK_NAME=%s\n' "${LITELLM_NETWORK_NAME}"
  } >"${LITELLM_ENV_FILE}"

  {
    printf 'POSTGRES_DB=agent_platform\n'
    printf 'POSTGRES_USER=agent_platform\n'
    printf 'POSTGRES_PASSWORD=%s\n' "${postgres_password}"
    printf 'REDIS_PASSWORD=%s\n' "${redis_password}"
    printf 'MINIO_ROOT_USER=agent_platform\n'
    printf 'MINIO_ROOT_PASSWORD=%s\n' "${minio_password}"
    printf 'PLATFORM_API_PORT=%s\n' "${PLATFORM_API_PORT:-8000}"
    printf 'PLATFORM_WEB_PORT=%s\n' "${PLATFORM_WEB_PORT:-8080}"
    printf 'OTEL_ENABLED=false\n'
    printf 'AGENT_PLATFORM_LLM_GATEWAY_URL=http://litellm:4000/v1\n'
    printf 'AGENT_PLATFORM_LLM_GATEWAY_API_KEY=%s\n' "${litellm_worker_key}"
    printf 'SANDBOX_CONTROLLER_BEARER_SECRET=%s\n' "${sandbox_secret}"
    printf 'SANDBOX_CONTROLLER_IMAGE=python:3.12.13-slim-bookworm@sha256:8a7e7cc04fd3e2bd787f7f24e22d5d119aa590d429b50c95dfe12b3abe52f48b\n'
    printf 'DOCKER_SOCKET_PATH=/var/run/docker.sock\n'
    printf 'CORE_NETWORK_NAME=%s\n' "${CORE_NETWORK_NAME}"
    printf 'LITELLM_NETWORK_NAME=%s\n' "${LITELLM_NETWORK_NAME}"
  } >"${PLATFORM_ENV_FILE}"
  chmod 600 "${SECRETS_ENV_FILE}" "${CORE_ENV_FILE}" "${LITELLM_ENV_FILE}" "${PLATFORM_ENV_FILE}"
}

dotenv_key_is_allowed() {
  local kind="$1"
  local key="$2"
  case "${kind}:${key}" in
    secrets:POSTGRES_PASSWORD|secrets:REDIS_PASSWORD|secrets:MINIO_ROOT_PASSWORD|\
      secrets:LITELLM_MASTER_KEY|secrets:LITELLM_WORKER_API_KEY|\
      secrets:LITELLM_DB_PASSWORD|secrets:SANDBOX_CONTROLLER_BEARER_SECRET|\
      core:POSTGRES_DB|core:POSTGRES_USER|core:POSTGRES_PASSWORD|core:POSTGRES_PORT|\
      core:REDIS_PASSWORD|core:REDIS_PORT|core:MINIO_ROOT_USER|\
      core:MINIO_ROOT_PASSWORD|core:MINIO_API_PORT|core:MINIO_CONSOLE_PORT|\
      litellm:LITELLM_MASTER_KEY|litellm:LITELLM_WORKER_API_KEY|litellm:LITELLM_PORT|\
      litellm:LITELLM_DB_NAME|litellm:LITELLM_DB_USER|litellm:LITELLM_DB_PASSWORD|\
      litellm:LITELLM_UPSTREAM_MODEL|litellm:LITELLM_NETWORK_NAME|\
      platform:POSTGRES_DB|platform:POSTGRES_USER|platform:POSTGRES_PASSWORD|\
      platform:REDIS_PASSWORD|platform:MINIO_ROOT_USER|platform:MINIO_ROOT_PASSWORD|\
      platform:PLATFORM_API_PORT|platform:PLATFORM_WEB_PORT|platform:OTEL_ENABLED|\
      platform:AGENT_PLATFORM_LLM_GATEWAY_URL|platform:AGENT_PLATFORM_LLM_GATEWAY_API_KEY|\
      platform:SANDBOX_CONTROLLER_BEARER_SECRET|platform:SANDBOX_CONTROLLER_IMAGE|\
      platform:DOCKER_SOCKET_PATH|platform:CORE_NETWORK_NAME|platform:LITELLM_NETWORK_NAME)
      return 0
      ;;
    *) return 1 ;;
  esac
}

load_dotenv_file() {
  local kind="$1"
  local path="$2"
  local line key value seen_keys="|"
  validate_private_file "${path}"
  while IFS= read -r line || [[ -n "${line}" ]]; do
    if [[ ! "${line}" =~ ^([A-Z][A-Z0-9_]*)=([A-Za-z0-9_./:@+-]+)$ ]]; then
      printf 'invalid dotenv entry in %s\n' "${path}" >&2
      return 2
    fi
    key="${BASH_REMATCH[1]}"
    value="${BASH_REMATCH[2]}"
    if ! dotenv_key_is_allowed "${kind}" "${key}"; then
      printf 'unexpected dotenv key in %s: %s\n' "${path}" "${key}" >&2
      return 2
    fi
    if [[ "${seen_keys}" == *"|${key}|"* ]]; then
      printf 'duplicate dotenv key in %s: %s\n' "${path}" "${key}" >&2
      return 2
    fi
    seen_keys="${seen_keys}${key}|"
    printf -v "${key}" '%s' "${value}"
    export "${key}"
  done <"${path}"
}

validate_port() {
  local name="$1"
  local value="$2"
  if [[ ! "${value}" =~ ^[0-9]+$ ]] || ((value < 1024 || value > 65535)); then
    printf 'invalid MVP port %s=%s\n' "${name}" "${value}" >&2
    return 2
  fi
}

clear_profile_environment() {
  unset POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD POSTGRES_PORT
  unset REDIS_PASSWORD REDIS_PORT MINIO_ROOT_USER MINIO_ROOT_PASSWORD
  unset MINIO_API_PORT MINIO_CONSOLE_PORT LITELLM_MASTER_KEY LITELLM_WORKER_API_KEY
  unset LITELLM_PORT LITELLM_DB_NAME LITELLM_DB_USER LITELLM_DB_PASSWORD
  unset LITELLM_UPSTREAM_MODEL PLATFORM_API_PORT PLATFORM_WEB_PORT OTEL_ENABLED
  unset AGENT_PLATFORM_LLM_GATEWAY_URL AGENT_PLATFORM_LLM_GATEWAY_API_KEY
  unset SANDBOX_CONTROLLER_BEARER_SECRET SANDBOX_CONTROLLER_IMAGE DOCKER_SOCKET_PATH
}

require_environment_keys() {
  local key
  for key in POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD POSTGRES_PORT \
    REDIS_PASSWORD REDIS_PORT MINIO_ROOT_USER MINIO_ROOT_PASSWORD MINIO_API_PORT \
    MINIO_CONSOLE_PORT LITELLM_MASTER_KEY LITELLM_WORKER_API_KEY LITELLM_PORT \
    LITELLM_DB_NAME LITELLM_DB_USER LITELLM_DB_PASSWORD LITELLM_UPSTREAM_MODEL \
    PLATFORM_API_PORT PLATFORM_WEB_PORT OTEL_ENABLED AGENT_PLATFORM_LLM_GATEWAY_URL \
    AGENT_PLATFORM_LLM_GATEWAY_API_KEY SANDBOX_CONTROLLER_BEARER_SECRET \
    SANDBOX_CONTROLLER_IMAGE DOCKER_SOCKET_PATH; do
    if ! declare -p "${key}" >/dev/null 2>&1; then
      printf 'missing required MVP environment key: %s\n' "${key}" >&2
      return 2
    fi
  done
}

validate_environment() {
  local port_name port_value ports="|"
  for port_name in POSTGRES_PORT REDIS_PORT MINIO_API_PORT MINIO_CONSOLE_PORT LITELLM_PORT PLATFORM_API_PORT PLATFORM_WEB_PORT; do
    port_value="${!port_name}"
    validate_port "${port_name}" "${port_value}"
    if [[ "${ports}" == *"|${port_value}|"* ]]; then
      printf 'MVP ports must be unique; duplicate value: %s\n' "${port_value}" >&2
      return 2
    fi
    ports="${ports}${port_value}|"
  done
  if [[ "${DOCKER_SOCKET_PATH}" != "/var/run/docker.sock" ]]; then
    printf 'invalid Docker socket path for MVP profile\n' >&2
    return 2
  fi
  if [[ "${CORE_NETWORK_NAME}" != "${CORE_PROJECT}_default" || "${LITELLM_NETWORK_NAME}" != "${PROFILE_NAME}-llm" ]]; then
    printf 'MVP environment contains unexpected network names\n' >&2
    return 2
  fi
  if [[ "${AGENT_PLATFORM_LLM_GATEWAY_URL}" != "http://litellm:4000/v1" || "${OTEL_ENABLED}" != "false" ]]; then
    printf 'MVP environment contains unsupported platform configuration\n' >&2
    return 2
  fi
  if [[ "${POSTGRES_DB}" != "agent_platform" || "${POSTGRES_USER}" != "agent_platform" || \
    "${MINIO_ROOT_USER}" != "agent_platform" || "${LITELLM_DB_NAME}" != "litellm" || \
    "${LITELLM_DB_USER}" != "litellm" || "${LITELLM_UPSTREAM_MODEL}" != "openai/local-test" ]]; then
    printf 'MVP environment contains unsupported service identities\n' >&2
    return 2
  fi
}

load_environment() {
  local path
  for path in "${SECRETS_ENV_FILE}" "${CORE_ENV_FILE}" "${LITELLM_ENV_FILE}" "${PLATFORM_ENV_FILE}"; do
    if [[ ! -f "${path}" ]]; then
      printf 'MVP profile is not initialized: %s\n' "${RUNTIME_DIR}" >&2
      return 2
    fi
  done
  clear_profile_environment
  load_dotenv_file secrets "${SECRETS_ENV_FILE}"
  load_dotenv_file core "${CORE_ENV_FILE}"
  load_dotenv_file litellm "${LITELLM_ENV_FILE}"
  load_dotenv_file platform "${PLATFORM_ENV_FILE}"
  require_environment_keys
  validate_environment
  export CORE_NETWORK_NAME LITELLM_NETWORK_NAME PLATFORM_BACKEND_IMAGE PLATFORM_FRONTEND_IMAGE
}

core_compose() {
  docker compose -p "${CORE_PROJECT}" --env-file "${CORE_ENV_FILE}" \
    -f "${CORE_COMPOSE_FILE}" "$@"
}

litellm_compose() {
  docker compose -p "${LITELLM_PROJECT}" --env-file "${LITELLM_ENV_FILE}" \
    -f "${LITELLM_COMPOSE_FILE}" -f "${LITELLM_STUB_COMPOSE_FILE}" "$@"
}

app_compose() {
  docker compose -p "${APP_PROJECT}" --env-file "${PLATFORM_ENV_FILE}" \
    -f "${PLATFORM_COMPOSE_FILE}" --profile worker "$@"
}

ensure_llm_network() {
  local owner
  if docker network inspect "${LITELLM_NETWORK_NAME}" >/dev/null 2>&1; then
    owner="$(docker network inspect --format '{{ index .Labels "agent-platform.mvp-profile" }}' "${LITELLM_NETWORK_NAME}")"
    if [[ "${owner}" != "${PROFILE_NAME}" ]]; then
      printf 'refusing to reuse unmanaged network: %s\n' "${LITELLM_NETWORK_NAME}" >&2
      return 1
    fi
    return
  fi
  docker network create \
    --label "agent-platform.mvp-profile=${PROFILE_NAME}" \
    "${LITELLM_NETWORK_NAME}" >/dev/null
}

list_profile_volumes() {
  local project
  for project in "${CORE_PROJECT}" "${LITELLM_PROJECT}" "${APP_PROJECT}"; do
    docker volume ls --quiet --filter "label=com.docker.compose.project=${project}"
  done | sort -u
}

capture_preexisting_volumes() {
  PREEXISTING_VOLUME_NAMES="$(list_profile_volumes)"
}

volume_was_preexisting() {
  local volume_name="$1"
  printf '%s\n' "${PREEXISTING_VOLUME_NAMES}" | rg --fixed-strings --line-regexp --quiet "${volume_name}"
}

remove_new_volumes() {
  local failed=false
  local volume_name
  while IFS= read -r volume_name; do
    if [[ -z "${volume_name}" ]] || volume_was_preexisting "${volume_name}"; then
      continue
    fi
    if ! docker volume rm "${volume_name}" >/dev/null; then
      printf 'failed to remove newly created MVP volume: %s\n' "${volume_name}" >&2
      failed=true
    fi
  done < <(list_profile_volumes)
  if [[ "${failed}" == "true" ]]; then
    return 1
  fi
}

remove_llm_network() {
  local owner
  if ! docker network inspect "${LITELLM_NETWORK_NAME}" >/dev/null 2>&1; then
    return
  fi
  owner="$(docker network inspect --format '{{ index .Labels "agent-platform.mvp-profile" }}' "${LITELLM_NETWORK_NAME}")"
  if [[ "${owner}" == "${PROFILE_NAME}" ]]; then
    docker network rm "${LITELLM_NETWORK_NAME}" >/dev/null
  fi
}

stop_profile() {
  local down_args=(down --remove-orphans)
  local failed=false
  if [[ "${MVP_PROFILE_REMOVE_VOLUMES:-false}" == "true" ]]; then
    down_args+=(--volumes)
  fi
  if ! app_compose "${down_args[@]}" >/dev/null 2>&1; then
    printf 'failed to stop MVP app stack: %s\n' "${APP_PROJECT}" >&2
    failed=true
  fi
  if ! litellm_compose "${down_args[@]}" >/dev/null 2>&1; then
    printf 'failed to stop MVP LiteLLM stack: %s\n' "${LITELLM_PROJECT}" >&2
    failed=true
  fi
  if ! core_compose "${down_args[@]}" >/dev/null 2>&1; then
    printf 'failed to stop MVP core stack: %s\n' "${CORE_PROJECT}" >&2
    failed=true
  fi
  if ! remove_llm_network; then
    printf 'failed to remove MVP LiteLLM network: %s\n' "${LITELLM_NETWORK_NAME}" >&2
    failed=true
  fi
  if [[ "${failed}" == "true" ]]; then
    return 1
  fi
  printf 'MVP profile stopped: %s\n' "${PROFILE_NAME}"
}

cleanup_failed_start() {
  local exit_code=$?
  local cleanup_failed=false
  trap - ERR INT TERM
  if ! MVP_PROFILE_REMOVE_VOLUMES=false stop_profile; then
    cleanup_failed=true
  elif ! remove_new_volumes; then
    cleanup_failed=true
  fi
  if [[ "${cleanup_failed}" == "true" ]]; then
    printf 'MVP profile failed-start cleanup was incomplete; runtime environment preserved: %s\n' "${RUNTIME_DIR}" >&2
  else
    printf 'MVP profile failed-start cleanup completed: %s\n' "${PROFILE_NAME}" >&2
  fi
  printf 'MVP profile start failed safely: %s\n' "${PROFILE_NAME}" >&2
  exit "${exit_code}"
}

assert_service_healthy() {
  local stack="$1"
  local service="$2"
  local container_id state
  case "${stack}" in
    core) container_id="$(core_compose ps --quiet "${service}")" ;;
    litellm) container_id="$(litellm_compose ps --quiet "${service}")" ;;
    app) container_id="$(app_compose ps --quiet "${service}")" ;;
    *) printf 'unknown MVP stack: %s\n' "${stack}" >&2; return 2 ;;
  esac
  if [[ -z "${container_id}" ]]; then
    printf '%s service is missing: %s\n' "${stack}" "${service}" >&2
    return 1
  fi
  state="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "${container_id}")"
  if [[ "${state}" != "healthy" ]]; then
    printf '%s service is not healthy: %s (%s)\n' "${stack}" "${service}" "${state}" >&2
    return 1
  fi
  printf '%s service healthy: %s\n' "${stack}" "${service}"
}

health_profile() {
  local service
  for service in postgres redis minio; do
    assert_service_healthy core "${service}"
  done
  for service in litellm-db openai-stub litellm; do
    assert_service_healthy litellm "${service}"
  done
  for service in api dispatcher sandbox-controller sandbox-janitor worker frontend; do
    assert_service_healthy app "${service}"
  done
  PLATFORM_API_PORT="${PLATFORM_API_PORT}" PLATFORM_WEB_PORT="${PLATFORM_WEB_PORT}" \
    bash "${ROOT_DIR}/infra/platform/health.sh"
  printf 'MVP profile healthy (local Stub, RAGFlow excluded): %s\n' "${PROFILE_NAME}"
}

start_profile() {
  capture_preexisting_volumes
  trap cleanup_failed_start ERR INT TERM
  core_compose config --quiet
  litellm_compose config --quiet
  app_compose config --quiet
  app_compose build migrate frontend
  ensure_llm_network
  core_compose up --detach --wait --wait-timeout 180 postgres redis minio
  litellm_compose up --detach --wait --wait-timeout 240 litellm openai-stub
  litellm_compose run --rm --no-deps worker-key-bootstrap
  if ! app_compose up --detach --wait --wait-timeout 300 --no-build \
    migrate api dispatcher sandbox-controller sandbox-janitor worker frontend; then
    if ! app_compose logs --no-color --tail 80 worker sandbox-controller; then
      printf 'failed to collect MVP worker diagnostics\n' >&2
    fi
    return 1
  fi
  health_profile
  trap - ERR INT TERM
  printf 'MVP profile started: %s\n' "${PROFILE_NAME}"
}

status_profile() {
  printf 'Core services:\n'
  core_compose ps
  printf 'LiteLLM Stub services:\n'
  litellm_compose ps
  printf 'Platform services:\n'
  app_compose ps
}

validate_profile_name
RUNTIME_DIR="$(validate_runtime_directory)"
initialize_runtime_paths
acquire_profile_lock

case "${1:-status}" in
  start)
    write_environment
    load_environment
    start_profile
    ;;
  stop)
    if [[ -f "${SECRETS_ENV_FILE}" && -f "${CORE_ENV_FILE}" && -f "${LITELLM_ENV_FILE}" && -f "${PLATFORM_ENV_FILE}" ]]; then
      load_environment
      stop_profile
    else
      printf 'MVP profile already stopped: %s\n' "${PROFILE_NAME}"
    fi
    ;;
  health)
    load_environment
    health_profile
    ;;
  status)
    load_environment
    status_profile
    ;;
  *)
    printf 'usage: %s {start|stop|health|status}\n' "$0" >&2
    exit 2
    ;;
esac
