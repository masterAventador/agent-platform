use serde::Serialize;
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use tauri::{AppHandle, Manager};

const FILE_NAME: &str = "remembered-login.json";

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum RememberedLoginError {
    StoreUnavailable,
}

fn read(root: &Path) -> Result<Option<String>, RememberedLoginError> {
    match fs::read_to_string(root.join(FILE_NAME)) {
        Ok(value) => Ok(Some(value)),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(_) => Err(RememberedLoginError::StoreUnavailable),
    }
}

fn open_private_file(path: &Path) -> Result<std::fs::File, RememberedLoginError> {
    let mut options = OpenOptions::new();
    options.create(true).truncate(true).write(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    options
        .open(path)
        .map_err(|_| RememberedLoginError::StoreUnavailable)
}

fn write(root: &Path, value: &str) -> Result<(), RememberedLoginError> {
    fs::create_dir_all(root).map_err(|_| RememberedLoginError::StoreUnavailable)?;
    let target = root.join(FILE_NAME);
    let temporary = root.join(format!("{FILE_NAME}.{}.tmp", std::process::id()));
    let mut file = open_private_file(&temporary)?;
    file.write_all(value.as_bytes())
        .and_then(|()| file.sync_all())
        .map_err(|_| RememberedLoginError::StoreUnavailable)?;
    #[cfg(windows)]
    if target.exists() {
        fs::remove_file(&target).map_err(|_| RememberedLoginError::StoreUnavailable)?;
    }
    fs::rename(&temporary, target).map_err(|_| RememberedLoginError::StoreUnavailable)
}

fn delete(root: &Path) -> Result<(), RememberedLoginError> {
    match fs::remove_file(root.join(FILE_NAME)) {
        Ok(()) => Ok(()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(_) => Err(RememberedLoginError::StoreUnavailable),
    }
}

fn app_data_root(app: &AppHandle) -> Result<PathBuf, RememberedLoginError> {
    app.path()
        .app_data_dir()
        .map_err(|_| RememberedLoginError::StoreUnavailable)
}

#[tauri::command]
pub async fn remembered_login_get(app: AppHandle) -> Result<Option<String>, RememberedLoginError> {
    let root = app_data_root(&app)?;
    tauri::async_runtime::spawn_blocking(move || read(&root))
        .await
        .map_err(|_| RememberedLoginError::StoreUnavailable)?
}

#[tauri::command]
pub async fn remembered_login_set(
    app: AppHandle,
    value: String,
) -> Result<(), RememberedLoginError> {
    let root = app_data_root(&app)?;
    tauri::async_runtime::spawn_blocking(move || write(&root, &value))
        .await
        .map_err(|_| RememberedLoginError::StoreUnavailable)?
}

#[tauri::command]
pub async fn remembered_login_delete(app: AppHandle) -> Result<(), RememberedLoginError> {
    let root = app_data_root(&app)?;
    tauri::async_runtime::spawn_blocking(move || delete(&root))
        .await
        .map_err(|_| RememberedLoginError::StoreUnavailable)?
}

#[cfg(test)]
mod tests {
    use super::{delete, read, write, FILE_NAME};
    use std::fs;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn temporary_root() -> std::path::PathBuf {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("clock must be after epoch")
            .as_nanos();
        std::env::temp_dir().join(format!(
            "agent-platform-remembered-login-{}-{nonce}",
            std::process::id()
        ))
    }

    #[test]
    fn app_private_file_round_trips_and_deletes() {
        let root = temporary_root();
        let value = r#"{"email":"demo@example.com","password":"demo-password"}"#;

        assert_eq!(read(&root), Ok(None));
        assert_eq!(write(&root, value), Ok(()));
        assert_eq!(read(&root), Ok(Some(value.to_owned())));
        assert_eq!(delete(&root), Ok(()));
        assert_eq!(delete(&root), Ok(()));
        assert_eq!(read(&root), Ok(None));

        let _ = fs::remove_dir_all(root);
    }

    #[cfg(unix)]
    #[test]
    fn app_private_file_is_owner_only() {
        use std::os::unix::fs::PermissionsExt;

        let root = temporary_root();
        write(&root, "demo").expect("write should succeed");
        let mode = fs::metadata(root.join(FILE_NAME))
            .expect("file should exist")
            .permissions()
            .mode()
            & 0o777;

        assert_eq!(mode, 0o600);
        let _ = fs::remove_dir_all(root);
    }
}
