from __future__ import annotations

import logging
import socket
from threading import Event, Lock, Thread
from time import sleep
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from docker.errors import NotFound
from fastapi.testclient import TestClient

from agent_platform.platform.artifacts.entities import MAX_FILE_SIZE_BYTES
from agent_platform.sandbox.controller.api import create_controller_app
from agent_platform.sandbox.controller.config import ControllerSettings
from agent_platform.sandbox.controller.service import (
    EPOCH_LABEL,
    EXECUTE_SUPERVISOR_SCRIPT,
    LEASE_LABEL,
    MANAGED_LABEL,
    DockerSandboxController,
    SandboxDiscoveryAmbiguous,
    SandboxLeaseMismatch,
    SandboxLeaseRetired,
)

PINNED_IMAGE = "python:3.12.13-slim-bookworm@sha256:" + "a" * 64
SANDBOX_ID = "b" * 64


class FakeContainers:
    def __init__(self) -> None:
        self.created: list[dict[str, object]] = []
        self.missing = False
        self.container = SimpleNamespace(
            id=SANDBOX_ID,
            status="running",
            labels={"agent-platform.sandbox.lease-id": ""},
            reload=lambda: None,
            remove=lambda **_kwargs: None,
        )
        self.listed: list[object] = []

    def list(self, *, all: bool, filters: dict[str, object]) -> list[object]:
        del all, filters
        return self.listed

    def run(self, image: str, **kwargs: object) -> object:
        self.created.append({"image": image, **kwargs})
        self.container.labels = kwargs["labels"]
        return self.container

    def get(self, sandbox_id: str) -> object:
        if self.missing:
            raise NotFound("missing")
        assert sandbox_id == self.container.id
        return self.container


class FakeDocker:
    def __init__(self) -> None:
        self.containers = FakeContainers()


def settings(secret: str = "test-secret-long-enough") -> ControllerSettings:
    return ControllerSettings(
        bearer_secret=secret,
        sandbox_image=PINNED_IMAGE,
        max_file_bytes=1024,
        max_output_bytes=2048,
    )


def test_controller_fails_fast_without_secret_or_pinned_image() -> None:
    with pytest.raises(ValueError, match="bearer"):
        ControllerSettings(bearer_secret="", sandbox_image=PINNED_IMAGE)
    with pytest.raises(ValueError, match="digest"):
        ControllerSettings(bearer_secret="test-secret-long-enough", sandbox_image="python:3.12")


def test_controller_default_accepts_the_public_attachment_size_contract() -> None:
    configured = ControllerSettings(
        bearer_secret="test-secret-long-enough",
        sandbox_image=PINNED_IMAGE,
    )

    assert configured.max_file_bytes == MAX_FILE_SIZE_BYTES
    assert configured.max_batch_bytes >= MAX_FILE_SIZE_BYTES


def test_api_requires_bearer_auth_and_never_echoes_secret() -> None:
    app = create_controller_app(settings=settings(), docker_client=FakeDocker())
    client = TestClient(app)

    missing = client.post("/v1/sandboxes", json={"lease_id": str(uuid4())})
    invalid = client.post(
        "/v1/sandboxes",
        headers={"Authorization": "Bearer wrong-secret"},
        json={"lease_id": str(uuid4())},
    )

    assert missing.status_code == 401
    assert invalid.status_code == 401
    assert "test-secret" not in missing.text + invalid.text

    discovery = client.get("/v1/sandboxes", params={"lease_id": str(uuid4())})
    delete_by_lease = client.delete(
        "/v1/sandboxes",
        params={"lease_id": str(uuid4()), "sandbox_epoch": 1},
    )
    assert discovery.status_code == 401
    assert delete_by_lease.status_code == 401


def test_delete_by_lease_rejects_negative_epoch_as_validation_error() -> None:
    client = TestClient(
        create_controller_app(settings=settings(), docker_client=FakeDocker()),
        headers={"Authorization": "Bearer test-secret-long-enough"},
    )

    response = client.delete(
        "/v1/sandboxes",
        params={"lease_id": str(uuid4()), "sandbox_epoch": -1},
    )

    assert response.status_code == 422


def test_create_requires_an_explicit_sandbox_epoch() -> None:
    docker = FakeDocker()
    controller = DockerSandboxController(settings=settings(), docker_client=docker)
    client = TestClient(
        create_controller_app(settings=settings(), docker_client=docker),
        headers={"Authorization": "Bearer test-secret-long-enough"},
    )

    with pytest.raises(TypeError, match="sandbox_epoch"):
        controller.create(lease_id=uuid4())

    response = client.post("/v1/sandboxes", json={"lease_id": str(uuid4())})
    assert response.status_code == 422
    assert docker.containers.created == []


def test_create_uses_only_controller_owned_hardened_options() -> None:
    docker = FakeDocker()
    controller = DockerSandboxController(settings=settings(), docker_client=docker)

    result = controller.create(lease_id=uuid4(), sandbox_epoch=1)

    assert result.sandbox_id == SANDBOX_ID
    options = docker.containers.created[0]
    assert options["image"] == PINNED_IMAGE
    assert options["platform"] == "linux/arm64"
    assert options["user"] == "65532:65532"
    assert options["read_only"] is True
    assert options["network_disabled"] is True
    assert options["network_mode"] == "none"
    assert options["cap_drop"] == ["ALL"]
    assert options["security_opt"] == ["no-new-privileges:true"]
    assert options["pids_limit"] == 64
    assert options["mem_limit"] == "256m"
    assert options["nano_cpus"] == 500_000_000
    assert options["tmpfs"] == {
        "/workspace": "rw,nosuid,nodev,size=67108864,uid=65532,gid=65532",
        "/skills": "rw,nosuid,nodev,size=67108864,uid=65532,gid=65532",
    }
    forbidden = {"privileged", "devices", "volumes", "pid_mode", "ipc_mode"}
    assert forbidden.isdisjoint(options)


def test_discovery_accepts_only_exact_managed_canonical_lease_labels() -> None:
    docker = FakeDocker()
    lease_id = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")

    def container(container_id: str, labels: dict[str, str]) -> object:
        return SimpleNamespace(id=container_id, labels=labels, reload=lambda: None)

    exact = container(
        "1" * 64,
        {MANAGED_LABEL: "true", LEASE_LABEL: str(lease_id), EPOCH_LABEL: "1"},
    )
    docker.containers.listed = [
        exact,
        container("2" * 64, {MANAGED_LABEL: "false", LEASE_LABEL: str(lease_id)}),
        container("3" * 64, {MANAGED_LABEL: "true", LEASE_LABEL: str(uuid4())}),
        container("4" * 64, {MANAGED_LABEL: "true", LEASE_LABEL: "not-a-uuid"}),
        container("5" * 64, {MANAGED_LABEL: "true", LEASE_LABEL: str(lease_id).upper()}),
    ]
    controller = DockerSandboxController(settings=settings(), docker_client=docker)

    assert controller.find_by_lease(lease_id=lease_id, sandbox_epoch=1) == ["1" * 64]


def test_discovery_of_multiple_exact_containers_fails_closed_and_logs_alarm(caplog) -> None:
    controller_logger = logging.getLogger("agent_platform.sandbox.controller.service")
    controller_logger.disabled = False
    caplog.set_level(logging.ERROR, logger=controller_logger.name)
    docker = FakeDocker()
    lease_id = uuid4()
    docker.containers.listed = [
        SimpleNamespace(
            id=value * 64,
            labels={MANAGED_LABEL: "true", LEASE_LABEL: str(lease_id), EPOCH_LABEL: "1"},
            reload=lambda: None,
        )
        for value in ("1", "2")
    ]
    controller = DockerSandboxController(settings=settings(), docker_client=docker)

    with pytest.raises(SandboxDiscoveryAmbiguous):
        controller.find_by_lease(lease_id=lease_id, sandbox_epoch=1)

    assert "sandbox_discovery_ambiguous" in caplog.text
    assert all(not getattr(item, "removed", False) for item in docker.containers.listed)


def test_delete_by_lease_tombstone_rejects_late_create() -> None:
    docker = FakeDocker()
    lease_id = uuid4()
    controller = DockerSandboxController(settings=settings(), docker_client=docker)

    assert controller.delete_by_lease(lease_id=lease_id, sandbox_epoch=3) is None
    with pytest.raises(SandboxLeaseRetired):
        controller.create(lease_id=lease_id, sandbox_epoch=2)

    assert docker.containers.created == []


def test_delete_zero_result_serializes_with_and_rejects_queued_late_create() -> None:
    lease_id = uuid4()
    delete_holds_lock = Event()
    allow_delete_to_finish = Event()

    class BarrierContainers(FakeContainers):
        def list(self, *, all: bool, filters: dict[str, object]) -> list[object]:
            del all, filters
            if not delete_holds_lock.is_set():
                delete_holds_lock.set()
                assert allow_delete_to_finish.wait(timeout=1)
            return []

    docker = FakeDocker()
    docker.containers = BarrierContainers()
    controller = DockerSandboxController(settings=settings(), docker_client=docker)
    create_errors: list[Exception] = []

    delete_thread = Thread(
        target=controller.delete_by_lease,
        kwargs={"lease_id": lease_id, "sandbox_epoch": 7},
    )

    def late_create() -> None:
        try:
            controller.create(lease_id=lease_id, sandbox_epoch=6)
        except Exception as error:
            create_errors.append(error)

    create_thread = Thread(target=late_create)
    delete_thread.start()
    assert delete_holds_lock.wait(timeout=1)
    create_thread.start()
    assert docker.containers.created == []
    allow_delete_to_finish.set()
    delete_thread.join(timeout=1)
    create_thread.join(timeout=1)

    assert len(create_errors) == 1
    assert isinstance(create_errors[0], SandboxLeaseRetired)
    assert docker.containers.created == []


def test_tombstone_and_lease_lock_registry_are_bounded_with_a_controllable_clock() -> None:
    docker = FakeDocker()
    now = [100.0]
    lease_id = uuid4()
    limited = ControllerSettings(
        bearer_secret="test-secret-long-enough",
        sandbox_image=PINNED_IMAGE,
        tombstone_ttl_seconds=300,
    )
    controller = DockerSandboxController(
        settings=limited,
        docker_client=docker,
        monotonic_clock=lambda: now[0],
    )

    controller.delete_by_lease(lease_id=lease_id, sandbox_epoch=2)

    assert controller.tracked_lease_lock_count == 0
    assert controller.tracked_tombstone_count == 1
    with pytest.raises(SandboxLeaseRetired):
        controller.create(lease_id=lease_id, sandbox_epoch=2)

    now[0] += 301
    controller.create(lease_id=lease_id, sandbox_epoch=2)

    assert controller.tracked_tombstone_count == 0
    assert controller.tracked_lease_lock_count == 0


def test_controller_api_discovers_and_deletes_one_exact_lease_container() -> None:
    docker = FakeDocker()
    lease_id = uuid4()
    removed: list[bool] = []
    docker.containers.listed = [
        SimpleNamespace(
            id=SANDBOX_ID,
            labels={MANAGED_LABEL: "true", LEASE_LABEL: str(lease_id), EPOCH_LABEL: "1"},
            reload=lambda: None,
            remove=lambda *, force: removed.append(force),
        )
    ]
    client = TestClient(
        create_controller_app(settings=settings(), docker_client=docker),
        headers={"Authorization": "Bearer test-secret-long-enough"},
    )

    found = client.get(
        "/v1/sandboxes",
        params={"lease_id": str(lease_id), "sandbox_epoch": 1},
    )
    deleted = client.delete(
        "/v1/sandboxes",
        params={"lease_id": str(lease_id), "sandbox_epoch": 1},
    )

    assert found.json() == {"sandbox_ids": [SANDBOX_ID]}
    assert deleted.status_code == 200
    assert deleted.json() == {"sandbox_id": SANDBOX_ID}
    assert removed == [True]


def test_idempotent_delete_discards_lock_after_container_was_force_removed() -> None:
    docker = FakeDocker()
    controller = DockerSandboxController(settings=settings(), docker_client=docker)
    lease_id = uuid4()
    created = controller.create(lease_id=lease_id, sandbox_epoch=1)
    assert controller.tracked_execution_lock_count == 1
    docker.containers.missing = True

    controller.delete(created.sandbox_id, lease_id=lease_id, sandbox_epoch=1)

    assert controller.tracked_execution_lock_count == 0


@pytest.mark.parametrize(
    "path",
    ["relative", "/etc/passwd", "/workspace/../etc/passwd", "/workspace/a\\b", "/workspace//a"],
)
def test_workspace_paths_are_strictly_validated(path: str) -> None:
    controller = DockerSandboxController(settings=settings(), docker_client=FakeDocker())

    with pytest.raises(ValueError, match="path"):
        controller.validate_workspace_path(path)


def test_create_rejects_client_owned_container_configuration() -> None:
    docker = FakeDocker()
    client = TestClient(create_controller_app(settings=settings(), docker_client=docker))

    response = client.post(
        "/v1/sandboxes",
        headers={"Authorization": "Bearer test-secret-long-enough"},
        json={
            "lease_id": str(uuid4()),
            "sandbox_epoch": 1,
            "image": "evil",
            "privileged": True,
        },
    )

    assert response.status_code == 422
    assert docker.containers.created == []


def test_every_operation_rejects_a_different_lease() -> None:
    docker = FakeDocker()
    expected_lease = uuid4()
    docker.containers.container.labels = {
        MANAGED_LABEL: "true",
        LEASE_LABEL: str(expected_lease),
        EPOCH_LABEL: "1",
    }
    controller = DockerSandboxController(settings=settings(), docker_client=docker)

    with pytest.raises(SandboxLeaseMismatch, match="lease mismatch"):
        controller.reconnect(SANDBOX_ID, lease_id=uuid4(), sandbox_epoch=1)
    with pytest.raises(SandboxLeaseMismatch, match="lease mismatch"):
        controller.delete(SANDBOX_ID, lease_id=uuid4(), sandbox_epoch=1)


def test_old_epoch_cannot_create_discover_or_delete_a_new_epoch_container() -> None:
    docker = FakeDocker()
    lease_id = uuid4()
    removed: list[bool] = []
    container = SimpleNamespace(
        id=SANDBOX_ID,
        status="running",
        labels={
            MANAGED_LABEL: "true",
            LEASE_LABEL: str(lease_id),
            EPOCH_LABEL: "8",
        },
        reload=lambda: None,
        remove=lambda *, force: removed.append(force),
    )
    docker.containers.container = container
    docker.containers.listed = [container]
    controller = DockerSandboxController(settings=settings(), docker_client=docker)

    with pytest.raises(SandboxLeaseRetired):
        controller.create(lease_id=lease_id, sandbox_epoch=7)

    assert controller.find_by_lease(lease_id=lease_id, sandbox_epoch=7) == []
    assert controller.delete_by_lease(lease_id=lease_id, sandbox_epoch=7) is None
    assert removed == []


@pytest.mark.parametrize("operation", ["reconnect", "execute", "upload", "download", "delete"])
def test_old_epoch_data_plane_operation_cannot_reach_a_new_epoch_container(
    operation: str,
) -> None:
    docker = FakeDocker()
    lease_id = uuid4()
    removed: list[bool] = []
    docker.containers.container = SimpleNamespace(
        id=SANDBOX_ID,
        status="running",
        labels={
            MANAGED_LABEL: "true",
            LEASE_LABEL: str(lease_id),
            EPOCH_LABEL: "8",
        },
        reload=lambda: None,
        remove=lambda *, force: removed.append(force),
    )
    controller = DockerSandboxController(settings=settings(), docker_client=docker)

    with pytest.raises(SandboxLeaseMismatch, match="epoch mismatch"):
        if operation == "reconnect":
            controller.reconnect(SANDBOX_ID, lease_id=lease_id, sandbox_epoch=7)
        elif operation == "execute":
            controller.execute(
                SANDBOX_ID,
                lease_id=lease_id,
                sandbox_epoch=7,
                command="true",
                timeout=1,
            )
        elif operation == "upload":
            controller.upload(
                SANDBOX_ID,
                lease_id=lease_id,
                sandbox_epoch=7,
                files=[("/workspace/demo", b"old")],
            )
        elif operation == "download":
            controller.download(
                SANDBOX_ID,
                lease_id=lease_id,
                sandbox_epoch=7,
                paths=["/workspace/demo"],
            )
        else:
            controller.delete(SANDBOX_ID, lease_id=lease_id, sandbox_epoch=7)

    assert removed == []


def test_execute_calls_for_one_sandbox_are_serialized() -> None:
    state_lock = Lock()
    active = 0
    max_active = 0
    expected_lease = uuid4()

    class ExecutableContainer:
        id = SANDBOX_ID
        status = "running"
        labels = {
            MANAGED_LABEL: "true",
            LEASE_LABEL: str(expected_lease),
            EPOCH_LABEL: "1",
        }

        @staticmethod
        def reload() -> None:
            return None

        @staticmethod
        def exec_run(command: list[str], **kwargs: object) -> object:
            nonlocal active, max_active
            del kwargs
            if len(command) > 2 and command[2] == EXECUTE_SUPERVISOR_SCRIPT:
                with state_lock:
                    active += 1
                    max_active = max(max_active, active)
                sleep(0.03)
                with state_lock:
                    active -= 1
                return SimpleNamespace(output=b"ok", exit_code=0)
            if len(command) > 3 and command[3].endswith(".output"):
                return SimpleNamespace(output=b"ok", exit_code=0)
            if len(command) > 3 and command[3].endswith(".json"):
                return SimpleNamespace(output=b'{"exit_code": 0, "truncated": false}', exit_code=0)
            return SimpleNamespace(output=b"", exit_code=0)

    docker = FakeDocker()
    docker.containers.container = ExecutableContainer()
    controller = DockerSandboxController(settings=settings(), docker_client=docker)

    threads = [
        Thread(
            target=controller.execute,
            kwargs={
                "sandbox_id": SANDBOX_ID,
                "lease_id": expected_lease,
                "sandbox_epoch": 1,
                "command": "true",
                "timeout": 1,
            },
        )
        for _ in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert max_active == 1


def test_upload_inspect_timeout_force_removes_the_sandbox() -> None:
    expected_lease = uuid4()

    class TimeoutContainer:
        id = SANDBOX_ID
        status = "running"
        labels = {
            MANAGED_LABEL: "true",
            LEASE_LABEL: str(expected_lease),
            EPOCH_LABEL: "1",
        }
        removed = False

        @staticmethod
        def reload() -> None:
            return None

        def remove(self, *, force: bool) -> None:
            assert force is True
            self.removed = True

    class TimeoutAPI:
        def __init__(self) -> None:
            self.peer: socket.socket | None = None

        @staticmethod
        def exec_create(*args: object, **kwargs: object) -> dict[str, str]:
            del args, kwargs
            return {"Id": "exec-id"}

        def exec_start(self, *args: object, **kwargs: object) -> object:
            del args, kwargs
            stream_socket, self.peer = socket.socketpair()
            return stream_socket.makefile("rb")

        @staticmethod
        def exec_inspect(_execution_id: str) -> dict[str, object]:
            return {"Running": True, "ExitCode": None}

    docker = FakeDocker()
    container = TimeoutContainer()
    docker.containers.container = container
    docker.api = TimeoutAPI()
    limited = ControllerSettings(
        bearer_secret="test-secret-long-enough",
        sandbox_image=PINNED_IMAGE,
        max_timeout_seconds=1,
    )
    controller = DockerSandboxController(settings=limited, docker_client=docker)

    with pytest.raises(RuntimeError, match="timed out"):
        controller.upload(
            SANDBOX_ID,
            lease_id=expected_lease,
            sandbox_epoch=1,
            files=[("/workspace/demo", b"content")],
        )

    assert container.removed is True
    if docker.api.peer is not None:
        docker.api.peer.close()


def test_upload_stdin_failure_force_removes_the_sandbox() -> None:
    expected_lease = uuid4()

    class Container:
        id = SANDBOX_ID
        status = "running"
        labels = {
            MANAGED_LABEL: "true",
            LEASE_LABEL: str(expected_lease),
            EPOCH_LABEL: "1",
        }
        removed = False

        @staticmethod
        def reload() -> None:
            return None

        def remove(self, *, force: bool) -> None:
            assert force is True
            self.removed = True

    class BrokenStream:
        @staticmethod
        def fileno() -> int:
            raise OSError("stdin unavailable")

        @staticmethod
        def close() -> None:
            return None

    class BrokenAPI:
        @staticmethod
        def exec_create(*args: object, **kwargs: object) -> dict[str, str]:
            del args, kwargs
            return {"Id": "exec-id"}

        @staticmethod
        def exec_start(*args: object, **kwargs: object) -> BrokenStream:
            del args, kwargs
            return BrokenStream()

    docker = FakeDocker()
    container = Container()
    docker.containers.container = container
    docker.api = BrokenAPI()
    controller = DockerSandboxController(settings=settings(), docker_client=docker)

    with pytest.raises(RuntimeError, match="upload failed"):
        controller.upload(
            SANDBOX_ID,
            lease_id=expected_lease,
            sandbox_epoch=1,
            files=[("/workspace/demo", b"content")],
        )

    assert container.removed is True


def test_oversized_single_file_and_batch_are_rejected_before_docker_access() -> None:
    docker = FakeDocker()
    limited = ControllerSettings(
        bearer_secret="test-secret-long-enough",
        sandbox_image=PINNED_IMAGE,
        max_file_bytes=4,
        max_batch_bytes=6,
    )
    client = TestClient(create_controller_app(settings=limited, docker_client=docker))
    headers = {
        "Authorization": "Bearer test-secret-long-enough",
        "X-Sandbox-Lease-ID": str(uuid4()),
        "X-Sandbox-Epoch": "1",
    }

    single = client.put(
        "/v1/sandboxes/unknown/files",
        headers=headers,
        json={"files": [{"path": "/workspace/a", "content_base64": "MTIzNDU="}]},
    )
    batch = client.put(
        "/v1/sandboxes/unknown/files",
        headers=headers,
        json={
            "files": [
                {"path": "/workspace/a", "content_base64": "MTIzNA=="},
                {"path": "/workspace/b", "content_base64": "MTIzNA=="},
            ]
        },
    )

    assert single.status_code in {400, 413, 422}
    assert batch.status_code in {400, 413, 422}
    assert docker.containers.created == []


def test_invalid_content_length_is_rejected_without_a_traceback() -> None:
    client = TestClient(create_controller_app(settings=settings(), docker_client=FakeDocker()))

    response = client.post(
        "/v1/sandboxes",
        headers={
            "Authorization": "Bearer test-secret-long-enough",
            "Content-Length": "invalid",
        },
        json={"lease_id": str(uuid4())},
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "invalid content length"}
