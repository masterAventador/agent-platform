from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
COMPOSE_PATH = ROOT / "infra" / "compose" / "observability.yml"
COLLECTOR_CONFIG_PATH = ROOT / "infra" / "observability" / "otel-collector.yml"

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


def test_collector_accepts_otlp_and_only_exports_traces() -> None:
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

    service = config["service"]
    assert service["extensions"] == ["health_check"]
    pipelines = service["pipelines"]
    assert set(pipelines) == {"traces"}, "metrics and logs must not be exported yet"
    assert pipelines["traces"]["receivers"] == ["otlp"]
    assert pipelines["traces"]["processors"] == ["batch"]
    assert pipelines["traces"]["exporters"] == ["otlp/jaeger"]

    assert config["extensions"]["health_check"]["endpoint"] == "0.0.0.0:13133"
    assert config["processors"] == {"batch": {}}


def test_health_script_cannot_remove_core_orphans() -> None:
    script = (ROOT / "infra" / "observability" / "test.sh").read_text(encoding="utf-8")
    assert "--remove-orphans" not in script
    assert "http://127.0.0.1:${otel_health_port}/" in script
    assert "http://127.0.0.1:${jaeger_ui_port}/" in script


if __name__ == "__main__":
    test_compose_contract()
    test_collector_accepts_otlp_and_only_exports_traces()
    test_health_script_cannot_remove_core_orphans()
    print("observability configuration contract: OK")
