from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    """应用运行配置。"""

    model_config = SettingsConfigDict(env_prefix="AGENT_PLATFORM_", extra="ignore")

    database_url: str = (
        "postgresql+asyncpg://agent_platform:agent-platform-local-postgres"
        "@127.0.0.1:5432/agent_platform"
    )
    redis_url: str = "redis://:agent-platform-local-redis@127.0.0.1:6379/0"
    run_queue_stream_name: str = "agent-platform:runs"
    run_queue_group_name: str = "agent-platform-workers"
    run_queue_dead_letter_stream_name: str = "agent-platform:runs:dlq"
    queue_pending_min_idle_ms: int = Field(default=1_000, ge=1)
    queue_max_delivery_attempts: int = Field(default=5, ge=1, le=100)
    worker_retry_backoff_seconds: float = Field(default=1.0, ge=0, le=60)
    runtime_lease_seconds: int = Field(default=30, ge=3, le=300)
    runtime_heartbeat_seconds: float = Field(default=10.0, ge=1, le=100)
    runtime_cancel_poll_initial_seconds: float = Field(default=0.25, ge=0.01, le=5)
    runtime_cancel_poll_max_seconds: float = Field(default=2.0, ge=0.01, le=10)
    ragflow_url: str = "http://127.0.0.1:19380"
    ragflow_api_key: str = ""
    minio_endpoint: str = "127.0.0.1:9000"
    minio_access_key: str = "agent_platform"
    minio_secret_key: str = "agent-platform-local-minio"
    minio_secure: bool = False
    skill_storage_bucket: str = "agent-platform-skills"
    local_credentials_file: str | None = None
    local_credentials_repository_root: str | None = None
    sandbox_provider: Literal["local-controller"] = "local-controller"
    sandbox_controller_url: str = "http://sandbox-controller:8090"
    sandbox_controller_secret: SecretStr = SecretStr("")
    sandbox_controller_request_timeout_seconds: float = Field(default=130.0, ge=125, le=3_600)
    sandbox_ttl_seconds: int = Field(default=3_600, ge=60, le=86_400)
    sandbox_janitor_interval_seconds: float = Field(default=30.0, ge=1, le=3_600)
    sandbox_janitor_batch_size: int = Field(default=100, ge=1, le=1_000)
    otel_enabled: bool = False
    otel_service_name: str = "agent-platform-api"
    otel_environment: str = "development"
    otel_otlp_endpoint: str = "http://127.0.0.1:4317"
    otel_otlp_insecure: bool = True
    require_email_verification: bool = False
    auth_cookie_name: str = "agent_platform_session"
    auth_cookie_secure: bool = False
    auth_session_ttl_seconds: int = 60 * 60 * 24 * 7
    auth_register_limit_per_minute: int = 5
    auth_login_limit_per_minute: int = 10

    @model_validator(mode="after")
    def validate_runtime_heartbeat(self) -> "AppSettings":
        if self.runtime_heartbeat_seconds >= self.runtime_lease_seconds:
            raise ValueError("runtime heartbeat must be shorter than runtime lease")
        if self.runtime_cancel_poll_initial_seconds > self.runtime_cancel_poll_max_seconds:
            raise ValueError("runtime cancel poll initial delay must not exceed maximum")
        return self
