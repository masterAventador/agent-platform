use chacha20poly1305::aead::{Aead, KeyInit, Payload};
use chacha20poly1305::{ChaCha20Poly1305, Nonce};
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use uuid::Uuid;

const COOKIE_MAGIC: &[u8; 4] = b"SOC1";
const COOKIE_NONCE_BYTES: usize = 12;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BrowserSessionError {
    InvalidProfileIdentity,
    InvalidRoot,
    IoUnavailable,
    CookieEncryptionFailed,
    CookieDecryptionFailed,
    InvalidLoginTransition,
    ManualResumeRequired,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SocialPlatform {
    Douyin,
    Xiaohongshu,
    Kuaishou,
    WechatChannels,
    Wechat,
}

impl SocialPlatform {
    fn as_str(self) -> &'static str {
        match self {
            Self::Douyin => "douyin",
            Self::Xiaohongshu => "xiaohongshu",
            Self::Kuaishou => "kuaishou",
            Self::WechatChannels => "wechat_channels",
            Self::Wechat => "wechat",
        }
    }

    fn parse(value: &str) -> Option<Self> {
        match value {
            "douyin" => Some(Self::Douyin),
            "xiaohongshu" => Some(Self::Xiaohongshu),
            "kuaishou" => Some(Self::Kuaishou),
            "wechat_channels" => Some(Self::WechatChannels),
            "wechat" => Some(Self::Wechat),
            _ => None,
        }
    }
}

#[derive(Debug, Clone)]
pub struct BrowserProfile {
    path: PathBuf,
}

impl BrowserProfile {
    pub fn prepare(
        root: impl AsRef<Path>,
        platform: SocialPlatform,
        account_id: &str,
    ) -> Result<Self, BrowserSessionError> {
        validate_account_id(account_id)?;
        let storage = create_private_child(root.as_ref(), "social-operations")?;
        let profiles = create_private_child(&storage, "browser-profiles")?;
        let platform = create_private_child(&profiles, platform.as_str())?;
        let path = create_private_child(&platform, account_id)?;
        Ok(Self { path })
    }

    pub fn prepare_raw(
        root: impl AsRef<Path>,
        platform: &str,
        account_id: &str,
    ) -> Result<Self, BrowserSessionError> {
        let platform =
            SocialPlatform::parse(platform).ok_or(BrowserSessionError::InvalidProfileIdentity)?;
        Self::prepare(root, platform, account_id)
    }

    pub fn path(&self) -> &Path {
        &self.path
    }
}

pub struct EncryptedCookieVault {
    root: PathBuf,
    cipher: ChaCha20Poly1305,
}

impl EncryptedCookieVault {
    pub fn new(
        root: impl AsRef<Path>,
        encryption_key: [u8; 32],
    ) -> Result<Self, BrowserSessionError> {
        let storage = create_private_child(root.as_ref(), "social-operations")?;
        let root = create_private_child(&storage, "browser-state")?;
        Ok(Self {
            root,
            cipher: ChaCha20Poly1305::new((&encryption_key).into()),
        })
    }

    pub fn store(&self, account_id: &str, cookies: &[u8]) -> Result<PathBuf, BrowserSessionError> {
        let path = self.path_for_test(account_id)?;
        let mut nonce = [0_u8; COOKIE_NONCE_BYTES];
        getrandom::fill(&mut nonce).map_err(|_| BrowserSessionError::CookieEncryptionFailed)?;
        let ciphertext = self
            .cipher
            .encrypt(
                Nonce::from_slice(&nonce),
                Payload {
                    msg: cookies,
                    aad: account_id.as_bytes(),
                },
            )
            .map_err(|_| BrowserSessionError::CookieEncryptionFailed)?;
        let staging = path.with_extension("cookies.staging");
        let result = (|| {
            let mut file = OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(&staging)
                .map_err(|_| BrowserSessionError::IoUnavailable)?;
            file.write_all(COOKIE_MAGIC)
                .and_then(|_| file.write_all(&nonce))
                .and_then(|_| file.write_all(&ciphertext))
                .and_then(|_| file.sync_all())
                .map_err(|_| BrowserSessionError::IoUnavailable)?;
            set_private_file_permissions(&staging)?;
            fs::rename(&staging, &path).map_err(|_| BrowserSessionError::IoUnavailable)?;
            Ok(path)
        })();
        if result.is_err() {
            let _ = fs::remove_file(&staging);
        }
        result
    }

    pub fn load(&self, account_id: &str) -> Result<Option<Vec<u8>>, BrowserSessionError> {
        let path = self.path_for_test(account_id)?;
        let stored = match fs::read(path) {
            Ok(stored) => stored,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
            Err(_) => return Err(BrowserSessionError::IoUnavailable),
        };
        if stored.len() <= COOKIE_MAGIC.len() + COOKIE_NONCE_BYTES
            || &stored[..COOKIE_MAGIC.len()] != COOKIE_MAGIC
        {
            return Err(BrowserSessionError::CookieDecryptionFailed);
        }
        let nonce_start = COOKIE_MAGIC.len();
        let ciphertext_start = nonce_start + COOKIE_NONCE_BYTES;
        self.cipher
            .decrypt(
                Nonce::from_slice(&stored[nonce_start..ciphertext_start]),
                Payload {
                    msg: &stored[ciphertext_start..],
                    aad: account_id.as_bytes(),
                },
            )
            .map(Some)
            .map_err(|_| BrowserSessionError::CookieDecryptionFailed)
    }

    pub fn logout(&self, account_id: &str) -> Result<(), BrowserSessionError> {
        let path = self.path_for_test(account_id)?;
        match fs::remove_file(path) {
            Ok(()) => Ok(()),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
            Err(_) => Err(BrowserSessionError::IoUnavailable),
        }
    }

    pub fn path_for_test(&self, account_id: &str) -> Result<PathBuf, BrowserSessionError> {
        validate_account_id(account_id)?;
        Ok(self.root.join(format!("{account_id}.cookies")))
    }
}

fn validate_account_id(account_id: &str) -> Result<(), BrowserSessionError> {
    let parsed =
        Uuid::parse_str(account_id).map_err(|_| BrowserSessionError::InvalidProfileIdentity)?;
    if parsed.hyphenated().to_string() == account_id.to_ascii_lowercase() {
        Ok(())
    } else {
        Err(BrowserSessionError::InvalidProfileIdentity)
    }
}

fn create_private_child(parent: &Path, child: &str) -> Result<PathBuf, BrowserSessionError> {
    ensure_private_directory(parent)?;
    let path = parent.join(child);
    match path.symlink_metadata() {
        Ok(metadata) if metadata.file_type().is_symlink() || !metadata.is_dir() => {
            return Err(BrowserSessionError::InvalidRoot)
        }
        Ok(_) => {}
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            fs::create_dir(&path).map_err(|_| BrowserSessionError::IoUnavailable)?;
        }
        Err(_) => return Err(BrowserSessionError::IoUnavailable),
    }
    ensure_private_directory(&path)?;
    Ok(path)
}

fn ensure_private_directory(path: &Path) -> Result<(), BrowserSessionError> {
    match path.symlink_metadata() {
        Ok(metadata) if metadata.file_type().is_symlink() || !metadata.is_dir() => {
            return Err(BrowserSessionError::InvalidRoot)
        }
        Ok(_) => {}
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            fs::create_dir(path).map_err(|_| BrowserSessionError::IoUnavailable)?;
        }
        Err(_) => return Err(BrowserSessionError::IoUnavailable),
    }
    set_private_directory_permissions(path)
}

#[cfg(unix)]
fn set_private_directory_permissions(path: &Path) -> Result<(), BrowserSessionError> {
    use std::os::unix::fs::PermissionsExt;
    fs::set_permissions(path, fs::Permissions::from_mode(0o700))
        .map_err(|_| BrowserSessionError::IoUnavailable)
}

#[cfg(not(unix))]
fn set_private_directory_permissions(_path: &Path) -> Result<(), BrowserSessionError> {
    Ok(())
}

#[cfg(unix)]
fn set_private_file_permissions(path: &Path) -> Result<(), BrowserSessionError> {
    use std::os::unix::fs::PermissionsExt;
    fs::set_permissions(path, fs::Permissions::from_mode(0o600))
        .map_err(|_| BrowserSessionError::IoUnavailable)
}

#[cfg(not(unix))]
fn set_private_file_permissions(_path: &Path) -> Result<(), BrowserSessionError> {
    Ok(())
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LoginState {
    LoggedOut,
    AwaitingScan,
    AwaitingConfirmation,
    Healthy,
    HumanHandoff,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LoginSignal {
    BeginQr,
    QrScanned,
    Authenticated,
    CaptchaRequired,
    RiskControl,
    OperatorResume,
    Logout,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct LoginSnapshot {
    pub state: LoginState,
    pub circuit_open: bool,
    pub session_revision: u64,
}

pub struct QrLoginSession {
    snapshot: LoginSnapshot,
}

impl Default for QrLoginSession {
    fn default() -> Self {
        Self::new()
    }
}

impl QrLoginSession {
    pub fn new() -> Self {
        Self {
            snapshot: LoginSnapshot {
                state: LoginState::LoggedOut,
                circuit_open: true,
                session_revision: 0,
            },
        }
    }

    pub fn snapshot(&self) -> LoginSnapshot {
        self.snapshot
    }

    pub fn apply(&mut self, signal: LoginSignal) -> Result<LoginSnapshot, BrowserSessionError> {
        if self.snapshot.state == LoginState::HumanHandoff
            && signal != LoginSignal::OperatorResume
            && signal != LoginSignal::Logout
        {
            return Err(BrowserSessionError::ManualResumeRequired);
        }

        match signal {
            LoginSignal::CaptchaRequired | LoginSignal::RiskControl => {
                self.snapshot.state = LoginState::HumanHandoff;
                self.snapshot.circuit_open = true;
            }
            LoginSignal::OperatorResume if self.snapshot.state == LoginState::HumanHandoff => {
                self.snapshot.state = LoginState::AwaitingScan;
                self.snapshot.circuit_open = true;
                self.snapshot.session_revision += 1;
            }
            LoginSignal::BeginQr if self.snapshot.state == LoginState::LoggedOut => {
                self.snapshot.state = LoginState::AwaitingScan;
                self.snapshot.circuit_open = true;
            }
            LoginSignal::QrScanned if self.snapshot.state == LoginState::AwaitingScan => {
                self.snapshot.state = LoginState::AwaitingConfirmation;
            }
            LoginSignal::Authenticated
                if self.snapshot.state == LoginState::AwaitingConfirmation =>
            {
                self.snapshot.state = LoginState::Healthy;
                self.snapshot.circuit_open = false;
            }
            LoginSignal::Logout => {
                self.snapshot.state = LoginState::LoggedOut;
                self.snapshot.circuit_open = true;
                self.snapshot.session_revision += 1;
            }
            _ => return Err(BrowserSessionError::InvalidLoginTransition),
        }
        Ok(self.snapshot)
    }
}
