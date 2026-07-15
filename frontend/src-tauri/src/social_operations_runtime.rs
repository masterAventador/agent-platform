use crate::browser_session::{
    BrowserProfile, BrowserSessionError, EncryptedCookieVault, LoginSignal, LoginSnapshot,
    LoginState, QrLoginSession,
};
use crate::local_executor::{LocalExecutorError, LocalExecutorManager, LocalExecutorStatus};
use crate::sidecar_package::{SidecarPackageError, SignedSidecarManifest, TrustedSidecarInstaller};
use serde::Serialize;
use serde_json::Value;
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::time::Duration;
use tauri::State;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum SocialRuntimeError {
    TrustAnchorUnavailable,
    PackageRejected,
    StorageUnavailable,
    InvalidAccount,
    AccountNotHealthy,
    AccountCircuitOpen,
    ActiveAccountConflict,
    ExecutorUnavailable,
    RuntimeUnavailable,
}

impl From<SidecarPackageError> for SocialRuntimeError {
    fn from(_: SidecarPackageError) -> Self {
        Self::PackageRejected
    }
}

impl From<BrowserSessionError> for SocialRuntimeError {
    fn from(error: BrowserSessionError) -> Self {
        match error {
            BrowserSessionError::InvalidProfileIdentity
            | BrowserSessionError::InvalidLoginTransition
            | BrowserSessionError::ManualResumeRequired => Self::InvalidAccount,
            BrowserSessionError::InvalidRoot
            | BrowserSessionError::IoUnavailable
            | BrowserSessionError::CookieEncryptionFailed
            | BrowserSessionError::CookieDecryptionFailed => Self::StorageUnavailable,
        }
    }
}

impl From<LocalExecutorError> for SocialRuntimeError {
    fn from(_: LocalExecutorError) -> Self {
        Self::ExecutorUnavailable
    }
}

struct ManagedAccount {
    platform: String,
    profile: BrowserProfile,
    login: QrLoginSession,
}

pub struct SocialOperationsRuntime {
    root: PathBuf,
    verifying_key: Option<[u8; 32]>,
    max_package_bytes: usize,
    cookie_vault: EncryptedCookieVault,
    installed_sidecar: Option<PathBuf>,
    accounts: HashMap<String, ManagedAccount>,
    active_account: Option<String>,
}

impl SocialOperationsRuntime {
    pub fn new(
        root: impl AsRef<Path>,
        verifying_key: Option<[u8; 32]>,
        max_package_bytes: usize,
    ) -> Result<Self, SocialRuntimeError> {
        let root = root.as_ref().to_path_buf();
        let cookie_vault = EncryptedCookieVault::open(&root)?;
        let installed_sidecar = match verifying_key {
            Some(key) => TrustedSidecarInstaller::new(&root, key, max_package_bytes)?
                .installed_current_verified()?,
            None => None,
        };
        Ok(Self {
            root,
            verifying_key,
            max_package_bytes,
            cookie_vault,
            installed_sidecar,
            accounts: HashMap::new(),
            active_account: None,
        })
    }

    fn installer(&self) -> Result<TrustedSidecarInstaller, SocialRuntimeError> {
        let verifying_key = self
            .verifying_key
            .ok_or(SocialRuntimeError::TrustAnchorUnavailable)?;
        TrustedSidecarInstaller::new(&self.root, verifying_key, self.max_package_bytes)
            .map_err(Into::into)
    }

    pub fn install_manifest(
        &mut self,
        manifest: &SignedSidecarManifest,
        package: &[u8],
        signature: &[u8],
    ) -> Result<PathBuf, SocialRuntimeError> {
        let installed = self
            .installer()?
            .install_manifest(manifest, package, signature)?;
        self.installed_sidecar = Some(installed.clone());
        Ok(installed)
    }

    pub fn download_manifest(
        &mut self,
        download_url: &str,
        manifest: &SignedSidecarManifest,
        signature: &[u8],
    ) -> Result<PathBuf, SocialRuntimeError> {
        let installed = self
            .installer()?
            .download_manifest(download_url, manifest, signature)?;
        self.installed_sidecar = Some(installed.clone());
        Ok(installed)
    }

    pub fn prepare_account(
        &mut self,
        platform: &str,
        account_id: &str,
    ) -> Result<PathBuf, SocialRuntimeError> {
        if let Some(account) = self.accounts.get_mut(account_id) {
            if account.platform != platform {
                return Err(SocialRuntimeError::InvalidAccount);
            }
            if !account.profile.path().is_dir() {
                account.profile = BrowserProfile::prepare_raw(&self.root, platform, account_id)?;
                account.login = QrLoginSession::new();
            }
            return Ok(account.profile.path().to_path_buf());
        }
        let profile = BrowserProfile::prepare_raw(&self.root, platform, account_id)?;
        let path = profile.path().to_path_buf();
        self.accounts.insert(
            account_id.to_owned(),
            ManagedAccount {
                platform: platform.to_owned(),
                profile,
                login: QrLoginSession::new(),
            },
        );
        Ok(path)
    }

    pub fn apply_login_signal(
        &mut self,
        account_id: &str,
        signal: LoginSignal,
    ) -> Result<LoginSnapshot, SocialRuntimeError> {
        self.accounts
            .get_mut(account_id)
            .ok_or(SocialRuntimeError::InvalidAccount)?
            .login
            .apply(signal)
            .map_err(Into::into)
    }

    pub fn apply_login_signal_with_executor(
        &mut self,
        account_id: &str,
        signal: LoginSignal,
        executor: &mut LocalExecutorManager,
    ) -> Result<LoginSnapshot, SocialRuntimeError> {
        let fail_safe = matches!(
            signal,
            LoginSignal::CaptchaRequired | LoginSignal::RiskControl | LoginSignal::LoginExpired
        );
        let transition = self.apply_login_signal(account_id, signal);
        if !fail_safe {
            return transition;
        }

        let stop_result = if self.active_account.as_deref() == Some(account_id) {
            self.active_account = None;
            executor
                .stop()
                .map(|_| ())
                .map_err(SocialRuntimeError::from)
        } else {
            Ok(())
        };
        let snapshot = transition?;
        stop_result?;
        Ok(snapshot)
    }

    pub fn account_snapshot(&self, account_id: &str) -> Result<LoginSnapshot, SocialRuntimeError> {
        self.accounts
            .get(account_id)
            .map(|account| account.login.snapshot())
            .ok_or(SocialRuntimeError::InvalidAccount)
    }

    pub fn store_cookies(
        &self,
        account_id: &str,
        cookies: &[u8],
    ) -> Result<(), SocialRuntimeError> {
        if !self.accounts.contains_key(account_id) {
            return Err(SocialRuntimeError::InvalidAccount);
        }
        self.cookie_vault.store(account_id, cookies)?;
        Ok(())
    }

    pub fn load_cookies(&self, account_id: &str) -> Result<Option<Vec<u8>>, SocialRuntimeError> {
        if !self.accounts.contains_key(account_id) {
            return Err(SocialRuntimeError::InvalidAccount);
        }
        self.cookie_vault.load(account_id).map_err(Into::into)
    }

    pub fn start_account(
        &mut self,
        account_id: &str,
        executor: &mut LocalExecutorManager,
    ) -> Result<LocalExecutorStatus, SocialRuntimeError> {
        if self
            .active_account
            .as_deref()
            .is_some_and(|active| active != account_id)
        {
            return Err(SocialRuntimeError::ActiveAccountConflict);
        }
        let snapshot = self.account_snapshot(account_id)?;
        if snapshot.state != LoginState::Healthy {
            return Err(SocialRuntimeError::AccountNotHealthy);
        }
        if snapshot.circuit_open {
            return Err(SocialRuntimeError::AccountCircuitOpen);
        }
        let executable = self
            .installed_sidecar
            .clone()
            .ok_or(SocialRuntimeError::PackageRejected)?;
        let status = executor.start_installed(executable)?;
        self.active_account = Some(account_id.to_owned());
        Ok(status)
    }

    pub fn invoke_account(
        &mut self,
        account_id: &str,
        executor: &mut LocalExecutorManager,
        request: Value,
        timeout: Duration,
    ) -> Result<Value, SocialRuntimeError> {
        if self.active_account.as_deref() != Some(account_id) {
            return Err(SocialRuntimeError::InvalidAccount);
        }
        let snapshot = self.account_snapshot(account_id)?;
        if snapshot.state != LoginState::Healthy {
            return Err(SocialRuntimeError::AccountNotHealthy);
        }
        if snapshot.circuit_open {
            return Err(SocialRuntimeError::AccountCircuitOpen);
        }
        executor
            .invoke_with_timeout(request, timeout)
            .map_err(Into::into)
    }

    pub fn logout_account(
        &mut self,
        account_id: &str,
        executor: &mut LocalExecutorManager,
    ) -> Result<(), SocialRuntimeError> {
        let profile = {
            let account = self
                .accounts
                .get_mut(account_id)
                .ok_or(SocialRuntimeError::InvalidAccount)?;
            account.login.apply(LoginSignal::Logout)?;
            account.profile.clone()
        };
        let stop_result = if self.active_account.as_deref() == Some(account_id) {
            self.active_account = None;
            executor
                .stop()
                .map(|_| ())
                .map_err(SocialRuntimeError::from)
        } else {
            Ok(())
        };
        let cookie_result = self
            .cookie_vault
            .logout(account_id)
            .map_err(SocialRuntimeError::from);
        let profile_result = profile.remove().map_err(SocialRuntimeError::from);
        stop_result?;
        cookie_result?;
        profile_result
    }

    pub fn emergency_stop(
        &mut self,
        account_id: &str,
        executor: &mut LocalExecutorManager,
    ) -> Result<(), SocialRuntimeError> {
        self.apply_login_signal_with_executor(account_id, LoginSignal::RiskControl, executor)
            .map(|_| ())
    }
}

fn parse_login_signal(value: &str) -> Result<LoginSignal, SocialRuntimeError> {
    match value {
        "begin_qr" => Ok(LoginSignal::BeginQr),
        "qr_scanned" => Ok(LoginSignal::QrScanned),
        "authenticated" => Ok(LoginSignal::Authenticated),
        "captcha_required" => Ok(LoginSignal::CaptchaRequired),
        "risk_control" => Ok(LoginSignal::RiskControl),
        "login_expired" => Ok(LoginSignal::LoginExpired),
        "operator_resume" => Ok(LoginSignal::OperatorResume),
        "logout" => Ok(LoginSignal::Logout),
        _ => Err(SocialRuntimeError::InvalidAccount),
    }
}

fn lock_runtime<'a>(
    state: &'a State<'_, Mutex<SocialOperationsRuntime>>,
) -> Result<std::sync::MutexGuard<'a, SocialOperationsRuntime>, SocialRuntimeError> {
    state
        .lock()
        .map_err(|_| SocialRuntimeError::RuntimeUnavailable)
}

fn lock_executor<'a>(
    state: &'a State<'_, Mutex<LocalExecutorManager>>,
) -> Result<std::sync::MutexGuard<'a, LocalExecutorManager>, SocialRuntimeError> {
    state
        .lock()
        .map_err(|_| SocialRuntimeError::RuntimeUnavailable)
}

#[tauri::command]
pub fn social_sidecar_install(
    runtime: State<'_, Mutex<SocialOperationsRuntime>>,
    manifest: SignedSidecarManifest,
    package: Vec<u8>,
    signature: Vec<u8>,
) -> Result<String, SocialRuntimeError> {
    lock_runtime(&runtime)?.install_manifest(&manifest, &package, &signature)?;
    Ok(manifest.version)
}

#[tauri::command]
pub fn social_sidecar_download(
    runtime: State<'_, Mutex<SocialOperationsRuntime>>,
    download_url: String,
    manifest: SignedSidecarManifest,
    signature: Vec<u8>,
) -> Result<String, SocialRuntimeError> {
    lock_runtime(&runtime)?.download_manifest(&download_url, &manifest, &signature)?;
    Ok(manifest.version)
}

#[tauri::command]
pub fn social_account_prepare(
    runtime: State<'_, Mutex<SocialOperationsRuntime>>,
    platform: String,
    account_id: String,
) -> Result<LoginSnapshot, SocialRuntimeError> {
    let mut runtime = lock_runtime(&runtime)?;
    runtime.prepare_account(&platform, &account_id)?;
    runtime.account_snapshot(&account_id)
}

#[tauri::command]
pub fn social_account_login_signal(
    runtime: State<'_, Mutex<SocialOperationsRuntime>>,
    executor: State<'_, Mutex<LocalExecutorManager>>,
    account_id: String,
    signal: String,
) -> Result<LoginSnapshot, SocialRuntimeError> {
    let mut runtime = lock_runtime(&runtime)?;
    let mut executor = lock_executor(&executor)?;
    runtime.apply_login_signal_with_executor(
        &account_id,
        parse_login_signal(&signal)?,
        &mut executor,
    )
}

#[tauri::command]
pub fn social_account_store_cookies(
    runtime: State<'_, Mutex<SocialOperationsRuntime>>,
    account_id: String,
    cookies: Vec<u8>,
) -> Result<(), SocialRuntimeError> {
    lock_runtime(&runtime)?.store_cookies(&account_id, &cookies)
}

#[tauri::command]
pub fn social_account_has_cookies(
    runtime: State<'_, Mutex<SocialOperationsRuntime>>,
    account_id: String,
) -> Result<bool, SocialRuntimeError> {
    lock_runtime(&runtime)?
        .load_cookies(&account_id)
        .map(|cookies| cookies.is_some())
}

#[tauri::command]
pub fn social_account_start(
    runtime: State<'_, Mutex<SocialOperationsRuntime>>,
    executor: State<'_, Mutex<LocalExecutorManager>>,
    account_id: String,
) -> Result<LocalExecutorStatus, SocialRuntimeError> {
    let mut runtime = lock_runtime(&runtime)?;
    let mut executor = lock_executor(&executor)?;
    runtime.start_account(&account_id, &mut executor)
}

#[tauri::command]
pub fn social_account_invoke(
    runtime: State<'_, Mutex<SocialOperationsRuntime>>,
    executor: State<'_, Mutex<LocalExecutorManager>>,
    account_id: String,
    request: Value,
) -> Result<Value, SocialRuntimeError> {
    let mut runtime = lock_runtime(&runtime)?;
    let mut executor = lock_executor(&executor)?;
    runtime.invoke_account(&account_id, &mut executor, request, Duration::from_secs(30))
}

#[tauri::command]
pub fn social_account_logout(
    runtime: State<'_, Mutex<SocialOperationsRuntime>>,
    executor: State<'_, Mutex<LocalExecutorManager>>,
    account_id: String,
) -> Result<(), SocialRuntimeError> {
    let mut runtime = lock_runtime(&runtime)?;
    let mut executor = lock_executor(&executor)?;
    runtime.logout_account(&account_id, &mut executor)
}

#[tauri::command]
pub fn social_account_emergency_stop(
    runtime: State<'_, Mutex<SocialOperationsRuntime>>,
    executor: State<'_, Mutex<LocalExecutorManager>>,
    account_id: String,
) -> Result<(), SocialRuntimeError> {
    let mut runtime = lock_runtime(&runtime)?;
    let mut executor = lock_executor(&executor)?;
    runtime.emergency_stop(&account_id, &mut executor)
}

#[tauri::command]
pub fn social_executor_take_safe_diagnostics(
    executor: State<'_, Mutex<LocalExecutorManager>>,
) -> Result<Vec<String>, SocialRuntimeError> {
    Ok(lock_executor(&executor)?.take_safe_diagnostics())
}
