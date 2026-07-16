#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COMPOSE_FILE="${ROOT_DIR}/infra/compose/observability.yml"
COLLECTOR_CONFIG="${ROOT_DIR}/infra/observability/otel-collector.yml"
COLLECTOR_IMAGE="otel/opentelemetry-collector-contrib:0.156.0"
MODE="${1:-config}"

run_contract_test() {
  uv run --project "${ROOT_DIR}/backend" python "${ROOT_DIR}/infra/observability/test_config.py"
}

validate_compose() {
  docker compose -f "${COMPOSE_FILE}" config --quiet
}

validate_collector() {
  docker run --rm \
    --volume "${COLLECTOR_CONFIG}:/etc/otelcol-contrib/config.yaml:ro" \
    "${COLLECTOR_IMAGE}" \
    validate --config=/etc/otelcol-contrib/config.yaml
}

wait_for_url() {
  local name="$1"
  local url="$2"
  local attempts=30

  for ((attempt = 1; attempt <= attempts; attempt++)); do
    if curl --fail --silent --show-error --output /dev/null "${url}"; then
      printf '%s is healthy: %s\n' "${name}" "${url}"
      return 0
    fi
    sleep 1
  done

  printf '%s did not become healthy: %s\n' "${name}" "${url}" >&2
  return 1
}

run_start_health_test() {
  local otel_health_port="${OTEL_HEALTH_PORT:-13133}"
  local jaeger_ui_port="${JAEGER_UI_PORT:-16686}"
  local prometheus_port="${PROMETHEUS_UI_PORT:-9090}"
  local grafana_port="${GRAFANA_UI_PORT:-3000}"

  cleanup() {
    docker compose -f "${COMPOSE_FILE}" down
  }
  trap cleanup EXIT

  docker compose -f "${COMPOSE_FILE}" up --detach
  wait_for_url "OpenTelemetry Collector" "http://127.0.0.1:${otel_health_port}/"
  wait_for_url "Jaeger" "http://127.0.0.1:${jaeger_ui_port}/"
  wait_for_url "Prometheus" "http://127.0.0.1:${prometheus_port}/-/ready"
  wait_for_url "Grafana" "http://127.0.0.1:${grafana_port}/api/health"
  docker compose -f "${COMPOSE_FILE}" ps
}

case "${MODE}" in
  config)
    run_contract_test
    validate_compose
    validate_collector
    ;;
  start-health)
    run_contract_test
    validate_compose
    run_start_health_test
    ;;
  *)
    printf 'usage: %s [config|start-health]\n' "$0" >&2
    exit 2
    ;;
esac
