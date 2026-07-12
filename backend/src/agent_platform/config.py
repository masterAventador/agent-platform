from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    """应用运行配置。"""

    model_config = SettingsConfigDict(env_prefix="AGENT_PLATFORM_", extra="ignore")

    database_url: str = (
        "postgresql+asyncpg://agent_platform:agent-platform-local-postgres"
        "@127.0.0.1:5432/agent_platform"
    )
    redis_url: str = "redis://:agent-platform-local-redis@127.0.0.1:6379/0"
    ragflow_url: str = "http://127.0.0.1:19380"
    ragflow_api_key: str = ""
    minio_endpoint: str = "127.0.0.1:9000"
    minio_access_key: str = "agent_platform"
    minio_secret_key: str = "agent-platform-local-minio"
    minio_secure: bool = False
    skill_storage_bucket: str = "agent-platform-skills"
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
