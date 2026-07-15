use agent_platform_desktop::browser_session::{
    BrowserProfile, BrowserSessionError, EncryptedCookieVault, LoginSignal, LoginState,
    QrLoginSession, SocialPlatform,
};
use tempfile::tempdir;

const ACCOUNT_ID: &str = "00000000-0000-4000-8000-000000000501";

#[test]
fn browser_profile_is_app_owned_and_rejects_path_escape() {
    let root = tempdir().expect("temp root");
    let profile = BrowserProfile::prepare(root.path(), SocialPlatform::Douyin, ACCOUNT_ID)
        .expect("prepare profile");

    assert!(profile.path().starts_with(root.path()));
    assert!(profile.path().ends_with(format!("douyin/{ACCOUNT_ID}")));
    assert!(profile.path().is_dir());
    assert_eq!(
        BrowserProfile::prepare_raw(root.path(), "../../escape", ACCOUNT_ID)
            .expect_err("platform traversal must fail"),
        BrowserSessionError::InvalidProfileIdentity
    );
    assert_eq!(
        BrowserProfile::prepare_raw(root.path(), "douyin", "../escape")
            .expect_err("account traversal must fail"),
        BrowserSessionError::InvalidProfileIdentity
    );
}

#[test]
fn cookie_vault_persists_only_authenticated_ciphertext_and_logout_removes_it() {
    let root = tempdir().expect("temp root");
    let vault = EncryptedCookieVault::new(root.path(), [9_u8; 32]).expect("cookie vault");
    let plaintext = br#"[{"name":"sessionid","value":"private-cookie"}]"#;

    let stored_path = vault.store(ACCOUNT_ID, plaintext).expect("store cookies");
    let stored = std::fs::read(&stored_path).expect("stored ciphertext");
    assert!(!stored
        .windows("private-cookie".len())
        .any(|value| value == b"private-cookie"));
    assert_eq!(
        vault.load(ACCOUNT_ID).expect("load cookies"),
        Some(plaintext.to_vec())
    );

    vault.logout(ACCOUNT_ID).expect("logout");
    assert_eq!(vault.load(ACCOUNT_ID).expect("load after logout"), None);
    assert!(!stored_path.exists());
}

#[test]
fn cookie_ciphertext_cannot_be_moved_between_accounts() {
    let root = tempdir().expect("temp root");
    let vault = EncryptedCookieVault::new(root.path(), [9_u8; 32]).expect("cookie vault");
    let source = vault.store(ACCOUNT_ID, b"private-cookie").expect("store");
    let other = "00000000-0000-4000-8000-000000000502";
    let target = vault.path_for_test(other).expect("target path");
    std::fs::create_dir_all(target.parent().expect("target parent")).expect("target parent");
    std::fs::copy(source, target).expect("copy ciphertext");

    assert_eq!(
        vault
            .load(other)
            .expect_err("account-bound AAD must reject copy"),
        BrowserSessionError::CookieDecryptionFailed
    );
}

#[test]
fn captcha_and_risk_control_require_explicit_human_resume() {
    for signal in [LoginSignal::CaptchaRequired, LoginSignal::RiskControl] {
        let mut session = QrLoginSession::new();
        session.apply(LoginSignal::BeginQr).expect("begin QR");
        session.apply(LoginSignal::QrScanned).expect("scan QR");
        let handoff = session.apply(signal).expect("handoff");
        assert_eq!(handoff.state, LoginState::HumanHandoff);
        assert!(handoff.circuit_open);
        assert_eq!(
            session
                .apply(LoginSignal::Authenticated)
                .expect_err("health signal cannot bypass handoff"),
            BrowserSessionError::ManualResumeRequired
        );

        let resumed = session
            .apply(LoginSignal::OperatorResume)
            .expect("operator resume");
        assert_eq!(resumed.state, LoginState::AwaitingScan);
        assert_eq!(resumed.session_revision, 1);
    }
}

#[test]
fn qr_login_health_and_logout_have_explicit_states() {
    let mut session = QrLoginSession::new();
    assert_eq!(session.snapshot().state, LoginState::LoggedOut);
    session.apply(LoginSignal::BeginQr).expect("begin QR");
    session.apply(LoginSignal::QrScanned).expect("scan QR");
    let healthy = session
        .apply(LoginSignal::Authenticated)
        .expect("authenticated");
    assert_eq!(healthy.state, LoginState::Healthy);
    assert!(!healthy.circuit_open);
    let logged_out = session.apply(LoginSignal::Logout).expect("logout");
    assert_eq!(logged_out.state, LoginState::LoggedOut);
    assert!(logged_out.circuit_open);
    assert_eq!(logged_out.session_revision, 1);
}

#[cfg(unix)]
#[test]
fn browser_profile_rejects_symlinked_storage_parent() {
    use std::os::unix::fs::symlink;

    let root = tempdir().expect("temp root");
    let outside = tempdir().expect("outside root");
    symlink(outside.path(), root.path().join("social-operations")).expect("symlink storage parent");

    assert_eq!(
        BrowserProfile::prepare(root.path(), SocialPlatform::Douyin, ACCOUNT_ID)
            .expect_err("symlinked parent must fail"),
        BrowserSessionError::InvalidRoot
    );
    assert!(!outside.path().join("browser-profiles").exists());
}
