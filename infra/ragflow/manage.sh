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
  # 默认端口与 README 基线一致；宿主机端口被占用（如常驻开发栈、SSH 隧道）时
  # 可通过 RAGFLOW_*_PORT 环境变量覆盖，不修改官方 Compose。
  # tei-cpu 提供默认本地 embedding（TEI 镜像预置模型）；官方默认的
  # Qwen3-Embedding-0.6B 与 bge-m3 在本机 CPU warmup 均可能超出可用内存被 OOM，
  # 默认用最小的 bge-small-en-v1.5，可用 RAGFLOW_TEI_MODEL 覆盖。
  ES_PORT="${RAGFLOW_ES_PORT:-19200}" \
  EXPOSE_MYSQL_PORT="${RAGFLOW_MYSQL_PORT:-13306}" \
  MINIO_PORT="${RAGFLOW_MINIO_PORT:-19000}" \
  MINIO_CONSOLE_PORT="${RAGFLOW_MINIO_CONSOLE_PORT:-19001}" \
  REDIS_PORT="${RAGFLOW_REDIS_PORT:-16379}" \
  SVR_WEB_HTTP_PORT="${RAGFLOW_WEB_HTTP_PORT:-18080}" \
  SVR_WEB_HTTPS_PORT="${RAGFLOW_WEB_HTTPS_PORT:-18443}" \
  SVR_HTTP_PORT="${RAGFLOW_API_PORT:-19380}" \
  ADMIN_SVR_HTTP_PORT="${RAGFLOW_ADMIN_PORT:-19381}" \
  SVR_MCP_PORT="${RAGFLOW_MCP_PORT:-19382}" \
  GO_ADMIN_PORT="${RAGFLOW_GO_ADMIN_PORT:-19383}" \
  GO_HTTP_PORT="${RAGFLOW_GO_HTTP_PORT:-19384}" \
  COMPOSE_PROFILES="${RAGFLOW_COMPOSE_PROFILES:-elasticsearch,cpu,tei-cpu}" \
  TEI_MODEL="${RAGFLOW_TEI_MODEL:-BAAI/bge-small-en-v1.5}" \
    docker compose \
    --project-name agent-platform-ragflow \
    --project-directory "${RUNTIME_ROOT}/docker" \
    -f "${RUNTIME_ROOT}/docker/docker-compose.yml" \
    -f "${SCRIPT_DIR}/compose.override.yml" "$@"
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
