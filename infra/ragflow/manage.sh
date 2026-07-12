#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPOSITORY_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
VERSION="$(tr -d '[:space:]' < "${SCRIPT_DIR}/VERSION")"
RUNTIME_ROOT="${REPOSITORY_ROOT}/.local/ragflow/${VERSION}"
UPSTREAM_URL="https://github.com/infiniflow/ragflow.git"
OFFICIAL_IMAGE="infiniflow/ragflow:${VERSION}"

prepare() {
  if [[ ! -d "${RUNTIME_ROOT}/.git" ]]; then
    mkdir -p "$(dirname "${RUNTIME_ROOT}")"
    git clone --depth 1 --branch "${VERSION}" "${UPSTREAM_URL}" "${RUNTIME_ROOT}"
  fi
  local actual
  actual="$(git -C "${RUNTIME_ROOT}" describe --tags --exact-match)"
  [[ "${actual}" == "${VERSION}" ]] || {
    echo "RAGFlow 运行目录版本不匹配：期望 ${VERSION}，实际 ${actual}" >&2
    exit 1
  }
  git -C "${RUNTIME_ROOT}" diff --quiet || {
    echo "RAGFlow 官方运行目录存在源码改动，拒绝启动" >&2
    exit 1
  }
}

compose() {
  prepare
  ES_PORT=19200 \
  EXPOSE_MYSQL_PORT=13306 \
  MINIO_PORT=19000 \
  MINIO_CONSOLE_PORT=19001 \
  REDIS_PORT=16379 \
  SVR_WEB_HTTP_PORT=18080 \
  SVR_WEB_HTTPS_PORT=18443 \
  SVR_HTTP_PORT=19380 \
  ADMIN_SVR_HTTP_PORT=19381 \
  SVR_MCP_PORT=19382 \
  GO_ADMIN_PORT=19383 \
  GO_HTTP_PORT=19384 \
    docker compose \
    --project-name agent-platform-ragflow \
    --project-directory "${RUNTIME_ROOT}/docker" \
    -f "${RUNTIME_ROOT}/docker/docker-compose.yml" \
    -f "${SCRIPT_DIR}/compose.override.yml" \
    --profile cpu \
    --profile elasticsearch "$@"
}

pull_image() {
  prepare
  local source="${RAGFLOW_IMAGE_SOURCE:-${OFFICIAL_IMAGE}}"
  docker pull --platform linux/amd64 "${source}"
  if [[ "${source}" != "${OFFICIAL_IMAGE}" ]]; then
    docker tag "${source}" "${OFFICIAL_IMAGE}"
  fi
}

case "${1:-}" in
  prepare) prepare ;;
  pull-image) pull_image ;;
  up) compose up -d --wait ;;
  down) compose down ;;
  status) compose ps ;;
  config) compose config ;;
  logs) compose logs -f ragflow-cpu ;;
  *)
    echo "用法: $0 {prepare|pull-image|up|down|status|config|logs}" >&2
    exit 2
    ;;
esac
