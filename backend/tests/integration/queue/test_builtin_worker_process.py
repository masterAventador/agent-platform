from __future__ import annotations

import os
import subprocess
import sys


def test_worker_process_builds_builtin_adapters_without_external_module(tmp_path) -> None:
    environment = {
        **os.environ,
        "AGENT_PLATFORM_WORKER_CONFIG_CHECK": "1",
        "AGENT_PLATFORM_SANDBOX_CONTROLLER_SECRET": "process-controller-secret",
        "AGENT_PLATFORM_SANDBOX_CONTROLLER_URL": "http://sandbox-controller:8090",
        "AGENT_PLATFORM_LOCAL_CREDENTIALS_REPOSITORY_ROOT": str(tmp_path),
    }
    environment.pop("AGENT_PLATFORM_RUNTIME_ADAPTER_FACTORY", None)

    completed = subprocess.run(
        [sys.executable, "-m", "agent_platform.workers.main"],
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "module:attribute" not in completed.stderr
    assert "process-controller-secret" not in completed.stderr
