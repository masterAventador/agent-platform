from __future__ import annotations

import json
import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = ROOT / "infra/compose/core.yml"
COMPOSE_ENV = ROOT / "infra/compose/.env.example"
TEST_ENTRY = ROOT / "infra/compose/test.sh"
LOOPBACK_HOST = "127.0.0.1"
MIN_DOCKER_COMPOSE_VERSION = "2.20.0"


def assert_loopback_published_ports(config: dict[str, Any]) -> int:
    published_count = 0
    violations: list[str] = []
    for service_name, service in config.get("services", {}).items():
        for port in service.get("ports", []):
            published = port.get("published")
            if published is None:
                continue
            published_count += 1
            if port.get("host_ip") != LOOPBACK_HOST:
                violations.append(
                    f"{service_name}:{published}->{port.get('target')} "
                    f"host_ip={port.get('host_ip')!r}"
                )

    if violations:
        raise AssertionError(
            "core published ports must bind only to 127.0.0.1: " + ", ".join(violations)
        )
    return published_count


class CoreComposeContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        result = subprocess.run(
            [
                "docker",
                "compose",
                "--env-file",
                str(COMPOSE_ENV),
                "-f",
                str(COMPOSE_FILE),
                "config",
                "--format",
                "json",
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        cls.config = json.loads(result.stdout)

    def test_all_core_published_ports_bind_to_loopback(self) -> None:
        self.assertEqual(assert_loopback_published_ports(self.config), 4)

    def test_loopback_contract_rejects_a_missing_host_ip(self) -> None:
        invalid_config = {
            "services": {
                "postgres": {
                    "ports": [{"published": "5432", "target": 5432, "protocol": "tcp"}]
                }
            }
        }

        with self.assertRaisesRegex(AssertionError, "postgres:5432->5432"):
            assert_loopback_published_ports(invalid_config)


@unittest.skipIf(
    os.environ.get("CORE_COMPOSE_TEST_ENTRY") == "1",
    "standard entry already owns this nested contract run",
)
class CoreComposeStandardEntryTest(unittest.TestCase):
    def _run_entry(
        self,
        *,
        compose_version: str = MIN_DOCKER_COMPOSE_VERSION,
        version_exit: int = 0,
    ) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            fake_bin = temp_path / "bin"
            fake_bin.mkdir()
            fake_docker = fake_bin / "docker"
            fake_docker.write_text(
                textwrap.dedent(
                    """\
                    #!/usr/bin/env python3
                    import json
                    import os
                    import sys

                    args = sys.argv[1:]
                    if args == ["compose", "version", "--short"]:
                        print(os.environ["FAKE_COMPOSE_VERSION"])
                        raise SystemExit(int(os.environ["FAKE_COMPOSE_VERSION_EXIT"]))
                    if args[-3:] == ["config", "--format", "json"]:
                        ports = {
                            "postgres": ("5432", 5432),
                            "redis": ("6379", 6379),
                            "minio": ("9000", 9000),
                            "minio-console": ("9001", 9001),
                        }
                        print(json.dumps({
                            "services": {
                                name: {
                                    "ports": [{
                                        "host_ip": "127.0.0.1",
                                        "published": published,
                                        "target": target,
                                        "protocol": "tcp",
                                    }]
                                }
                                for name, (published, target) in ports.items()
                            }
                        }))
                        raise SystemExit(0)
                    if args[-2:] == ["config", "--quiet"]:
                        raise SystemExit(0)
                    print(f"unexpected fake docker arguments: {args}", file=sys.stderr)
                    raise SystemExit(2)
                    """
                ),
                encoding="utf-8",
            )
            fake_docker.chmod(0o755)
            env = os.environ.copy()
            env.update(
                {
                    "FAKE_COMPOSE_VERSION": compose_version,
                    "FAKE_COMPOSE_VERSION_EXIT": str(version_exit),
                    "PATH": f"{fake_bin}{os.pathsep}{env['PATH']}",
                }
            )
            return subprocess.run(
                ["bash", str(TEST_ENTRY), "config"],
                cwd=temp_path,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

    def test_config_entry_runs_from_an_arbitrary_working_directory(self) -> None:
        result = self._run_entry()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("core compose contract passed", result.stdout)

    def test_config_entry_fails_clearly_when_compose_is_unavailable(self) -> None:
        result = self._run_entry(version_exit=127)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Docker Compose v2 is required", result.stderr)

    def test_config_entry_fails_clearly_for_unsupported_compose_version(self) -> None:
        result = self._run_entry(compose_version="2.19.9")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            f"requires Docker Compose >= {MIN_DOCKER_COMPOSE_VERSION}",
            result.stderr,
        )


if __name__ == "__main__":
    unittest.main()
