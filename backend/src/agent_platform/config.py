from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from agent_platform.platform.models import (
    DEFAULT_MODEL_ALIASES,
    validate_gateway_alias,
)


class AppSettings(BaseSettings):
    """应用运行配置。"""

    model_config = SettingsConfigDict(env_prefix="AGENT_PLATFORM_", extra="ignore")

    app_environment: Literal["local", "development", "test", "staging", "production"] = (
        "development"
    )
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
    llm_gateway_url: SecretStr = SecretStr("http://127.0.0.1:4000/v1")
    llm_gateway_api_key: SecretStr = SecretStr("")
    llm_gateway_request_timeout_seconds: float = Field(default=120, gt=0, le=3_600)
    llm_gateway_readiness_timeout_seconds: float = Field(default=5, gt=0, le=30)
    llm_gateway_max_retries: int = Field(default=2, ge=0, le=10)
    llm_gateway_allowed_aliases: frozenset[str] = DEFAULT_MODEL_ALIASES
    ragflow_url: str = "http://127.0.0.1:19380"
    ragflow_api_key: str = ""
    minio_endpoint: str = "127.0.0.1:9000"
    minio_access_key: str = "agent_platform"
    minio_secret_key: str = "agent-platform-local-minio"
    minio_secure: bool = False
    skill_storage_bucket: str = "agent-platform-skills"
    artifact_storage_bucket: str = "agent-platform-artifacts"
    artifact_storage_provider: Literal["minio", "tencent-cos"] = "minio"
    artifact_storage_operation_lease_seconds: int = Field(default=300, ge=3, le=3_600)
    artifact_storage_operation_heartbeat_seconds: float = Field(default=30.0, ge=1, le=300)
    artifact_storage_request_timeout_seconds: float = Field(default=30.0, ge=1, le=120)
    artifact_storage_tombstone_observation_seconds: int = Field(default=45, ge=1, le=600)
    artifact_storage_tombstone_rescan_seconds: int = Field(default=5, ge=1, le=60)
    artifact_unbound_file_ttl_seconds: int = Field(default=86_400, ge=60, le=2_592_000)
    artifact_unbound_file_cleanup_interval_seconds: int = Field(default=300, ge=5, le=86_400)
    audit_retention_days: int = Field(default=180, ge=1, le=3_650)
    audit_retention_sweep_interval_seconds: int = Field(default=3_600, ge=60, le=86_400)
    audit_retention_sweep_batch_limit: int = Field(default=1_000, ge=1, le=10_000)
    cos_region: str | None = None
    cos_secret_id: SecretStr = SecretStr("")
    cos_secret_key: SecretStr = SecretStr("")
    cos_token: SecretStr = SecretStr("")
    cos_scheme: Literal["http", "https"] = "https"
    local_credentials_file: str | None = None
    local_credentials_repository_root: str | None = None
    mcp_connection_timeout_seconds: float = Field(default=10.0, gt=0, le=120)
    mcp_stdio_allowed_commands: list[str] = Field(default_factory=list)
    tool_invocation_timeout_seconds: float = Field(default=30.0, gt=0, le=600)
    tool_invocation_max_read_retries: int = Field(default=2, ge=0, le=10)
    tool_circuit_failure_threshold: int = Field(default=5, ge=1, le=100)
    tool_circuit_cooldown_seconds: float = Field(default=30.0, gt=0, le=3_600)
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
    cors_allowed_origins: tuple[str, ...] = (
        "tauri://localhost",
        "http://tauri.localhost",
    )
    auth_cookie_name: str = "agent_platform_session"
    auth_cookie_secure: bool = False
    auth_cookie_same_site: Literal["lax", "strict", "none"] = "lax"
    auth_session_ttl_seconds: int = 60 * 60 * 24 * 7
    auth_register_limit_per_minute: int = 5
    auth_login_limit_per_minute: int = 10

    @field_validator("llm_gateway_allowed_aliases")
    @classmethod
    def validate_llm_gateway_allowed_aliases(cls, aliases: frozenset[str]) -> frozenset[str]:
        if not aliases:
            raise ValueError("model gateway alias allowlist must not be empty")
        for alias in aliases:
            try:
                validate_gateway_alias(alias)
            except ValueError:
                raise ValueError(
                    "model gateway alias allowlist contains an invalid alias"
                ) from None
        return aliases

    @model_validator(mode="after")
    def validate_runtime_heartbeat(self) -> "AppSettings":
        if self.runtime_heartbeat_seconds >= self.runtime_lease_seconds:
            raise ValueError("runtime heartbeat must be shorter than runtime lease")
        if self.runtime_cancel_poll_initial_seconds > self.runtime_cancel_poll_max_seconds:
            raise ValueError("runtime cancel poll initial delay must not exceed maximum")
        if (
            self.artifact_storage_operation_heartbeat_seconds
            >= self.artifact_storage_operation_lease_seconds
        ):
            raise ValueError("artifact storage heartbeat must be shorter than operation lease")
        if self.artifact_storage_tombstone_observation_seconds < (
            self.artifact_storage_request_timeout_seconds
            + self.artifact_storage_tombstone_rescan_seconds
        ):
            raise ValueError("artifact tombstone observation must cover provider timeout")
        if self.auth_cookie_same_site == "none" and not self.auth_cookie_secure:
            raise ValueError("SameSite=None auth cookies must also be Secure")
        if self.app_environment == "production" and (
            not self.auth_cookie_secure or self.auth_cookie_same_site != "none"
        ):
            raise ValueError("production auth cookies must be Secure and SameSite=None for Tauri")
        if "*" in self.cors_allowed_origins:
            raise ValueError("credentialed CORS must use exact origins")
        if self.artifact_storage_provider == "tencent-cos" and (
            not self.cos_region
            or not self.cos_secret_id.get_secret_value()
            or not self.cos_secret_key.get_secret_value()
        ):
            raise ValueError("Tencent COS requires region and credentials")
        return self
