use ed25519_dalek::{Signature, VerifyingKey};
use regex::Regex;
use reqwest::blocking::Client;
use std::fs::{self, OpenOptions};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::sync::OnceLock;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SidecarPackageError {
    InvalidRoot,
    InvalidVersion,
    UntrustedDownloadUrl,
    DownloadFailed,
    PackageTooLarge,
    SignatureInvalid,
    IoUnavailable,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CrashRecoveryAction {
    Restart,
    Stop,
}

#[derive(Debug, Clone)]
pub struct CrashRecoveryPolicy {
    max_restarts: usize,
    restart_attempts: usize,
}

impl CrashRecoveryPolicy {
    pub fn new(max_restarts: usize) -> Self {
        Self {
            max_restarts,
            restart_attempts: 0,
        }
    }

    pub fn on_unexpected_exit(&mut self) -> CrashRecoveryAction {
        if self.restart_attempts >= self.max_restarts {
            return CrashRecoveryAction::Stop;
        }
        self.restart_attempts += 1;
        CrashRecoveryAction::Restart
    }

    pub fn on_clean_stop(&self) -> CrashRecoveryAction {
        CrashRecoveryAction::Stop
    }

    pub fn reset_after_stable_start(&mut self) {
        self.restart_attempts = 0;
    }
}

pub struct TrustedSidecarInstaller {
    root: PathBuf,
    verifying_key: VerifyingKey,
    max_package_bytes: usize,
    client: Client,
}

impl TrustedSidecarInstaller {
    pub fn new(
        root: impl AsRef<Path>,
        verifying_key: [u8; 32],
        max_package_bytes: usize,
    ) -> Result<Self, SidecarPackageError> {
        let root = root.as_ref();
        if root.as_os_str().is_empty() || max_package_bytes == 0 {
            return Err(SidecarPackageError::InvalidRoot);
        }
        if root.exists()
            && root
                .symlink_metadata()
                .map_err(|_| SidecarPackageError::InvalidRoot)?
                .file_type()
                .is_symlink()
        {
            return Err(SidecarPackageError::InvalidRoot);
        }
        let verifying_key = VerifyingKey::from_bytes(&verifying_key)
            .map_err(|_| SidecarPackageError::SignatureInvalid)?;
        let client = Client::builder()
            .https_only(false)
            .build()
            .map_err(|_| SidecarPackageError::DownloadFailed)?;
        Ok(Self {
            root: root.to_path_buf(),
            verifying_key,
            max_package_bytes,
            client,
        })
    }

    pub fn download_and_install(
        &self,
        download_url: &str,
        version: &str,
        signature: &[u8],
    ) -> Result<PathBuf, SidecarPackageError> {
        let url = reqwest::Url::parse(download_url)
            .map_err(|_| SidecarPackageError::UntrustedDownloadUrl)?;
        let trusted = url.scheme() == "https"
            || (url.scheme() == "http"
                && matches!(url.host_str(), Some("127.0.0.1" | "localhost" | "::1")));
        if !trusted || !url.username().is_empty() || url.password().is_some() {
            return Err(SidecarPackageError::UntrustedDownloadUrl);
        }
        validate_version(version)?;

        let mut response = self
            .client
            .get(url)
            .send()
            .map_err(|_| SidecarPackageError::DownloadFailed)?;
        if !response.status().is_success() {
            return Err(SidecarPackageError::DownloadFailed);
        }
        if response
            .content_length()
            .is_some_and(|length| length > self.max_package_bytes as u64)
        {
            return Err(SidecarPackageError::PackageTooLarge);
        }
        let mut package = Vec::new();
        response
            .by_ref()
            .take(self.max_package_bytes as u64 + 1)
            .read_to_end(&mut package)
            .map_err(|_| SidecarPackageError::DownloadFailed)?;
        if package.len() > self.max_package_bytes {
            return Err(SidecarPackageError::PackageTooLarge);
        }
        self.install_bytes(version, &package, signature)
    }

    pub fn install_bytes(
        &self,
        version: &str,
        package: &[u8],
        signature: &[u8],
    ) -> Result<PathBuf, SidecarPackageError> {
        validate_version(version)?;
        if package.is_empty() || package.len() > self.max_package_bytes {
            return Err(SidecarPackageError::PackageTooLarge);
        }
        let signature =
            Signature::from_slice(signature).map_err(|_| SidecarPackageError::SignatureInvalid)?;
        self.verifying_key
            .verify_strict(package, &signature)
            .map_err(|_| SidecarPackageError::SignatureInvalid)?;

        let sidecars = create_private_child(&self.root, "sidecars")?;
        let version_dir = create_private_child(&sidecars, version)?;

        let executable = version_dir.join("social-operations-sidecar");
        let staging = version_dir.join(".social-operations-sidecar.staging");
        if staging.exists() {
            fs::remove_file(&staging).map_err(|_| SidecarPackageError::IoUnavailable)?;
        }
        let install_result = (|| {
            let mut file = OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(&staging)
                .map_err(|_| SidecarPackageError::IoUnavailable)?;
            file.write_all(package)
                .and_then(|_| file.sync_all())
                .map_err(|_| SidecarPackageError::IoUnavailable)?;
            set_executable_permissions(&staging)?;
            fs::rename(&staging, &executable).map_err(|_| SidecarPackageError::IoUnavailable)?;
            Ok(executable)
        })();
        if install_result.is_err() {
            let _ = fs::remove_file(&staging);
        }
        install_result
    }
}

fn validate_version(version: &str) -> Result<(), SidecarPackageError> {
    let valid = !version.is_empty()
        && version.len() <= 64
        && version.split('.').all(|part| {
            !part.is_empty() && part.len() <= 10 && part.bytes().all(|byte| byte.is_ascii_digit())
        });
    if valid {
        Ok(())
    } else {
        Err(SidecarPackageError::InvalidVersion)
    }
}

fn create_private_child(parent: &Path, child: &str) -> Result<PathBuf, SidecarPackageError> {
    ensure_private_directory(parent)?;
    let path = parent.join(child);
    match path.symlink_metadata() {
        Ok(metadata) if metadata.file_type().is_symlink() || !metadata.is_dir() => {
            return Err(SidecarPackageError::InvalidRoot)
        }
        Ok(_) => {}
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            fs::create_dir(&path).map_err(|_| SidecarPackageError::IoUnavailable)?;
        }
        Err(_) => return Err(SidecarPackageError::IoUnavailable),
    }
    ensure_private_directory(&path)?;
    Ok(path)
}

fn ensure_private_directory(path: &Path) -> Result<(), SidecarPackageError> {
    match path.symlink_metadata() {
        Ok(metadata) if metadata.file_type().is_symlink() || !metadata.is_dir() => {
            return Err(SidecarPackageError::InvalidRoot)
        }
        Ok(_) => {}
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            fs::create_dir(path).map_err(|_| SidecarPackageError::IoUnavailable)?;
        }
        Err(_) => return Err(SidecarPackageError::IoUnavailable),
    }
    set_private_directory_permissions(path)
}

#[cfg(unix)]
fn set_private_directory_permissions(path: &Path) -> Result<(), SidecarPackageError> {
    use std::os::unix::fs::PermissionsExt;
    fs::set_permissions(path, fs::Permissions::from_mode(0o700))
        .map_err(|_| SidecarPackageError::IoUnavailable)
}

#[cfg(not(unix))]
fn set_private_directory_permissions(_path: &Path) -> Result<(), SidecarPackageError> {
    Ok(())
}

#[cfg(unix)]
fn set_executable_permissions(path: &Path) -> Result<(), SidecarPackageError> {
    use std::os::unix::fs::PermissionsExt;
    fs::set_permissions(path, fs::Permissions::from_mode(0o700))
        .map_err(|_| SidecarPackageError::IoUnavailable)
}

#[cfg(not(unix))]
fn set_executable_permissions(_path: &Path) -> Result<(), SidecarPackageError> {
    Ok(())
}

pub fn redact_log_line(line: &str) -> String {
    static BEARER: OnceLock<Regex> = OnceLock::new();
    static ASSIGNMENT: OnceLock<Regex> = OnceLock::new();
    static PRIVATE_PATH: OnceLock<Regex> = OnceLock::new();

    let bearer = BEARER.get_or_init(|| Regex::new(r"(?i)Bearer\s+[^\s]+").expect("valid regex"));
    let assignment = ASSIGNMENT.get_or_init(|| {
        Regex::new(r"(?i)(cookie|token|password|secret|api[_-]?key)=[^\s]+").expect("valid regex")
    });
    let private_path = PRIVATE_PATH.get_or_init(|| {
        Regex::new(r"(?i)(?:/Users|/home|/root|/tmp|/private/var/folders)/[^\s]+|[A-Z]:\\[^\s]+")
            .expect("valid regex")
    });

    let line = bearer.replace_all(line, "Bearer [REDACTED]");
    let line = assignment.replace_all(&line, "$1=[REDACTED]");
    private_path.replace_all(&line, "[REDACTED]").into_owned()
}
