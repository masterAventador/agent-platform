from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from agent_platform.platform.audit.hashing import (
    INSECURE_DEV_AUDIT_HMAC_KEY,
)
from agent_platform.platform.models import (
    DEFAULT_MODEL_ALIASES,
    validate_gateway_alias,
)

_AUDIT_HMAC_KEY_MIN_LENGTH = 32


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
    # 审计哈希链 HMAC 密钥：只经环境变量注入，绝不落数据库、绝不进日志。
    audit_hmac_key: SecretStr = SecretStr("")
    audit_retention_days: int = Field(default=180, ge=1, le=3_650)
    # C13 审批中心：pending 审批的超时时长与过期清扫（配置驱动）
    approval_pending_timeout_seconds: int = Field(default=86_400, ge=60, le=2_592_000)
    approval_expiry_sweep_interval_seconds: int = Field(default=60, ge=5, le=86_400)
    approval_expiry_sweep_batch_limit: int = Field(default=500, ge=1, le=10_000)
    audit_retention_sweep_interval_seconds: int = Field(default=3_600, ge=60, le=86_400)
    audit_retention_sweep_batch_limit: int = Field(default=1_000, ge=1, le=10_000)
    # C12 定时任务调度：循环开关、节拍、每跳批量与执行历史保留
    scheduler_enabled: bool = True
    scheduler_tick_interval_seconds: int = Field(default=30, ge=1, le=3_600)
    scheduler_tick_batch_limit: int = Field(default=200, ge=1, le=10_000)
    scheduled_task_execution_retention_days: int = Field(default=90, ge=1, le=3_650)
    scheduled_task_execution_purge_interval_seconds: int = Field(
        default=3_600, ge=60, le=86_400
    )
    scheduled_task_execution_purge_batch_limit: int = Field(default=1_000, ge=1, le=10_000)
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
    auth_reset_request_limit_per_minute: int = 5
    account_invitation_ttl_seconds: int = Field(default=60 * 60 * 24 * 7, ge=300, le=2_592_000)
    account_email_verification_ttl_seconds: int = Field(
        default=60 * 60 * 24, ge=300, le=2_592_000
    )
    account_reset_token_ttl_seconds: int = Field(default=60 * 60, ge=300, le=86_400)
    # Demo/开发受控通道：找回密码/邮箱验证不真发信时，允许通过专用开发端点读取
    # token 明文。生产/预发必须关闭，公共请求端点始终保持防用户枚举。
    expose_dev_account_tokens: bool = True
    installed_capabilities: tuple[str, ...] = ("social-operations",)
    # video-studio 素材直传桶：配置后（连同 cos_region/cos_secret_id/cos_secret_key）
    # 生产装配注入真实腾讯 CAM/STS 签发器；缺省时素材上传凭证端点保持 503 失败关闭。
    video_material_cos_bucket: str | None = None
    # 租户级 STS 签发频控（每分钟）；防止对真实腾讯 STS 的无界调用成本。
    video_sts_issue_limit_per_minute: int = Field(default=30, ge=1, le=10_000)
    # 素材库回收清扫：过期草稿止血 + cleanup_required 对象回收（M-2）。
    video_media_maintenance_interval_seconds: float = Field(default=300.0, ge=1, le=86_400)
    video_media_maintenance_batch_limit: int = Field(default=100, ge=1, le=1_000)
    social_operations_offline_after_seconds: int = Field(default=90, ge=5, le=3_600)
    social_operations_claim_lease_seconds: int = Field(default=60, ge=5, le=3_600)
    social_operations_state_path: str | None = None

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
        if self.app_environment in ("staging", "production"):
            # 账号 token 开发通道属安全边界：staging/production 一律强制关闭
            # （fail-closed），明文重置/验证 token 永不通过该通道暴露或落库。
            self.expose_dev_account_tokens = False
        audit_key = self.audit_hmac_key.get_secret_value()
        if self.app_environment in ("staging", "production"):
            if (
                len(audit_key) < _AUDIT_HMAC_KEY_MIN_LENGTH
                or audit_key == INSECURE_DEV_AUDIT_HMAC_KEY
            ):
                raise ValueError(
                    "staging/production requires an explicit audit HMAC key of at least "
                    f"{_AUDIT_HMAC_KEY_MIN_LENGTH} characters (dev key is rejected)"
                )
        elif not audit_key:
            # 开发/测试环境允许回退到公开弱密钥，保证本机开箱可用；仍然是 HMAC，
            # 不存在无密钥哈希路径。
            self.audit_hmac_key = SecretStr(INSECURE_DEV_AUDIT_HMAC_KEY)
        if self.artifact_storage_provider == "tencent-cos" and (
            not self.cos_region
            or not self.cos_secret_id.get_secret_value()
            or not self.cos_secret_key.get_secret_value()
        ):
            raise ValueError("Tencent COS requires region and credentials")
        return self
