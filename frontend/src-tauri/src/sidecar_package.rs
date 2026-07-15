use ed25519_dalek::{Signature, VerifyingKey};
use regex::Regex;
use reqwest::blocking::Client;
use reqwest::redirect::Policy;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::fs::{self, OpenOptions};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::sync::OnceLock;
use std::time::Duration;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SidecarPackageError {
    InvalidRoot,
    InvalidVersion,
    UntrustedDownloadUrl,
    DownloadFailed,
    PackageTooLarge,
    SignatureInvalid,
    DigestMismatch,
    PlatformMismatch,
    RollbackRejected,
    IoUnavailable,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SignedSidecarManifest {
    pub version: String,
    pub platform: String,
    pub arch: String,
    pub sha256: String,
    pub package_size: u64,
}

#[derive(Debug)]
pub struct VerifiedSidecarExecutable {
    pub path: PathBuf,
    pub file: std::fs::File,
    pub contents: Vec<u8>,
}

impl SignedSidecarManifest {
    pub fn for_current(version: &str, package: &[u8]) -> Result<Self, SidecarPackageError> {
        validate_version(version)?;
        Ok(Self {
            version: version.to_owned(),
            platform: std::env::consts::OS.to_owned(),
            arch: std::env::consts::ARCH.to_owned(),
            sha256: hex_digest(package),
            package_size: package.len() as u64,
        })
    }

    pub fn signing_bytes(&self) -> Vec<u8> {
        format!(
            "version={}\nplatform={}\narch={}\nsha256={}\npackage_size={}\n",
            self.version, self.platform, self.arch, self.sha256, self.package_size
        )
        .into_bytes()
    }
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
        Self::new_with_timeouts(
            root,
            verifying_key,
            max_package_bytes,
            Duration::from_secs(5),
            Duration::from_secs(30),
        )
    }

    pub fn new_with_timeouts(
        root: impl AsRef<Path>,
        verifying_key: [u8; 32],
        max_package_bytes: usize,
        connect_timeout: Duration,
        request_timeout: Duration,
    ) -> Result<Self, SidecarPackageError> {
        let root = root.as_ref();
        if root.as_os_str().is_empty() || max_package_bytes == 0 {
            return Err(SidecarPackageError::InvalidRoot);
        }
        ensure_no_symlink_ancestors(root)?;
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
            .connect_timeout(connect_timeout)
            .timeout(request_timeout)
            .redirect(Policy::custom(|attempt| {
                if trusted_download_url(attempt.url()) {
                    attempt.follow()
                } else {
                    attempt.error("untrusted sidecar redirect")
                }
            }))
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
        validate_version(version)?;
        let package = self.download_package(download_url)?;
        self.install_bytes(version, &package, signature)
    }

    pub fn download_manifest(
        &self,
        download_url: &str,
        manifest: &SignedSidecarManifest,
        signature: &[u8],
    ) -> Result<PathBuf, SidecarPackageError> {
        let package = self.download_package(download_url)?;
        self.install_manifest(manifest, &package, signature)
    }

    fn download_package(&self, download_url: &str) -> Result<Vec<u8>, SidecarPackageError> {
        let url = reqwest::Url::parse(download_url)
            .map_err(|_| SidecarPackageError::UntrustedDownloadUrl)?;
        if !trusted_download_url(&url) {
            return Err(SidecarPackageError::UntrustedDownloadUrl);
        }

        let mut response = self.client.get(url).send().map_err(|error| {
            if error.is_redirect() {
                SidecarPackageError::UntrustedDownloadUrl
            } else {
                SidecarPackageError::DownloadFailed
            }
        })?;
        if !trusted_download_url(response.url()) {
            return Err(SidecarPackageError::UntrustedDownloadUrl);
        }
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
        Ok(package)
    }

    pub fn install_manifest(
        &self,
        manifest: &SignedSidecarManifest,
        package: &[u8],
        signature: &[u8],
    ) -> Result<PathBuf, SidecarPackageError> {
        self.verify_manifest_package(manifest, package, signature)?;
        if let Some(current) = self.current_version()? {
            if compare_versions(&manifest.version, &current)? == std::cmp::Ordering::Less {
                return Err(SidecarPackageError::RollbackRejected);
            }
        }
        let installed = self.install_verified(&manifest.version, package)?;
        self.write_signed_metadata(manifest, signature)?;
        self.write_current_version(&manifest.version)?;
        Ok(installed)
    }

    pub fn installed_current_verified(&self) -> Result<Option<PathBuf>, SidecarPackageError> {
        Ok(self
            .installed_current_verified_executable()?
            .map(|executable| executable.path))
    }

    pub fn installed_current_verified_executable(
        &self,
    ) -> Result<Option<VerifiedSidecarExecutable>, SidecarPackageError> {
        let Some(version) = self.current_version()? else {
            return Ok(None);
        };
        let version_dir = self.root.join("sidecars").join(&version);
        ensure_existing_private_directory(&version_dir)?;
        let manifest_bytes = read_private_bounded(&version_dir.join(".manifest.json"), 4096)?;
        let signature = read_private_bounded(&version_dir.join(".manifest.sig"), 128)?;
        let manifest: SignedSidecarManifest = serde_json::from_slice(&manifest_bytes)
            .map_err(|_| SidecarPackageError::SignatureInvalid)?;
        if manifest.version != version {
            return Err(SidecarPackageError::SignatureInvalid);
        }
        let executable = version_dir.join("social-operations-sidecar");
        let (file, package) =
            read_private_bounded_with_stable_identity(&executable, self.max_package_bytes)?;
        self.verify_manifest_package(&manifest, &package, &signature)?;
        Ok(Some(VerifiedSidecarExecutable {
            path: executable,
            file,
            contents: package,
        }))
    }

    fn verify_manifest_package(
        &self,
        manifest: &SignedSidecarManifest,
        package: &[u8],
        signature: &[u8],
    ) -> Result<(), SidecarPackageError> {
        validate_version(&manifest.version)?;
        if manifest.platform != std::env::consts::OS || manifest.arch != std::env::consts::ARCH {
            return Err(SidecarPackageError::PlatformMismatch);
        }
        if package.is_empty()
            || package.len() > self.max_package_bytes
            || manifest.package_size != package.len() as u64
        {
            return Err(SidecarPackageError::PackageTooLarge);
        }
        let signature =
            Signature::from_slice(signature).map_err(|_| SidecarPackageError::SignatureInvalid)?;
        self.verifying_key
            .verify_strict(&manifest.signing_bytes(), &signature)
            .map_err(|_| SidecarPackageError::SignatureInvalid)?;
        if manifest.sha256 != hex_digest(package) {
            return Err(SidecarPackageError::DigestMismatch);
        }
        Ok(())
    }

    fn write_signed_metadata(
        &self,
        manifest: &SignedSidecarManifest,
        signature: &[u8],
    ) -> Result<(), SidecarPackageError> {
        let version_dir = self.root.join("sidecars").join(&manifest.version);
        ensure_existing_private_directory(&version_dir)?;
        let manifest_bytes =
            serde_json::to_vec(manifest).map_err(|_| SidecarPackageError::IoUnavailable)?;
        write_private_atomic(&version_dir, ".manifest.json", &manifest_bytes)?;
        write_private_atomic(&version_dir, ".manifest.sig", signature)
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

        self.install_verified(version, package)
    }

    fn install_verified(
        &self,
        version: &str,
        package: &[u8],
    ) -> Result<PathBuf, SidecarPackageError> {
        let sidecars = create_private_child(&self.root, "sidecars")?;
        let version_dir = create_private_child(&sidecars, version)?;

        let executable = version_dir.join("social-operations-sidecar");
        let staging = unique_staging(&version_dir, ".social-operations-sidecar")?;
        let install_result = (|| {
            let mut file = open_new_private(&staging)?;
            file.write_all(package)
                .and_then(|_| file.sync_all())
                .map_err(|_| SidecarPackageError::IoUnavailable)?;
            set_executable_permissions(&staging)?;
            atomic_replace(&staging, &executable)?;
            Ok(executable)
        })();
        if install_result.is_err() {
            let _ = fs::remove_file(&staging);
        }
        install_result
    }

    fn current_version(&self) -> Result<Option<String>, SidecarPackageError> {
        let marker = self.root.join("sidecars/.current-version");
        ensure_no_symlink_ancestors(&marker)?;
        match marker.symlink_metadata() {
            Ok(metadata) if metadata.file_type().is_symlink() || !metadata.is_file() => {
                Err(SidecarPackageError::InvalidRoot)
            }
            Ok(_) => {
                let value = String::from_utf8(read_private_bounded(&marker, 64)?)
                    .map_err(|_| SidecarPackageError::InvalidVersion)?;
                validate_version(value.trim())?;
                Ok(Some(value.trim().to_owned()))
            }
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(None),
            Err(_) => Err(SidecarPackageError::IoUnavailable),
        }
    }

    fn write_current_version(&self, version: &str) -> Result<(), SidecarPackageError> {
        let sidecars = create_private_child(&self.root, "sidecars")?;
        write_private_atomic(&sidecars, ".current-version", version.as_bytes())
    }
}

fn ensure_existing_private_directory(path: &Path) -> Result<(), SidecarPackageError> {
    ensure_no_symlink_ancestors(path)?;
    let metadata = path
        .symlink_metadata()
        .map_err(|_| SidecarPackageError::IoUnavailable)?;
    if metadata.file_type().is_symlink() || !metadata.is_dir() {
        return Err(SidecarPackageError::InvalidRoot);
    }
    set_private_directory_permissions(path)
}

fn read_private_bounded(path: &Path, max_bytes: usize) -> Result<Vec<u8>, SidecarPackageError> {
    let mut file = open_private_read(path)?;
    let mut contents = Vec::new();
    Read::by_ref(&mut file)
        .take(max_bytes as u64 + 1)
        .read_to_end(&mut contents)
        .map_err(|_| SidecarPackageError::IoUnavailable)?;
    if contents.len() > max_bytes {
        return Err(SidecarPackageError::PackageTooLarge);
    }
    Ok(contents)
}

fn read_private_bounded_with_stable_identity(
    path: &Path,
    max_bytes: usize,
) -> Result<(std::fs::File, Vec<u8>), SidecarPackageError> {
    let mut file = open_private_read(path)?;
    let before = file_identity(&file)?;
    let mut contents = Vec::new();
    Read::by_ref(&mut file)
        .take(max_bytes as u64 + 1)
        .read_to_end(&mut contents)
        .map_err(|_| SidecarPackageError::IoUnavailable)?;
    if contents.len() > max_bytes || file_identity(&file)? != before {
        return Err(SidecarPackageError::IoUnavailable);
    }
    let current = open_private_read(path)?;
    if file_identity(&current)? != before {
        return Err(SidecarPackageError::IoUnavailable);
    }
    Ok((file, contents))
}

#[derive(Debug, PartialEq, Eq)]
struct FileIdentity {
    device: u64,
    file: u64,
    length: u64,
}

#[cfg(unix)]
fn file_identity(file: &std::fs::File) -> Result<FileIdentity, SidecarPackageError> {
    use std::os::unix::fs::MetadataExt;
    let metadata = file
        .metadata()
        .map_err(|_| SidecarPackageError::IoUnavailable)?;
    Ok(FileIdentity {
        device: metadata.dev(),
        file: metadata.ino(),
        length: metadata.len(),
    })
}

#[cfg(windows)]
fn file_identity(file: &std::fs::File) -> Result<FileIdentity, SidecarPackageError> {
    use std::mem::zeroed;
    use std::os::windows::io::AsRawHandle;
    use windows_sys::Win32::Storage::FileSystem::{
        GetFileInformationByHandle, BY_HANDLE_FILE_INFORMATION,
    };
    let mut information: BY_HANDLE_FILE_INFORMATION = unsafe { zeroed() };
    if unsafe { GetFileInformationByHandle(file.as_raw_handle() as _, &mut information) } == 0 {
        return Err(SidecarPackageError::IoUnavailable);
    }
    Ok(FileIdentity {
        device: u64::from(information.dwVolumeSerialNumber),
        file: (u64::from(information.nFileIndexHigh) << 32) | u64::from(information.nFileIndexLow),
        length: (u64::from(information.nFileSizeHigh) << 32) | u64::from(information.nFileSizeLow),
    })
}

#[cfg(all(not(unix), not(windows)))]
fn file_identity(file: &std::fs::File) -> Result<FileIdentity, SidecarPackageError> {
    let metadata = file
        .metadata()
        .map_err(|_| SidecarPackageError::IoUnavailable)?;
    Ok(FileIdentity {
        device: 0,
        file: 0,
        length: metadata.len(),
    })
}

fn write_private_atomic(
    parent: &Path,
    filename: &str,
    contents: &[u8],
) -> Result<(), SidecarPackageError> {
    let destination = parent.join(filename);
    let staging = unique_staging(parent, filename)?;
    let result = (|| {
        let mut file = open_new_private(&staging)?;
        file.write_all(contents)
            .and_then(|_| file.sync_all())
            .map_err(|_| SidecarPackageError::IoUnavailable)?;
        set_private_file_permissions(&staging)?;
        atomic_replace(&staging, &destination)
    })();
    if result.is_err() {
        let _ = fs::remove_file(&staging);
    }
    result
}

fn trusted_download_url(url: &reqwest::Url) -> bool {
    (url.scheme() == "https"
        || (url.scheme() == "http"
            && matches!(url.host_str(), Some("127.0.0.1" | "localhost" | "::1"))))
        && url.username().is_empty()
        && url.password().is_none()
}

fn hex_digest(package: &[u8]) -> String {
    Sha256::digest(package)
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

fn compare_versions(left: &str, right: &str) -> Result<std::cmp::Ordering, SidecarPackageError> {
    validate_version(left)?;
    validate_version(right)?;
    let parse = |value: &str| {
        value
            .split('.')
            .map(|part| {
                part.parse::<u64>()
                    .map_err(|_| SidecarPackageError::InvalidVersion)
            })
            .collect::<Result<Vec<_>, _>>()
    };
    Ok(parse(left)?.cmp(&parse(right)?))
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
    ensure_no_symlink_ancestors(path)?;
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

fn ensure_no_symlink_ancestors(path: &Path) -> Result<(), SidecarPackageError> {
    let mut absolute = if path.is_absolute() {
        path.to_path_buf()
    } else {
        std::env::current_dir()
            .map_err(|_| SidecarPackageError::IoUnavailable)?
            .join(path)
    };
    #[cfg(target_os = "macos")]
    for alias in ["var", "tmp", "etc"] {
        let prefix = Path::new("/").join(alias);
        if let Ok(suffix) = absolute.strip_prefix(&prefix) {
            absolute = Path::new("/private").join(alias).join(suffix);
            break;
        }
    }
    let mut current = PathBuf::new();
    for component in absolute.components() {
        current.push(component.as_os_str());
        match current.symlink_metadata() {
            Ok(metadata) if metadata.file_type().is_symlink() => {
                return Err(SidecarPackageError::InvalidRoot)
            }
            Ok(_) => {}
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => break,
            Err(_) => return Err(SidecarPackageError::IoUnavailable),
        }
    }
    Ok(())
}

fn unique_staging(parent: &Path, base: &str) -> Result<PathBuf, SidecarPackageError> {
    let mut suffix = [0_u8; 8];
    getrandom::fill(&mut suffix).map_err(|_| SidecarPackageError::IoUnavailable)?;
    let suffix: String = suffix.iter().map(|byte| format!("{byte:02x}")).collect();
    Ok(parent.join(format!("{base}.{suffix}.staging")))
}

fn open_new_private(path: &Path) -> Result<std::fs::File, SidecarPackageError> {
    ensure_no_symlink_ancestors(path)?;
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600).custom_flags(libc::O_NOFOLLOW);
    }
    options
        .open(path)
        .map_err(|_| SidecarPackageError::IoUnavailable)
}

fn open_private_read(path: &Path) -> Result<std::fs::File, SidecarPackageError> {
    ensure_no_symlink_ancestors(path)?;
    let mut options = OpenOptions::new();
    options.read(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.custom_flags(libc::O_NOFOLLOW);
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::OpenOptionsExt;
        use windows_sys::Win32::Storage::FileSystem::FILE_SHARE_READ;
        options.share_mode(FILE_SHARE_READ);
    }
    options
        .open(path)
        .map_err(|_| SidecarPackageError::IoUnavailable)
}

#[cfg(unix)]
fn atomic_replace(staging: &Path, destination: &Path) -> Result<(), SidecarPackageError> {
    fs::rename(staging, destination).map_err(|_| SidecarPackageError::IoUnavailable)
}

#[cfg(windows)]
fn atomic_replace(staging: &Path, destination: &Path) -> Result<(), SidecarPackageError> {
    use std::os::windows::ffi::OsStrExt;
    use windows_sys::Win32::Storage::FileSystem::{
        MoveFileExW, MOVEFILE_REPLACE_EXISTING, MOVEFILE_WRITE_THROUGH,
    };

    let source = staging
        .as_os_str()
        .encode_wide()
        .chain(std::iter::once(0))
        .collect::<Vec<_>>();
    let target = destination
        .as_os_str()
        .encode_wide()
        .chain(std::iter::once(0))
        .collect::<Vec<_>>();
    let replaced = unsafe {
        MoveFileExW(
            source.as_ptr(),
            target.as_ptr(),
            MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH,
        )
    };
    if replaced == 0 {
        Err(SidecarPackageError::IoUnavailable)
    } else {
        Ok(())
    }
}

#[cfg(all(not(unix), not(windows)))]
fn atomic_replace(staging: &Path, destination: &Path) -> Result<(), SidecarPackageError> {
    fs::rename(staging, destination).map_err(|_| SidecarPackageError::IoUnavailable)
}

#[cfg(unix)]
fn set_private_directory_permissions(path: &Path) -> Result<(), SidecarPackageError> {
    use std::os::unix::fs::PermissionsExt;
    fs::set_permissions(path, fs::Permissions::from_mode(0o700))
        .map_err(|_| SidecarPackageError::IoUnavailable)
}

#[cfg(windows)]
fn set_private_directory_permissions(path: &Path) -> Result<(), SidecarPackageError> {
    apply_windows_acl(path, true)
}

#[cfg(all(not(unix), not(windows)))]
fn set_private_directory_permissions(_path: &Path) -> Result<(), SidecarPackageError> {
    Err(SidecarPackageError::IoUnavailable)
}

#[cfg(unix)]
fn set_executable_permissions(path: &Path) -> Result<(), SidecarPackageError> {
    use std::os::unix::fs::PermissionsExt;
    fs::set_permissions(path, fs::Permissions::from_mode(0o700))
        .map_err(|_| SidecarPackageError::IoUnavailable)
}

#[cfg(windows)]
fn set_executable_permissions(path: &Path) -> Result<(), SidecarPackageError> {
    apply_windows_acl(path, false)
}

#[cfg(all(not(unix), not(windows)))]
fn set_executable_permissions(_path: &Path) -> Result<(), SidecarPackageError> {
    Err(SidecarPackageError::IoUnavailable)
}

#[cfg(unix)]
fn set_private_file_permissions(path: &Path) -> Result<(), SidecarPackageError> {
    use std::os::unix::fs::PermissionsExt;
    fs::set_permissions(path, fs::Permissions::from_mode(0o600))
        .map_err(|_| SidecarPackageError::IoUnavailable)
}

#[cfg(windows)]
fn set_private_file_permissions(path: &Path) -> Result<(), SidecarPackageError> {
    apply_windows_acl(path, false)
}

#[cfg(all(not(unix), not(windows)))]
fn set_private_file_permissions(_path: &Path) -> Result<(), SidecarPackageError> {
    Err(SidecarPackageError::IoUnavailable)
}

#[cfg(windows)]
fn apply_windows_acl(path: &Path, directory: bool) -> Result<(), SidecarPackageError> {
    let username = std::env::var("USERNAME").map_err(|_| SidecarPackageError::IoUnavailable)?;
    let grant = if directory {
        format!("{username}:(OI)(CI)F")
    } else {
        format!("{username}:(F)")
    };
    let status = std::process::Command::new("icacls")
        .arg(path)
        .args(["/inheritance:r", "/grant:r"])
        .arg(grant)
        .status()
        .map_err(|_| SidecarPackageError::IoUnavailable)?;
    if status.success() {
        Ok(())
    } else {
        Err(SidecarPackageError::IoUnavailable)
    }
}

pub fn redact_log_line(line: &str) -> String {
    static BEARER: OnceLock<Regex> = OnceLock::new();
    static ASSIGNMENT: OnceLock<Regex> = OnceLock::new();
    static COOKIE_HEADER: OnceLock<Regex> = OnceLock::new();
    static JSON_SECRET: OnceLock<Regex> = OnceLock::new();
    static QUERY_SECRET: OnceLock<Regex> = OnceLock::new();
    static PRIVATE_PATH: OnceLock<Regex> = OnceLock::new();

    let bearer = BEARER.get_or_init(|| Regex::new(r"(?i)Bearer\s+[^\s]+").expect("valid regex"));
    let assignment = ASSIGNMENT.get_or_init(|| {
        Regex::new(r"(?i)(cookie|token|password|secret|api[_-]?key)=[^\s]+").expect("valid regex")
    });
    let cookie_header = COOKIE_HEADER.get_or_init(|| {
        Regex::new(r"(?i)\b(?:Set-Cookie|Cookie):\s*[^\r\n]+").expect("valid regex")
    });
    let json_secret = JSON_SECRET.get_or_init(|| {
        Regex::new(
            r#"(?i)\\?[\"'](cookie|token|password|secret|api[_-]?key)\\?[\"']\s*:\s*\\?[\"'][^\"']+\\?[\"']"#,
        )
        .expect("valid regex")
    });
    let query_secret = QUERY_SECRET.get_or_init(|| {
        Regex::new(
            r"(?i)([?&](?:access[_-]?token|refresh[_-]?token|token|api[_-]?key|password)=)[^&\s]+",
        )
        .expect("valid regex")
    });
    let private_path = PRIVATE_PATH.get_or_init(|| {
        Regex::new(r"(?i)(?:/Users|/home|/root|/tmp|/var/folders|/private/var/folders)/[^\s]+|[A-Z]:\\[^\s]+")
            .expect("valid regex")
    });

    let line = bearer.replace_all(line, "Bearer [REDACTED]");
    let line = cookie_header.replace_all(&line, "Cookie: [REDACTED]");
    let line = json_secret.replace_all(&line, "\"$1\":\"[REDACTED]\"");
    let line = query_secret.replace_all(&line, "$1[REDACTED]");
    let line = assignment.replace_all(&line, "$1=[REDACTED]");
    private_path.replace_all(&line, "[REDACTED]").into_owned()
}
