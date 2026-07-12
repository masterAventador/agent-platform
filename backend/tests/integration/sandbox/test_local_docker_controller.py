from __future__ import annotations

import base64
import hashlib
import os
import time
from uuid import uuid4

import docker
import pytest
from fastapi.testclient import TestClient

from agent_platform.sandbox.controller.api import create_controller_app
from agent_platform.sandbox.controller.config import ControllerSettings
from agent_platform.sandbox.controller.service import EPOCH_LABEL, LEASE_LABEL, MANAGED_LABEL

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LOCAL_DOCKER_SANDBOX_TESTS") != "1",
    reason="set RUN_LOCAL_DOCKER_SANDBOX_TESTS=1 to run the destructive local Docker test",
)


def test_real_arm64_sandbox_lifecycle_is_hardened_and_leaves_no_container() -> None:
    secret = "local-integration-secret"
    image = os.environ["SANDBOX_CONTROLLER_IMAGE"]
    docker_client = docker.from_env()
    app = create_controller_app(
        settings=ControllerSettings(bearer_secret=secret, sandbox_image=image),
        docker_client=docker_client,
    )
    client = TestClient(app, headers={"Authorization": f"Bearer {secret}"})
    lease_id = uuid4()
    lease_headers = {
        "X-Sandbox-Lease-ID": str(lease_id),
        "X-Sandbox-Epoch": "1",
    }
    sandbox_id: str | None = None

    try:
        created = client.post(
            "/v1/sandboxes",
            json={"lease_id": str(lease_id), "sandbox_epoch": 1},
        )
        assert created.status_code == 200, created.text
        sandbox_id = created.json()["sandbox_id"]

        discovered = client.get(
            "/v1/sandboxes",
            params={"lease_id": str(lease_id), "sandbox_epoch": 1},
        )
        assert discovered.status_code == 200, discovered.text
        assert discovered.json() == {"sandbox_ids": [sandbox_id]}

        uploaded = client.put(
            f"/v1/sandboxes/{sandbox_id}/files",
            headers=lease_headers,
            json={
                "files": [
                    {
                        "path": "/skills/demo/SKILL.md",
                        "content_base64": "IyBEZW1vCg==",
                    }
                ]
            },
        )
        assert uploaded.status_code == 200, uploaded.text

        boundary_files = {
            "/workspace/empty.bin": b"",
            "/workspace/binary.bin": bytes(range(256)),
            "/workspace/max.bin": b"\xa5" * (8 * 1024 * 1024),
        }
        boundary_upload = client.put(
            f"/v1/sandboxes/{sandbox_id}/files",
            headers=lease_headers,
            json={
                "files": [
                    {
                        "path": path,
                        "content_base64": base64.b64encode(content).decode(),
                    }
                    for path, content in boundary_files.items()
                ]
            },
        )
        assert boundary_upload.status_code == 200, boundary_upload.text
        boundary_download = client.post(
            f"/v1/sandboxes/{sandbox_id}/download",
            headers=lease_headers,
            json={"paths": list(boundary_files)},
        )
        assert boundary_download.status_code == 200, boundary_download.text
        downloaded_by_path = {
            item["path"]: base64.b64decode(item["content_base64"])
            for item in boundary_download.json()["files"]
        }
        assert downloaded_by_path["/workspace/empty.bin"] == b""
        assert downloaded_by_path["/workspace/binary.bin"] == bytes(range(256))
        assert hashlib.sha256(downloaded_by_path["/workspace/max.bin"]).digest() == (
            hashlib.sha256(boundary_files["/workspace/max.bin"]).digest()
        )

        executed = client.post(
            f"/v1/sandboxes/{sandbox_id}/exec",
            headers=lease_headers,
            json={"command": "cat /skills/demo/SKILL.md"},
        )
        assert executed.json() == {"output": "# Demo\n", "exit_code": 0, "truncated": False}

        downloaded = client.post(
            f"/v1/sandboxes/{sandbox_id}/download",
            headers=lease_headers,
            json={"paths": ["/skills/demo/SKILL.md"]},
        )
        assert downloaded.json()["files"][0]["content_base64"] == "IyBEZW1vCg=="

        network = client.post(
            f"/v1/sandboxes/{sandbox_id}/exec",
            headers=lease_headers,
            json={
                "command": (
                    'python -c "import socket; '
                    "socket.create_connection(('1.1.1.1', 53), timeout=1)\""
                ),
                "timeout": 5,
            },
        )
        assert network.json()["exit_code"] != 0

        started = time.monotonic()
        timed_out = client.post(
            f"/v1/sandboxes/{sandbox_id}/exec",
            headers=lease_headers,
            json={"command": "sleep 30", "timeout": 1},
        )
        assert timed_out.json()["exit_code"] in {124, 137}
        assert time.monotonic() - started < 5

        started = time.monotonic()
        excessive_output = client.post(
            f"/v1/sandboxes/{sandbox_id}/exec",
            headers=lease_headers,
            json={"command": "yes bounded-output", "timeout": 30},
        )
        assert excessive_output.status_code == 200, excessive_output.text
        assert excessive_output.json()["truncated"] is True
        assert len(excessive_output.json()["output"].encode()) <= 1024 * 1024
        assert time.monotonic() - started < 5

        container = docker_client.containers.get(sandbox_id)
        container.reload()
        host = container.attrs["HostConfig"]
        assert container.attrs["Config"]["User"] == "65532:65532"
        assert container.attrs["Config"]["Labels"] == {
            EPOCH_LABEL: "1",
            LEASE_LABEL: str(lease_id),
            MANAGED_LABEL: "true",
        }
        assert host["ReadonlyRootfs"] is True
        assert host["NetworkMode"] == "none"
        assert host["CapDrop"] == ["ALL"]
        assert "no-new-privileges:true" in host["SecurityOpt"]
        assert host["PidsLimit"] == 64
        assert host["Memory"] == 256 * 1024 * 1024
        assert host["NanoCpus"] == 500_000_000
        assert host["Binds"] is None
        assert host["Devices"] in (None, [])
        assert host["Init"] is True

        wrong_lease = client.post(
            f"/v1/sandboxes/{sandbox_id}/exec",
            headers={
                "X-Sandbox-Lease-ID": str(uuid4()),
                "X-Sandbox-Epoch": "1",
            },
            json={"command": "true"},
        )
        assert wrong_lease.status_code == 404

        background = client.post(
            f"/v1/sandboxes/{sandbox_id}/exec",
            headers=lease_headers,
            json={"command": "sleep 999 >/dev/null 2>&1 &"},
        )
        assert background.status_code == 200
        processes = container.top()["Processes"]
        assert all("sleep 999" not in " ".join(process) for process in processes)
        assert all("yes bounded-output" not in " ".join(process) for process in processes)
    finally:
        if sandbox_id is not None:
            deleted = client.delete(
                "/v1/sandboxes",
                params={"lease_id": str(lease_id), "sandbox_epoch": 1},
            )
            assert deleted.status_code == 200
            assert deleted.json() == {"sandbox_id": sandbox_id}
        remaining = docker_client.containers.list(
            all=True,
            filters={"label": [f"{MANAGED_LABEL}=true", f"{LEASE_LABEL}={lease_id}"]},
        )
        assert remaining == []
