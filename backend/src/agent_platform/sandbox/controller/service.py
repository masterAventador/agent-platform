from __future__ import annotations

import base64
import json
import logging
import os
import socket
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import PurePosixPath
from threading import Lock, RLock
from typing import Any
from uuid import UUID, uuid4

from docker.errors import APIError, NotFound

from agent_platform.sandbox.controller.config import ControllerSettings

LEASE_LABEL = "agent-platform.sandbox.lease-id"
MANAGED_LABEL = "agent-platform.sandbox.managed"
EPOCH_LABEL = "agent-platform.sandbox.epoch"
logger = logging.getLogger(__name__)
UPLOAD_SCRIPT = (
    "import os,sys;"
    "path=sys.argv[1];"
    "os.makedirs(os.path.dirname(path),exist_ok=True);"
    "open(path,'wb').write(sys.stdin.buffer.read())"
)
DOWNLOAD_SCRIPT = (
    "import pathlib,sys;"
    "path=pathlib.Path(sys.argv[1]);limit=int(sys.argv[2]);"
    "sys.exit(2) if not path.exists() else None;"
    "sys.exit(3) if not path.is_file() else None;"
    "sys.stdout.buffer.write(path.read_bytes()[:limit+1])"
)
EXECUTE_SUPERVISOR_SCRIPT = """
import json, os, select, signal, subprocess, sys, time
command, output_path, metadata_path, timeout_raw, limit_raw = sys.argv[1:]
timeout, limit = int(timeout_raw), int(limit_raw)
process = subprocess.Popen(
    ["/bin/sh", "-lc", command],
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    start_new_session=True,
)
assert process.stdout is not None
deadline = time.monotonic() + timeout
output = bytearray()
timed_out = False
truncated = False
while True:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        timed_out = True
        break
    ready, _, _ = select.select([process.stdout], [], [], min(0.1, remaining))
    if ready:
        chunk = os.read(process.stdout.fileno(), min(65536, limit + 1 - len(output)))
        if chunk:
            output.extend(chunk)
            if len(output) > limit:
                truncated = True
                break
        elif process.poll() is not None:
            break
    elif process.poll() is not None:
        break
if timed_out or truncated:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
exit_code = process.wait()
if timed_out:
    exit_code = 124
with open(output_path, "wb") as output_file:
    output_file.write(output[:limit])
with open(metadata_path, "w", encoding="utf-8") as metadata_file:
    json.dump({"exit_code": exit_code, "truncated": truncated}, metadata_file)
""".strip()


@dataclass(frozen=True)
class CreatedSandbox:
    sandbox_id: str


@dataclass(frozen=True)
class ExecutionResult:
    output: str
    exit_code: int | None
    truncated: bool


@dataclass
class LeaseLockEntry:
    lock: RLock
    users: int = 0


@dataclass(frozen=True)
class LeaseTombstone:
    sandbox_epoch: int
    expires_at: float


class SandboxNotFound(LookupError):
    pass


class SandboxLeaseMismatch(PermissionError):
    pass


class SandboxDiscoveryAmbiguous(RuntimeError):
    pass


class SandboxLeaseRetired(RuntimeError):
    pass


class DockerSandboxController:
    """Docker socket 的唯一持有者；Worker 只能调用它的内部 HTTP API。"""

    def __init__(
        self,
        *,
        settings: ControllerSettings,
        docker_client: Any,
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._settings = settings
        self._docker = docker_client
        self._execution_locks: dict[str, Lock] = {}
        self._locks_guard = Lock()
        self._lease_locks: dict[UUID, LeaseLockEntry] = {}
        self._tombstones: dict[UUID, LeaseTombstone] = {}
        self._monotonic_clock = monotonic_clock

    def create(self, *, lease_id: UUID, sandbox_epoch: int) -> CreatedSandbox:
        if sandbox_epoch < 1:
            raise ValueError("sandbox epoch 必须大于零")
        with self._lease_guard(lease_id):
            retired_epoch = self._retired_epoch(lease_id)
            if retired_epoch is not None and sandbox_epoch <= retired_epoch:
                raise SandboxLeaseRetired("sandbox lease generation is retired")
            existing = self._find_exact_by_lease(lease_id)
            if existing:
                container = existing[0]
                existing_epoch = self._container_epoch(container)
                if existing_epoch > sandbox_epoch:
                    raise SandboxLeaseRetired("sandbox lease generation is retired")
                if existing_epoch < sandbox_epoch:
                    raise SandboxDiscoveryAmbiguous("older sandbox generation still exists")
                container.reload()
                if container.status != "running":
                    container.start()
                sandbox_id = str(container.id)
                self._execution_lock(sandbox_id)
                return CreatedSandbox(sandbox_id=sandbox_id)
            container = self._docker.containers.run(
                self._settings.sandbox_image,
                command=["python", "-c", "import time; time.sleep(31536000)"],
                detach=True,
                labels={
                    MANAGED_LABEL: "true",
                    LEASE_LABEL: str(lease_id),
                    EPOCH_LABEL: str(sandbox_epoch),
                },
                platform=self._settings.platform,
                user="65532:65532",
                working_dir="/workspace",
                read_only=True,
                tmpfs={
                    "/workspace": (
                        f"rw,nosuid,nodev,size={self._settings.workspace_bytes},uid=65532,gid=65532"
                    ),
                    "/skills": (
                        f"rw,nosuid,nodev,size={self._settings.workspace_bytes},uid=65532,gid=65532"
                    ),
                },
                network_disabled=True,
                network_mode="none",
                cap_drop=["ALL"],
                security_opt=["no-new-privileges:true"],
                pids_limit=self._settings.pids_limit,
                mem_limit=self._settings.memory_limit,
                nano_cpus=self._settings.nano_cpus,
                init=True,
                auto_remove=False,
            )
            sandbox_id = str(container.id)
            self._execution_lock(sandbox_id)
            return CreatedSandbox(sandbox_id=sandbox_id)

    def find_by_lease(self, *, lease_id: UUID, sandbox_epoch: int) -> list[str]:
        with self._lease_guard(lease_id):
            return [
                str(container.id)
                for container in self._find_exact_by_lease(
                    lease_id,
                    sandbox_epoch=sandbox_epoch,
                )
            ]

    def delete_by_lease(self, *, lease_id: UUID, sandbox_epoch: int) -> str | None:
        with self._lease_guard(lease_id):
            containers = self._find_exact_by_lease(
                lease_id,
                sandbox_epoch=sandbox_epoch,
            )
            self._retire_generation(lease_id, sandbox_epoch=sandbox_epoch)
            if not containers:
                return None
            container = containers[0]
            sandbox_id = str(container.id)
            container.remove(force=True)
            with self._locks_guard:
                self._execution_locks.pop(sandbox_id, None)
            return sandbox_id

    def reconnect(
        self, sandbox_id: str, *, lease_id: UUID, sandbox_epoch: int
    ) -> CreatedSandbox:
        container = self._managed_container(
            sandbox_id,
            lease_id=lease_id,
            sandbox_epoch=sandbox_epoch,
        )
        if container.status != "running":
            container.start()
        return CreatedSandbox(sandbox_id=str(container.id))

    def execute(
        self,
        sandbox_id: str,
        *,
        lease_id: UUID,
        sandbox_epoch: int,
        command: str,
        timeout: int | None,
    ) -> ExecutionResult:
        container = self._managed_container(
            sandbox_id,
            lease_id=lease_id,
            sandbox_epoch=sandbox_epoch,
        )
        effective_timeout = timeout or self._settings.max_timeout_seconds
        if effective_timeout > self._settings.max_timeout_seconds:
            raise ValueError("timeout 超过 controller 限制")
        with self._execution_lock(sandbox_id):
            execution_key = uuid4().hex
            output_path = f"/workspace/.controller-{execution_key}.output"
            metadata_path = f"/workspace/.controller-{execution_key}.json"
            try:
                result = container.exec_run(
                    [
                        "python",
                        "-c",
                        EXECUTE_SUPERVISOR_SCRIPT,
                        command,
                        output_path,
                        metadata_path,
                        str(effective_timeout),
                        str(self._settings.max_output_bytes),
                    ],
                    user="65532:65532",
                    workdir="/workspace",
                    demux=False,
                )
                if result.exit_code != 0:
                    raise RuntimeError("sandbox execution supervisor failed")
                output = self._read_file(container, output_path, self._settings.max_output_bytes)
                metadata = json.loads(
                    self._read_file(container, metadata_path, 1024).decode("utf-8")
                )
                self._kill_exec_children(container)
                container.exec_run(
                    ["rm", "-f", output_path, metadata_path],
                    user="65532:65532",
                    workdir="/workspace",
                    demux=False,
                )
            except APIError:
                # 失去执行控制时销毁整个 sandbox，不能留下未知后台进程。
                container.remove(force=True)
                raise RuntimeError("sandbox execution failed") from None
        return ExecutionResult(
            output=output.decode("utf-8", errors="replace"),
            exit_code=int(metadata["exit_code"]),
            truncated=bool(metadata["truncated"]),
        )

    def upload(
        self,
        sandbox_id: str,
        *,
        lease_id: UUID,
        sandbox_epoch: int,
        files: list[tuple[str, bytes]],
    ) -> list[str]:
        container = self._managed_container(
            sandbox_id,
            lease_id=lease_id,
            sandbox_epoch=sandbox_epoch,
        )
        if sum(len(content) for _, content in files) > self._settings.max_batch_bytes:
            raise ValueError("upload batch 超过 controller 限制")
        uploaded: list[str] = []
        for requested_path, content in files:
            path = self.validate_workspace_path(requested_path)
            if len(content) > self._settings.max_file_bytes:
                raise ValueError("file 超过 controller 限制")
            self._write_file_stdin(container, path=path, content=content)
            uploaded.append(path)
        return uploaded

    def download(
        self,
        sandbox_id: str,
        *,
        lease_id: UUID,
        sandbox_epoch: int,
        paths: list[str],
    ) -> list[tuple[str, bytes | None, str | None]]:
        container = self._managed_container(
            sandbox_id,
            lease_id=lease_id,
            sandbox_epoch=sandbox_epoch,
        )
        results: list[tuple[str, bytes | None, str | None]] = []
        for requested_path in paths:
            path = self.validate_workspace_path(requested_path)
            result = container.exec_run(
                [
                    "python",
                    "-c",
                    DOWNLOAD_SCRIPT,
                    path,
                    str(self._settings.max_file_bytes),
                ],
                user="65532:65532",
                workdir="/workspace",
                demux=False,
            )
            if result.exit_code == 2:
                results.append((path, None, "file_not_found"))
                continue
            if result.exit_code == 3:
                results.append((path, None, "is_directory"))
                continue
            if result.exit_code != 0:
                raise RuntimeError("sandbox file download failed")
            content = result.output if isinstance(result.output, bytes) else bytes(result.output)
            if len(content) > self._settings.max_file_bytes:
                raise ValueError("file 超过 controller 限制")
            results.append((path, content, None))
        return results

    def delete(self, sandbox_id: str, *, lease_id: UUID, sandbox_epoch: int) -> None:
        with self._lease_guard(lease_id):
            try:
                container = self._managed_container(
                    sandbox_id,
                    lease_id=lease_id,
                    sandbox_epoch=sandbox_epoch,
                )
            except SandboxNotFound:
                self._discard_execution_lock(sandbox_id)
                self._retire_generation(lease_id, sandbox_epoch=sandbox_epoch)
                return
            container.remove(force=True)
            self._discard_execution_lock(sandbox_id)
            self._retire_generation(lease_id, sandbox_epoch=sandbox_epoch)

    @property
    def tracked_execution_lock_count(self) -> int:
        with self._locks_guard:
            return len(self._execution_locks)

    @property
    def tracked_lease_lock_count(self) -> int:
        with self._locks_guard:
            return len(self._lease_locks)

    @property
    def tracked_tombstone_count(self) -> int:
        with self._locks_guard:
            return len(self._tombstones)

    def _discard_execution_lock(self, sandbox_id: str) -> None:
        with self._locks_guard:
            self._execution_locks.pop(sandbox_id, None)

    @staticmethod
    def validate_workspace_path(path: str) -> str:
        if not path.startswith("/") or "\\" in path or "\x00" in path or "//" in path:
            raise ValueError("invalid workspace path")
        pure = PurePosixPath(path)
        if ".." in pure.parts or "." in pure.parts:
            raise ValueError("invalid workspace path")
        normalized = pure.as_posix()
        if normalized != path or not (
            normalized == "/workspace"
            or normalized.startswith("/workspace/")
            or normalized.startswith("/skills/")
        ):
            raise ValueError("invalid workspace path")
        return normalized

    def _managed_container(
        self,
        sandbox_id: str,
        *,
        lease_id: UUID,
        sandbox_epoch: int,
    ) -> Any:
        try:
            container = self._docker.containers.get(sandbox_id)
        except NotFound:
            raise SandboxNotFound("sandbox not found") from None
        container.reload()
        if container.labels.get(MANAGED_LABEL) != "true":
            raise SandboxNotFound("sandbox not found")
        if container.labels.get(LEASE_LABEL) != str(lease_id):
            raise SandboxLeaseMismatch("sandbox lease mismatch")
        if container.labels.get(EPOCH_LABEL) != str(sandbox_epoch):
            raise SandboxLeaseMismatch("sandbox epoch mismatch")
        return container

    @staticmethod
    def _kill_exec_children(container: Any) -> None:
        cleanup = container.exec_run(
            [
                "/bin/sh",
                "-c",
                "main=; oldest=999999999999999999; "
                "for p in $(cat /proc/1/task/1/children); do "
                '[ "$p" = "$$" ] && continue; '
                'started=$(cut -d" " -f22 "/proc/$p/stat" 2>/dev/null || echo 0); '
                '[ "$started" -lt "$oldest" ] && main=$p && oldest=$started; '
                "done; "
                "for p in $(cat /proc/1/task/1/children); do "
                '[ "$p" = "$$" ] || [ "$p" = "$main" ] || '
                'kill -KILL "$p" 2>/dev/null || true; '
                "done",
            ],
            user="65532:65532",
            demux=False,
        )
        if cleanup.exit_code != 0:
            container.remove(force=True)
            raise RuntimeError("sandbox process cleanup failed")

    def _execution_lock(self, sandbox_id: str) -> Lock:
        with self._locks_guard:
            return self._execution_locks.setdefault(sandbox_id, Lock())

    @contextmanager
    def _lease_guard(self, lease_id: UUID) -> Iterator[None]:
        with self._locks_guard:
            self._purge_expired_tombstones()
            entry = self._lease_locks.setdefault(lease_id, LeaseLockEntry(lock=RLock()))
            entry.users += 1
        entry.lock.acquire()
        try:
            yield
        finally:
            entry.lock.release()
            with self._locks_guard:
                entry.users -= 1
                if entry.users == 0:
                    self._lease_locks.pop(lease_id, None)

    def _retired_epoch(self, lease_id: UUID) -> int | None:
        with self._locks_guard:
            tombstone = self._tombstones.get(lease_id)
            if tombstone is None:
                return None
            if tombstone.expires_at <= self._monotonic_clock():
                self._tombstones.pop(lease_id, None)
                return None
            return tombstone.sandbox_epoch

    def _retire_generation(self, lease_id: UUID, *, sandbox_epoch: int) -> None:
        previous = self._retired_epoch(lease_id)
        with self._locks_guard:
            self._tombstones[lease_id] = LeaseTombstone(
                sandbox_epoch=max(
                    sandbox_epoch,
                    previous if previous is not None else 0,
                ),
                expires_at=self._monotonic_clock() + self._settings.tombstone_ttl_seconds,
            )

    def _purge_expired_tombstones(self) -> None:
        now = self._monotonic_clock()
        expired = [
            lease_id
            for lease_id, tombstone in self._tombstones.items()
            if tombstone.expires_at <= now
        ]
        for lease_id in expired:
            self._tombstones.pop(lease_id, None)

    def _find_exact_by_lease(
        self,
        lease_id: UUID,
        *,
        sandbox_epoch: int | None = None,
    ) -> list[Any]:
        candidates = self._docker.containers.list(
            all=True,
            filters={"label": [f"{MANAGED_LABEL}=true", f"{LEASE_LABEL}={lease_id}"]},
        )
        exact: list[Any] = []
        for container in candidates:
            container.reload()
            labels = container.labels
            label = labels.get(LEASE_LABEL)
            try:
                canonical = str(UUID(label)) if isinstance(label, str) else None
            except ValueError:
                canonical = None
            if (
                labels.get(MANAGED_LABEL) == "true"
                and canonical == label
                and label == str(lease_id)
            ):
                container_epoch = self._container_epoch(container)
                if sandbox_epoch is None or container_epoch == sandbox_epoch:
                    exact.append(container)
        if len(exact) > 1:
            logger.error(
                "sandbox_discovery_ambiguous",
                extra={"lease_id": str(lease_id), "count": len(exact)},
            )
            raise SandboxDiscoveryAmbiguous("multiple sandboxes matched one lease")
        return exact

    @staticmethod
    def _container_epoch(container: Any) -> int:
        label = container.labels.get(EPOCH_LABEL)
        if not isinstance(label, str) or not label.isdecimal():
            raise SandboxDiscoveryAmbiguous("sandbox generation label is invalid")
        sandbox_epoch = int(label)
        if sandbox_epoch < 1 or str(sandbox_epoch) != label:
            raise SandboxDiscoveryAmbiguous("sandbox generation label is invalid")
        return sandbox_epoch

    def _write_file_stdin(self, container: Any, *, path: str, content: bytes) -> None:
        created = self._docker.api.exec_create(
            container.id,
            ["python", "-c", UPLOAD_SCRIPT, path],
            stdin=True,
            user="65532:65532",
            workdir="/workspace",
        )
        execution_id = str(created["Id"])
        stream = self._docker.api.exec_start(execution_id, socket=True)
        try:
            try:
                duplicated_fd = os.dup(stream.fileno())
                with socket.socket(fileno=duplicated_fd) as writable_socket:
                    writable_socket.setblocking(True)
                    writable_socket.settimeout(self._settings.max_timeout_seconds)
                    writable_socket.sendall(content)
                    writable_socket.shutdown(socket.SHUT_WR)
            except (OSError, APIError):
                container.remove(force=True)
                raise RuntimeError("sandbox file upload failed") from None
        finally:
            stream.close()
        try:
            deadline = time.monotonic() + self._settings.max_timeout_seconds
            while True:
                state = self._docker.api.exec_inspect(execution_id)
                if not state["Running"]:
                    if state["ExitCode"] != 0:
                        raise RuntimeError("sandbox file upload failed")
                    return
                if time.monotonic() >= deadline:
                    container.remove(force=True)
                    raise RuntimeError("sandbox file upload timed out")
                time.sleep(0.01)
        except APIError:
            with suppress(APIError):
                container.remove(force=True)
            raise RuntimeError("sandbox file upload failed") from None

    @staticmethod
    def _read_file(container: Any, path: str, limit: int) -> bytes:
        result = container.exec_run(
            ["python", "-c", DOWNLOAD_SCRIPT, path, str(limit)],
            user="65532:65532",
            workdir="/workspace",
            demux=False,
        )
        if result.exit_code != 0:
            raise RuntimeError("sandbox internal file read failed")
        content = result.output if isinstance(result.output, bytes) else bytes(result.output)
        if len(content) > limit:
            raise RuntimeError("sandbox internal file exceeded limit")
        return content


def decode_upload(content_base64: str, *, max_bytes: int) -> bytes:
    try:
        value = base64.b64decode(content_base64, validate=True)
    except ValueError as exc:
        raise ValueError("invalid file encoding") from exc
    if len(value) > max_bytes:
        raise ValueError("file 超过 controller 限制")
    return value
