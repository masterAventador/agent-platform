from __future__ import annotations

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_PATH = ROOT / "infra" / "compose" / "observability.yml"
COLLECTOR_CONFIG_PATH = ROOT / "infra" / "observability" / "otel-collector.yml"
ALERT_RULES_PATH = ROOT / "infra" / "observability" / "alert-rules.yml"
PROMETHEUS_CONFIG_PATH = ROOT / "infra" / "observability" / "prometheus.yml"
LOKI_CONFIG_PATH = ROOT / "infra" / "observability" / "loki.yml"
GRAFANA_DATASOURCES_PATH = (
    ROOT
    / "infra"
    / "observability"
    / "grafana"
    / "provisioning"
    / "datasources"
    / "datasources.yml"
)
GRAFANA_DASHBOARDS_PATH = (
    ROOT
    / "infra"
    / "observability"
    / "grafana"
    / "provisioning"
    / "dashboards"
    / "dashboards.yml"
)
GRAFANA_DASHBOARD_DIR = ROOT / "infra" / "observability" / "grafana" / "dashboards"

COLLECTOR_IMAGE = "otel/opentelemetry-collector-contrib:0.156.0"
JAEGER_IMAGE = "jaegertracing/all-in-one:1.76.0"
PROMETHEUS_IMAGE = "prom/prometheus:v3.5.0"
LOKI_IMAGE = "grafana/loki:3.5.3"
GRAFANA_IMAGE = "grafana/grafana:12.1.1"
LOOPBACK = "127.0.0.1:"


def load_yaml(path: Path) -> dict[str, object]:
    assert path.is_file(), f"missing required configuration: {path.relative_to(ROOT)}"
    with path.open(encoding="utf-8") as stream:
        document = yaml.safe_load(stream)
    assert isinstance(document, dict), f"expected a YAML mapping in {path.relative_to(ROOT)}"
    return document


def load_json(path: Path) -> dict[str, object]:
    assert path.is_file(), f"missing required configuration: {path.relative_to(ROOT)}"
    document = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(document, dict), f"expected a JSON mapping in {path.relative_to(ROOT)}"
    return document


def test_compose_contract() -> None:
    compose = load_yaml(COMPOSE_PATH)
    assert compose.get("name") == "agent-platform-observability"
    services = compose.get("services")
    assert isinstance(services, dict)
    assert set(services) == {"otel-collector", "jaeger", "prometheus", "loki", "grafana"}

    collector = services["otel-collector"]
    jaeger = services["jaeger"]
    prometheus = services["prometheus"]
    loki = services["loki"]
    grafana = services["grafana"]
    assert collector["image"] == COLLECTOR_IMAGE
    assert jaeger["image"] == JAEGER_IMAGE
    assert prometheus["image"] == PROMETHEUS_IMAGE
    assert loki["image"] == LOKI_IMAGE
    assert grafana["image"] == GRAFANA_IMAGE

    for service in services.values():
        assert service.get("restart") == "no"
        assert service.get("healthcheck") is None, (
            "scratch images cannot perform an in-container HTTP health probe; "
            "use test.sh start-health"
        )
        assert not service.get("privileged", False)
        assert service.get("security_opt") == ["no-new-privileges:true"]

    published_ports = [
        str(port)
        for service in services.values()
        for port in service.get("ports", [])
    ]
    assert published_ports
    assert all(port.startswith(LOOPBACK) for port in published_ports)
    assert any(port.endswith(":4317") for port in published_ports)
    assert any(port.endswith(":4318") for port in published_ports)
    assert any(port.endswith(":16686") for port in published_ports)
    assert any(port.endswith(":9090") for port in published_ports)
    assert any(port.endswith(":3000") for port in published_ports)

    assert "volumes" not in compose, "local traces must remain ephemeral"
    assert jaeger.get("volumes") is None
    assert jaeger.get("environment") == {
        "COLLECTOR_OTLP_ENABLED": "true",
        "SPAN_STORAGE_TYPE": "memory",
    }
    assert collector.get("depends_on") == {
        "jaeger": {"condition": "service_started"},
        "loki": {"condition": "service_started"},
    }
    assert prometheus.get("depends_on") == {"otel-collector": {"condition": "service_started"}}
    assert grafana.get("depends_on") == {
        "jaeger": {"condition": "service_started"},
        "loki": {"condition": "service_started"},
        "prometheus": {"condition": "service_started"},
    }
    assert collector.get("tmpfs") == ["/tmp"]

    assert collector.get("volumes") == [
        "../observability/otel-collector.yml:/etc/otelcol-contrib/config.yaml:ro"
    ]
    assert prometheus.get("volumes") == [
        "../observability/prometheus.yml:/etc/prometheus/prometheus.yml:ro",
        "../observability/alert-rules.yml:/etc/prometheus/alert-rules.yml:ro",
    ]
    assert loki.get("volumes") == [
        "../observability/loki.yml:/etc/loki/local-config.yaml:ro"
    ]
    assert grafana.get("volumes") == [
        "../observability/grafana/provisioning:/etc/grafana/provisioning:ro",
        "../observability/grafana/dashboards:/var/lib/grafana/dashboards:ro",
    ]


def test_collector_accepts_otlp_and_exports_traces_metrics_and_logs() -> None:
    config = load_yaml(COLLECTOR_CONFIG_PATH)

    receiver = config["receivers"]["otlp"]
    assert receiver == {
        "protocols": {
            "grpc": {"endpoint": "0.0.0.0:4317"},
            "http": {"endpoint": "0.0.0.0:4318"},
        }
    }

    exporter = config["exporters"]["otlp/jaeger"]
    assert exporter == {
        "endpoint": "jaeger:4317",
        "tls": {"insecure": True},
    }
    assert config["exporters"]["prometheus"] == {"endpoint": "0.0.0.0:8889"}
    assert config["exporters"]["otlphttp/loki"] == {"endpoint": "http://loki:3100/otlp"}

    service = config["service"]
    assert service["extensions"] == ["health_check"]
    pipelines = service["pipelines"]
    assert set(pipelines) == {"traces", "metrics", "logs"}
    assert pipelines["traces"]["receivers"] == ["otlp"]
    assert pipelines["traces"]["processors"] == ["batch"]
    assert pipelines["traces"]["exporters"] == ["otlp/jaeger"]
    assert pipelines["metrics"]["receivers"] == ["otlp"]
    assert pipelines["metrics"]["processors"] == ["batch"]
    assert pipelines["metrics"]["exporters"] == ["prometheus"]
    assert pipelines["logs"]["receivers"] == ["otlp"]
    assert pipelines["logs"]["processors"] == ["batch"]
    assert pipelines["logs"]["exporters"] == ["otlphttp/loki"]

    assert config["extensions"]["health_check"]["endpoint"] == "0.0.0.0:13133"
    assert config["processors"] == {"batch": {}}


def test_prometheus_loki_and_grafana_are_provisioned() -> None:
    prometheus = load_yaml(PROMETHEUS_CONFIG_PATH)
    assert prometheus["rule_files"] == ["/etc/prometheus/alert-rules.yml"]
    scrape_configs = prometheus["scrape_configs"]
    assert scrape_configs == [
        {
            "job_name": "agent-platform-otel-collector",
            "static_configs": [{"targets": ["otel-collector:8889"]}],
        }
    ]

    loki = load_yaml(LOKI_CONFIG_PATH)
    assert loki["auth_enabled"] is False
    assert loki["analytics"]["reporting_enabled"] is False
    assert loki["common"]["path_prefix"] == "/tmp/loki"

    datasources = load_yaml(GRAFANA_DATASOURCES_PATH)
    configured = {datasource["name"]: datasource for datasource in datasources["datasources"]}
    assert configured["Prometheus"]["url"] == "http://prometheus:9090"
    assert configured["Loki"]["url"] == "http://loki:3100"
    assert configured["Jaeger"]["url"] == "http://jaeger:16686"
    assert configured["Prometheus"]["isDefault"] is True

    dashboards = load_yaml(GRAFANA_DASHBOARDS_PATH)
    providers = dashboards["providers"]
    assert providers[0]["options"]["path"] == "/var/lib/grafana/dashboards"
    dashboard = load_json(GRAFANA_DASHBOARD_DIR / "agent-platform-operations.json")
    assert dashboard["uid"] == "agent-platform-operations"
    assert dashboard["title"] == "Agent Platform 运维总览"


def test_health_script_cannot_remove_core_orphans() -> None:
    script = (ROOT / "infra" / "observability" / "test.sh").read_text(encoding="utf-8")
    assert "--remove-orphans" not in script
    assert "http://127.0.0.1:${otel_health_port}/" in script
    assert "http://127.0.0.1:${jaeger_ui_port}/" in script
    assert "http://127.0.0.1:${prometheus_port}/-/ready" in script
    assert "http://127.0.0.1:${grafana_port}/api/health" in script


def test_alert_rules_cover_c14_operability_domains() -> None:
    rules = load_yaml(ALERT_RULES_PATH)
    groups = rules.get("groups")
    assert isinstance(groups, list)
    alert_names: set[str] = set()
    expressions: dict[str, str] = {}
    for group in groups:
        assert isinstance(group, dict)
        for rule in group.get("rules", []):
            assert isinstance(rule, dict)
            alert = rule.get("alert")
            expr = rule.get("expr")
            assert isinstance(alert, str)
            assert isinstance(expr, str)
            alert_names.add(alert)
            expressions[alert] = expr
            annotations = rule.get("annotations")
            labels = rule.get("labels")
            assert isinstance(annotations, dict)
            assert isinstance(labels, dict)
            assert labels.get("service") == "agent-platform"
            assert "summary" in annotations

    assert {
        "AgentPlatformApiHighErrorRate",
        "AgentPlatformApiLatencyHigh",
        "AgentPlatformWorkerRunFailures",
        "AgentPlatformQueueDeadLetters",
        "AgentPlatformModelGatewayFailures",
        "AgentPlatformRagFlowFailures",
        "AgentPlatformSandboxFailures",
        "AgentPlatformClientErrors",
        "AgentPlatformAuditIngestionFailures",
    }.issubset(alert_names)
    combined = "\n".join(expressions.values())
    for metric_name in [
        "agent_platform_api_server_requests_total",
        "agent_platform_api_server_duration_milliseconds_bucket",
        "agent_platform_worker_runs_failed_total",
        "agent_platform_queue_dead_letters_total",
        "agent_platform_model_gateway_requests_total",
        "agent_platform_ragflow_requests_total",
        "agent_platform_sandbox_operations_total",
        "agent_platform_client_errors_total",
        "agent_platform_audit_events_failed_total",
    ]:
        assert metric_name in combined


if __name__ == "__main__":
    test_compose_contract()
    test_collector_accepts_otlp_and_exports_traces_metrics_and_logs()
    test_prometheus_loki_and_grafana_are_provisioned()
    test_health_script_cannot_remove_core_orphans()
    test_alert_rules_cover_c14_operability_domains()
    print("observability configuration contract: OK")
