import pytest
from pydantic import ValidationError

from agent_platform.config import AppSettings
from agent_platform.platform.audit.hashing import INSECURE_DEV_AUDIT_HMAC_KEY


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
    )

    assert settings.auth_cookie_secure is True
    assert settings.auth_cookie_same_site == "none"


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
