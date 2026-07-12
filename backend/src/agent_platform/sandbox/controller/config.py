from __future__ import annotations

import os
import re
from dataclasses import dataclass

_DIGEST_IMAGE = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")
MAX_FILE_HARD_LIMIT = 16 * 1024 * 1024


@dataclass(frozen=True)
class ControllerSettings:
    bearer_secret: str
    sandbox_image: str
    platform: str = "linux/arm64"
    max_file_bytes: int = 8 * 1024 * 1024
    max_output_bytes: int = 1024 * 1024
    max_batch_bytes: int = 16 * 1024 * 1024
    max_timeout_seconds: int = 120
    tombstone_ttl_seconds: int = 3_600
    workspace_bytes: int = 64 * 1024 * 1024
    memory_limit: str = "256m"
    nano_cpus: int = 500_000_000
    pids_limit: int = 64

    def __post_init__(self) -> None:
        if len(self.bearer_secret) < 16:
            raise ValueError("controller bearer secret 必须至少 16 字符")
        if _DIGEST_IMAGE.fullmatch(self.sandbox_image) is None:
            raise ValueError("sandbox image 必须使用固定 sha256 digest")
        if self.platform != "linux/arm64":
            raise ValueError("本机 controller 只允许 linux/arm64 sandbox")
        for value in (
            self.max_file_bytes,
            self.max_output_bytes,
            self.max_batch_bytes,
            self.max_timeout_seconds,
            self.tombstone_ttl_seconds,
            self.workspace_bytes,
            self.nano_cpus,
            self.pids_limit,
        ):
            if value <= 0:
                raise ValueError("controller resource limits 必须大于零")
        if self.max_file_bytes > MAX_FILE_HARD_LIMIT:
            raise ValueError("controller 单文件限制超过安全上限")
        if self.max_file_bytes > self.max_batch_bytes:
            raise ValueError("controller 单文件限制不能超过批量限制")
        if self.tombstone_ttl_seconds < self.max_timeout_seconds:
            raise ValueError("controller tombstone TTL 不能短于最大请求时长")

    @property
    def max_request_bytes(self) -> int:
        # base64 最坏膨胀 + JSON 元数据余量；ASGI 层在解析前拒绝超限请求。
        return ((self.max_batch_bytes + 2) // 3) * 4 + 64 * 1024

    @classmethod
    def from_env(cls) -> ControllerSettings:
        return cls(
            bearer_secret=os.environ.get("SANDBOX_CONTROLLER_BEARER_SECRET", ""),
            sandbox_image=os.environ.get("SANDBOX_CONTROLLER_IMAGE", ""),
        )
