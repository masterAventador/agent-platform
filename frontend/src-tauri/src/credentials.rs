use keyring::{Entry, Error as KeyringError};
use serde::Serialize;

const CREDENTIAL_SERVICE: &str = "com.masteraventador.agent-platform";

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum CredentialError {
    InvalidKey,
    StoreUnavailable,
}

pub fn validate_credential_key(key: &str) -> Result<(), CredentialError> {
    let valid_length = !key.is_empty() && key.len() <= 128;
    let valid_characters = key
        .bytes()
        .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b'-' | b'/'));
    let safe_segments = !key.starts_with('/')
        && !key.ends_with('/')
        && !key.contains("//")
        && key
            .split('/')
            .all(|segment| segment != "." && segment != "..");
    if valid_length && valid_characters && safe_segments {
        Ok(())
    } else {
        Err(CredentialError::InvalidKey)
    }
}

fn entry(key: &str) -> Result<Entry, CredentialError> {
    validate_credential_key(key)?;
    Entry::new(CREDENTIAL_SERVICE, key).map_err(|_| CredentialError::StoreUnavailable)
}

fn get(key: &str) -> Result<Option<String>, CredentialError> {
    match entry(key)?.get_password() {
        Ok(secret) => Ok(Some(secret)),
        Err(KeyringError::NoEntry) => Ok(None),
        Err(_) => Err(CredentialError::StoreUnavailable),
    }
}

fn set(key: &str, secret: &str) -> Result<(), CredentialError> {
    entry(key)?
        .set_password(secret)
        .map_err(|_| CredentialError::StoreUnavailable)
}

fn delete(key: &str) -> Result<(), CredentialError> {
    match entry(key)?.delete_credential() {
        Ok(()) | Err(KeyringError::NoEntry) => Ok(()),
        Err(_) => Err(CredentialError::StoreUnavailable),
    }
}

#[tauri::command]
pub async fn credential_get(key: String) -> Result<Option<String>, CredentialError> {
    tauri::async_runtime::spawn_blocking(move || get(&key))
        .await
        .map_err(|_| CredentialError::StoreUnavailable)?
}

#[tauri::command]
pub async fn credential_set(key: String, secret: String) -> Result<(), CredentialError> {
    tauri::async_runtime::spawn_blocking(move || set(&key, &secret))
        .await
        .map_err(|_| CredentialError::StoreUnavailable)?
}

#[tauri::command]
pub async fn credential_delete(key: String) -> Result<(), CredentialError> {
    tauri::async_runtime::spawn_blocking(move || delete(&key))
        .await
        .map_err(|_| CredentialError::StoreUnavailable)?
}
