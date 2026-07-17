import pytest
from pydantic import ValidationError

from agent_platform.config import AppSettings
from agent_platform.platform.audit.hashing import INSECURE_DEV_AUDIT_HMAC_KEY
from agent_platform.platform.model_gateway.credentials import (
    INSECURE_DEV_MODEL_GATEWAY_KEY_SECRET,
)


def test_tauri_cors_defaults_use_exact_origins() -> None:
    settings = AppSettings()

    assert settings.cors_allowed_origins == (
        "tauri://localhost",
        "http://tauri.localhost",
    )
    assert "*" not in settings.cors_allowed_origins


@pytest.mark.parametrize(
    "overrides",
    [
        {"auth_cookie_secure": False, "auth_cookie_same_site": "none"},
        {
            "app_environment": "production",
            "auth_cookie_secure": False,
            "auth_cookie_same_site": "lax",
        },
        {
            "app_environment": "production",
            "auth_cookie_secure": True,
            "auth_cookie_same_site": "lax",
        },
        {"cors_allowed_origins": ("*",)},
    ],
)
def test_auth_transport_rejects_unsafe_cross_origin_settings(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        AppSettings(**overrides)


def test_production_auth_transport_accepts_secure_cross_site_cookie() -> None:
    settings = AppSettings(
        app_environment="production",
        auth_cookie_secure=True,
        auth_cookie_same_site="none",
        audit_hmac_key="an-explicit-production-audit-hmac-key-0001",
        model_gateway_key_secret="an-explicit-production-gateway-secret-01",
    )

    assert settings.auth_cookie_secure is True
    assert settings.auth_cookie_same_site == "none"


def test_dev_account_token_channel_defaults_open_but_forced_closed_in_production() -> None:
    assert AppSettings().expose_dev_account_tokens is True

    forced = AppSettings(
        app_environment="production",
        auth_cookie_secure=True,
        auth_cookie_same_site="none",
        audit_hmac_key="an-explicit-production-audit-hmac-key-0001",
        model_gateway_key_secret="an-explicit-production-gateway-secret-01",
        expose_dev_account_tokens=True,
    )
    assert forced.expose_dev_account_tokens is False

    staging = AppSettings(
        app_environment="staging",
        audit_hmac_key="an-explicit-staging-audit-hmac-key-000001",
        model_gateway_key_secret="an-explicit-staging-gateway-secret-0001",
        expose_dev_account_tokens=True,
    )
    assert staging.expose_dev_account_tokens is False


@pytest.mark.parametrize(
    "audit_hmac_key",
    [
        "",
        "too-short-key",
        INSECURE_DEV_AUDIT_HMAC_KEY,
    ],
)
def test_production_requires_explicit_strong_audit_hmac_key(audit_hmac_key: str) -> None:
    with pytest.raises(ValidationError):
        AppSettings(
            app_environment="production",
            auth_cookie_secure=True,
            auth_cookie_same_site="none",
            audit_hmac_key=audit_hmac_key,
        )


def test_staging_requires_explicit_audit_hmac_key() -> None:
    with pytest.raises(ValidationError):
        AppSettings(app_environment="staging")


def test_non_production_defaults_to_insecure_dev_audit_hmac_key() -> None:
    settings = AppSettings()

    assert settings.audit_hmac_key.get_secret_value() == INSECURE_DEV_AUDIT_HMAC_KEY
    assert INSECURE_DEV_AUDIT_HMAC_KEY not in repr(settings.audit_hmac_key)


def test_explicit_audit_hmac_key_is_preserved_in_development() -> None:
    settings = AppSettings(audit_hmac_key="explicit-development-audit-key-0001")

    assert settings.audit_hmac_key.get_secret_value() == "explicit-development-audit-key-0001"


def test_tencent_cos_requires_region_and_credentials() -> None:
    with pytest.raises(ValidationError):
        AppSettings(artifact_storage_provider="tencent-cos")


def test_artifact_storage_heartbeat_must_be_shorter_than_lease() -> None:
    with pytest.raises(ValidationError):
        AppSettings(
            artifact_storage_operation_lease_seconds=5,
            artifact_storage_operation_heartbeat_seconds=5,
        )


def test_artifact_tombstone_observation_must_cover_provider_timeout_and_rescan() -> None:
    with pytest.raises(ValidationError):
        AppSettings(
            artifact_storage_request_timeout_seconds=30,
            artifact_storage_tombstone_observation_seconds=34,
            artifact_storage_tombstone_rescan_seconds=5,
        )


def test_unbound_file_cleanup_uses_a_bounded_default_interval() -> None:
    settings = AppSettings()

    assert settings.artifact_unbound_file_cleanup_interval_seconds == 300

    with pytest.raises(ValidationError):
        AppSettings(artifact_unbound_file_cleanup_interval_seconds=4)


def test_dev_environments_fall_back_to_the_published_weak_gateway_key_secret() -> None:
    assert (
        AppSettings().model_gateway_key_secret.get_secret_value()
        == INSECURE_DEV_MODEL_GATEWAY_KEY_SECRET
    )


@pytest.mark.parametrize(
    "model_gateway_key_secret",
    [
        "",
        "too-short-secret",
        INSECURE_DEV_MODEL_GATEWAY_KEY_SECRET,
    ],
)
def test_production_requires_an_explicit_strong_gateway_key_secret(
    model_gateway_key_secret: str,
) -> None:
    """派生密钥泄漏等于所有租户 Key 可被签发：staging/production 必须显式强密钥。"""
    with pytest.raises(ValidationError):
        AppSettings(
            app_environment="production",
            auth_cookie_secure=True,
            auth_cookie_same_site="none",
            audit_hmac_key="an-explicit-production-audit-hmac-key-0001",
            model_gateway_key_secret=model_gateway_key_secret,
        )


def test_staging_requires_an_explicit_strong_gateway_key_secret() -> None:
    with pytest.raises(ValidationError):
        AppSettings(
            app_environment="staging",
            audit_hmac_key="an-explicit-staging-audit-hmac-key-000001",
            model_gateway_key_secret=INSECURE_DEV_MODEL_GATEWAY_KEY_SECRET,
        )


def test_gateway_key_secret_never_appears_in_settings_repr() -> None:
    settings = AppSettings(model_gateway_key_secret="a-strong-model-gateway-key-secret-000001")

    assert "a-strong-model-gateway-key-secret-000001" not in repr(settings)
    assert "a-strong-model-gateway-key-secret-000001" not in str(settings)
