pub mod browser_session;
pub mod credentials;
pub mod local_executor;
pub mod remembered_login;
pub mod sidecar_package;
pub mod social_operations_runtime;

use serde::Serialize;
use tauri::Manager;

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct PlatformCapabilities {
    platform: &'static str,
    secure_credentials: bool,
    remembered_login: bool,
}

#[derive(Debug, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
struct PlatformRuntimeConfig {
    api_base_url: Option<String>,
    web_url: Option<String>,
}

#[derive(Debug, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
enum RuntimeConfigError {
    InvalidApiBaseUrl,
    #[cfg(feature = "desktop-test")]
    InvalidWebUrl,
}

fn validated_url(value: &str, required_path: Option<&str>) -> Option<String> {
    let url = tauri::Url::parse(value).ok()?;
    let allowed_scheme = url.scheme() == "https"
        || (url.scheme() == "http"
            && matches!(url.host_str(), Some("127.0.0.1" | "localhost" | "::1")));
    let clean_authority = url.username().is_empty() && url.password().is_none();
    let clean_suffix = url.query().is_none() && url.fragment().is_none();
    let valid_path = match required_path {
        Some(path) => url.path().trim_end_matches('/') == path,
        None => true,
    };
    if allowed_scheme && clean_authority && clean_suffix && valid_path {
        Some(url.to_string().trim_end_matches('/').to_owned())
    } else {
        None
    }
}

fn optional_runtime_url(
    key: &str,
    required_path: Option<&str>,
    error: RuntimeConfigError,
) -> Result<Option<String>, RuntimeConfigError> {
    match std::env::var(key) {
        Ok(value) => validated_url(&value, required_path).map(Some).ok_or(error),
        Err(std::env::VarError::NotPresent) => Ok(None),
        Err(std::env::VarError::NotUnicode(_)) => Err(error),
    }
}

#[tauri::command]
fn platform_runtime_config() -> Result<PlatformRuntimeConfig, RuntimeConfigError> {
    #[cfg(feature = "desktop-test")]
    let web_url = optional_runtime_url(
        "AGENT_PLATFORM_DESKTOP_WEB_URL",
        None,
        RuntimeConfigError::InvalidWebUrl,
    )?;
    #[cfg(not(feature = "desktop-test"))]
    let web_url = None;

    Ok(PlatformRuntimeConfig {
        api_base_url: optional_runtime_url(
            "AGENT_PLATFORM_DESKTOP_API_BASE_URL",
            Some("/api/v1"),
            RuntimeConfigError::InvalidApiBaseUrl,
        )?,
        web_url,
    })
}

#[tauri::command]
fn platform_capabilities() -> PlatformCapabilities {
    PlatformCapabilities {
        platform: std::env::consts::OS,
        secure_credentials: true,
        remembered_login: true,
    }
}

fn sidecar_verifying_key() -> Option<[u8; 32]> {
    let encoded = option_env!("AGENT_PLATFORM_SIDECAR_VERIFYING_KEY_HEX")?;
    if encoded.len() != 64 {
        return None;
    }
    let mut key = [0_u8; 32];
    for (index, slot) in key.iter_mut().enumerate() {
        let start = index * 2;
        *slot = u8::from_str_radix(&encoded[start..start + 2], 16).ok()?;
    }
    Some(key)
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let builder = tauri::Builder::default()
        .manage(local_executor::LocalExecutorManager::default())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_fs::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_opener::init());

    #[cfg(feature = "desktop-test")]
    let builder = builder
        .plugin(tauri_plugin_wdio::init())
        .plugin(tauri_plugin_wdio_webdriver::init());

    builder
        .setup(|app| {
            let app_data_dir = app.path().app_data_dir()?;
            let social_runtime = social_operations_runtime::SocialOperationsRuntime::new(
                app_data_dir,
                sidecar_verifying_key(),
                64 * 1024 * 1024,
            )
            .map_err(|error| std::io::Error::other(format!("social runtime: {error:?}")))?;
            app.manage(std::sync::Mutex::new(social_runtime));

            #[cfg(all(debug_assertions, not(feature = "desktop-test")))]
            {
                app.handle().plugin(
                    tauri_plugin_log::Builder::default()
                        .level(log::LevelFilter::Info)
                        .build(),
                )?;
            }

            #[cfg(all(feature = "desktop-test", target_os = "macos"))]
            app.handle()
                .set_activation_policy(tauri::ActivationPolicy::Accessory)?;

            #[cfg(all(feature = "desktop-test", not(target_os = "macos")))]
            let _ = app;

            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            platform_capabilities,
            platform_runtime_config,
            credentials::credential_get,
            credentials::credential_set,
            credentials::credential_delete,
            remembered_login::remembered_login_get,
            remembered_login::remembered_login_set,
            remembered_login::remembered_login_delete,
            local_executor::local_executor_start,
            local_executor::local_executor_invoke,
            local_executor::local_executor_status,
            local_executor::local_executor_stop,
            social_operations_runtime::social_sidecar_install,
            social_operations_runtime::social_sidecar_download,
            social_operations_runtime::social_account_prepare,
            social_operations_runtime::social_account_login_signal,
            social_operations_runtime::social_account_store_cookies,
            social_operations_runtime::social_account_has_cookies,
            social_operations_runtime::social_account_start,
            social_operations_runtime::social_account_invoke,
            social_operations_runtime::social_account_logout,
            social_operations_runtime::social_account_emergency_stop,
            social_operations_runtime::social_executor_take_safe_diagnostics,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

#[cfg(test)]
mod tests {
    use super::validated_url;

    const SOCIAL_COMMANDS: [&str; 11] = [
        "social_sidecar_install",
        "social_sidecar_download",
        "social_account_prepare",
        "social_account_login_signal",
        "social_account_store_cookies",
        "social_account_has_cookies",
        "social_account_start",
        "social_account_invoke",
        "social_account_logout",
        "social_account_emergency_stop",
        "social_executor_take_safe_diagnostics",
    ];

    #[test]
    fn runtime_urls_allow_https_and_loopback_http_only() {
        assert_eq!(
            validated_url("http://127.0.0.1:18000/api/v1", Some("/api/v1")),
            Some("http://127.0.0.1:18000/api/v1".to_owned())
        );
        assert_eq!(
            validated_url("https://platform.example.com/api/v1/", Some("/api/v1")),
            Some("https://platform.example.com/api/v1".to_owned())
        );
        assert_eq!(
            validated_url("http://platform.example.com/api/v1", Some("/api/v1")),
            None
        );
        assert_eq!(
            validated_url("https://platform.example.com/other", Some("/api/v1")),
            None
        );
        assert_eq!(
            validated_url("https://user@example.com/api/v1", Some("/api/v1")),
            None
        );
    }

    #[test]
    fn registered_social_commands_are_authorized_by_desktop_capabilities() {
        let command_permissions = include_str!("../permissions/app-commands.toml");
        let default_capability = include_str!("../capabilities/default.json");
        let test_capability = include_str!("../tauri.test.conf.json");
        let registered_commands = include_str!("lib.rs");

        assert!(default_capability.contains("allow-social-operations"));
        assert!(test_capability.contains("allow-social-operations"));
        for command in SOCIAL_COMMANDS {
            assert!(
                registered_commands.contains(&format!("social_operations_runtime::{command}")),
                "{command} must be registered"
            );
            assert!(
                command_permissions.contains(&format!("\"{command}\"")),
                "{command} must be authorized"
            );
        }
    }
}
