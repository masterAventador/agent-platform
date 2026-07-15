pub mod credentials;
pub mod local_executor;
pub mod remembered_login;

use serde::Serialize;

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

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let builder = tauri::Builder::default()
        .manage(std::sync::Mutex::new(
            local_executor::LocalExecutorManager::default(),
        ))
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
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

#[cfg(test)]
mod tests {
    use super::validated_url;

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
}
