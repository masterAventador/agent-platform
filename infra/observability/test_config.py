from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
COMPOSE_PATH = ROOT / "infra" / "compose" / "observability.yml"
COLLECTOR_CONFIG_PATH = ROOT / "infra" / "observability" / "otel-collector.yml"
ALERT_RULES_PATH = ROOT / "infra" / "observability" / "alert-rules.yml"

COLLECTOR_IMAGE = "otel/opentelemetry-collector-contrib:0.156.0"
JAEGER_IMAGE = "jaegertracing/all-in-one:1.76.0"
LOOPBACK = "127.0.0.1:"


def load_yaml(path: Path) -> dict[str, object]:
    assert path.is_file(), f"missing required configuration: {path.relative_to(ROOT)}"
    with path.open(encoding="utf-8") as stream:
        document = yaml.safe_load(stream)
    assert isinstance(document, dict), f"expected a YAML mapping in {path.relative_to(ROOT)}"
    return document


def test_compose_contract() -> None:
    compose = load_yaml(COMPOSE_PATH)
    assert compose.get("name") == "agent-platform-observability"
    services = compose.get("services")
    assert isinstance(services, dict)
    assert set(services) == {"otel-collector", "jaeger"}

    collector = services["otel-collector"]
    jaeger = services["jaeger"]
    assert collector["image"] == COLLECTOR_IMAGE
    assert jaeger["image"] == JAEGER_IMAGE

    for service in (collector, jaeger):
        assert service.get("restart") == "no"
        assert service.get("healthcheck") is None, (
            "scratch images cannot perform an in-container HTTP health probe; "
            "use test.sh start-health"
        )
        assert not service.get("privileged", False)

    published_ports = [
        str(port)
        for service in (collector, jaeger)
        for port in service.get("ports", [])
    ]
    assert published_ports
    assert all(port.startswith(LOOPBACK) for port in published_ports)
    assert any(port.endswith(":4317") for port in published_ports)
    assert any(port.endswith(":4318") for port in published_ports)
    assert any(port.endswith(":16686") for port in published_ports)

    assert "volumes" not in compose, "local traces must remain ephemeral"
    assert jaeger.get("volumes") is None
    assert jaeger.get("environment") == {
        "COLLECTOR_OTLP_ENABLED": "true",
        "SPAN_STORAGE_TYPE": "memory",
    }
    assert collector.get("depends_on") == {"jaeger": {"condition": "service_started"}}
    assert collector.get("tmpfs") == ["/tmp"]

    mounts = collector.get("volumes")
    assert mounts == ["../observability/otel-collector.yml:/etc/otelcol-contrib/config.yaml:ro"]


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
    assert config["exporters"]["debug/metrics"] == {"verbosity": "basic"}
    assert config["exporters"]["debug/logs"] == {"verbosity": "basic"}

    service = config["service"]
    assert service["extensions"] == ["health_check"]
    pipelines = service["pipelines"]
    assert set(pipelines) == {"traces", "metrics", "logs"}
    assert pipelines["traces"]["receivers"] == ["otlp"]
    assert pipelines["traces"]["processors"] == ["batch"]
    assert pipelines["traces"]["exporters"] == ["otlp/jaeger"]
    assert pipelines["metrics"]["receivers"] == ["otlp"]
    assert pipelines["metrics"]["processors"] == ["batch"]
    assert pipelines["metrics"]["exporters"] == ["debug/metrics"]
    assert pipelines["logs"]["receivers"] == ["otlp"]
    assert pipelines["logs"]["processors"] == ["batch"]
    assert pipelines["logs"]["exporters"] == ["debug/logs"]

    assert config["extensions"]["health_check"]["endpoint"] == "0.0.0.0:13133"
    assert config["processors"] == {"batch": {}}


def test_health_script_cannot_remove_core_orphans() -> None:
    script = (ROOT / "infra" / "observability" / "test.sh").read_text(encoding="utf-8")
    assert "--remove-orphans" not in script
    assert "http://127.0.0.1:${otel_health_port}/" in script
    assert "http://127.0.0.1:${jaeger_ui_port}/" in script


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
    test_health_script_cannot_remove_core_orphans()
    test_alert_rules_cover_c14_operability_domains()
    print("observability configuration contract: OK")
