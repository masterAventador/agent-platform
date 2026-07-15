import pytest
from pydantic import ValidationError

from agent_platform.config import AppSettings


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
    )

    assert settings.auth_cookie_secure is True
    assert settings.auth_cookie_same_site == "none"


def test_tencent_cos_requires_region_and_credentials() -> None:
    with pytest.raises(ValidationError):
        AppSettings(artifact_storage_provider="tencent-cos")
