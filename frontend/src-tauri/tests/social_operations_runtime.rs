use agent_platform_desktop::browser_session::{LoginSignal, LoginState};
use agent_platform_desktop::local_executor::LocalExecutorManager;
use agent_platform_desktop::sidecar_package::SignedSidecarManifest;
use agent_platform_desktop::social_operations_runtime::{
    emergency_stop_managed_account, invoke_managed_account, SocialOperationsRuntime,
    SocialRuntimeError,
};
use ed25519_dalek::{Signer, SigningKey};
use serde_json::json;
use std::time::Duration;
use tempfile::tempdir;

const ACCOUNT_ID: &str = "018f4c54-2f7b-7f6a-9f5a-8f8f99547001";

#[cfg(unix)]
fn signed_sidecar() -> (SigningKey, Vec<u8>, SignedSidecarManifest, Vec<u8>) {
    let signing_key = SigningKey::from_bytes(&[11_u8; 32]);
    let package = b"#!/bin/sh\nread token\nwhile IFS= read -r line; do echo '{\"ok\":true,\"protocol_version\":\"1.0\"}'; done\n".to_vec();
    let manifest = SignedSidecarManifest::for_current("3.1.0", &package).expect("manifest");
    let signature = signing_key
        .sign(&manifest.signing_bytes())
        .to_bytes()
        .to_vec();
    (signing_key, package, manifest, signature)
}

#[cfg(unix)]
#[test]
fn signed_install_to_healthy_execution_and_logout_is_one_closed_loop() {
    let (signing_key, package, manifest, signature) = signed_sidecar();
    let root = tempdir().expect("runtime root");
    let mut runtime = SocialOperationsRuntime::new(
        root.path(),
        Some(signing_key.verifying_key().to_bytes()),
        1024 * 1024,
    )
    .expect("runtime");
    let executor = LocalExecutorManager::default();

    runtime
        .install_manifest(&manifest, &package, &signature)
        .expect("signed install");
    let profile = runtime
        .prepare_account("douyin", ACCOUNT_ID)
        .expect("profile");
    runtime
        .store_cookies(ACCOUNT_ID, b"sid=private")
        .expect("cookie store");
    for signal in [
        LoginSignal::BeginQr,
        LoginSignal::QrScanned,
        LoginSignal::Authenticated,
    ] {
        runtime
            .apply_login_signal(ACCOUNT_ID, signal)
            .expect("login");
    }

    runtime
        .start_account(ACCOUNT_ID, &executor)
        .expect("healthy account starts installed sidecar");
    assert!(executor.status_snapshot().expect("status").running);
    assert_eq!(
        runtime
            .invoke_account(
                ACCOUNT_ID,
                &executor,
                json!({"protocol_version": "1.0", "message_type": "task.request"}),
                Duration::from_secs(1),
            )
            .expect("invoke")["ok"],
        true
    );

    runtime
        .logout_account(ACCOUNT_ID, &executor)
        .expect("logout");
    assert!(!executor.status_snapshot().expect("stopped").running);
    assert!(!profile.exists());
    assert_eq!(runtime.load_cookies(ACCOUNT_ID).expect("load"), None);
    let snapshot = runtime.account_snapshot(ACCOUNT_ID).expect("snapshot");
    assert_eq!(snapshot.state, LoginState::LoggedOut);
    assert!(snapshot.circuit_open);
}

#[cfg(unix)]
#[test]
fn emergency_stop_terminates_execution_and_circuits_the_account() {
    let (signing_key, package, manifest, signature) = signed_sidecar();
    let root = tempdir().expect("runtime root");
    let mut runtime = SocialOperationsRuntime::new(
        root.path(),
        Some(signing_key.verifying_key().to_bytes()),
        1024 * 1024,
    )
    .expect("runtime");
    let executor = LocalExecutorManager::default();
    runtime
        .install_manifest(&manifest, &package, &signature)
        .expect("signed install");
    runtime
        .prepare_account("douyin", ACCOUNT_ID)
        .expect("profile");
    for signal in [
        LoginSignal::BeginQr,
        LoginSignal::QrScanned,
        LoginSignal::Authenticated,
    ] {
        runtime
            .apply_login_signal(ACCOUNT_ID, signal)
            .expect("login");
    }
    runtime.start_account(ACCOUNT_ID, &executor).expect("start");
    runtime
        .apply_login_signal(ACCOUNT_ID, LoginSignal::RiskControl)
        .expect("state enters handoff before emergency cleanup");
    assert!(
        executor.status_snapshot().expect("still active").running,
        "the test must exercise emergency cleanup after the state already changed"
    );

    runtime
        .emergency_stop(ACCOUNT_ID, &executor)
        .expect("emergency stop");

    assert!(!executor.status_snapshot().expect("stopped").running);
    let snapshot = runtime.account_snapshot(ACCOUNT_ID).expect("snapshot");
    assert_eq!(snapshot.state, LoginState::HumanHandoff);
    assert!(snapshot.circuit_open);

    runtime
        .emergency_stop(ACCOUNT_ID, &executor)
        .expect("repeated emergency stop is idempotent");
}

#[cfg(unix)]
#[test]
fn risk_captcha_and_expired_signals_circuit_and_stop_the_active_process() {
    for signal in [
        LoginSignal::RiskControl,
        LoginSignal::CaptchaRequired,
        LoginSignal::LoginExpired,
    ] {
        let (signing_key, package, manifest, signature) = signed_sidecar();
        let root = tempdir().expect("runtime root");
        let mut runtime = SocialOperationsRuntime::new(
            root.path(),
            Some(signing_key.verifying_key().to_bytes()),
            1024 * 1024,
        )
        .expect("runtime");
        let executor = LocalExecutorManager::default();
        runtime
            .install_manifest(&manifest, &package, &signature)
            .expect("signed install");
        runtime
            .prepare_account("douyin", ACCOUNT_ID)
            .expect("profile");
        for login_signal in [
            LoginSignal::BeginQr,
            LoginSignal::QrScanned,
            LoginSignal::Authenticated,
        ] {
            runtime
                .apply_login_signal(ACCOUNT_ID, login_signal)
                .expect("login");
        }
        runtime.start_account(ACCOUNT_ID, &executor).expect("start");

        runtime
            .apply_login_signal_with_executor(ACCOUNT_ID, signal, &executor)
            .expect("fail-safe signal");

        assert!(!executor.status_snapshot().expect("stopped").running);
        let snapshot = runtime.account_snapshot(ACCOUNT_ID).expect("snapshot");
        assert_eq!(snapshot.state, LoginState::HumanHandoff);
        assert!(snapshot.circuit_open);
    }
}

#[cfg(unix)]
#[test]
fn logout_then_prepare_recreates_profile_and_allows_a_new_qr_session() {
    let (signing_key, package, manifest, signature) = signed_sidecar();
    let root = tempdir().expect("runtime root");
    let mut runtime = SocialOperationsRuntime::new(
        root.path(),
        Some(signing_key.verifying_key().to_bytes()),
        1024 * 1024,
    )
    .expect("runtime");
    let executor = LocalExecutorManager::default();
    runtime
        .install_manifest(&manifest, &package, &signature)
        .expect("signed install");
    let original = runtime
        .prepare_account("douyin", ACCOUNT_ID)
        .expect("profile");
    runtime
        .apply_login_signal(ACCOUNT_ID, LoginSignal::BeginQr)
        .expect("begin qr");
    runtime
        .logout_account(ACCOUNT_ID, &executor)
        .expect("logout");
    assert!(!original.exists());

    let recreated = runtime
        .prepare_account("douyin", ACCOUNT_ID)
        .expect("reprepare");
    assert!(recreated.is_dir());
    assert_eq!(recreated, original);
    let snapshot = runtime
        .apply_login_signal(ACCOUNT_ID, LoginSignal::BeginQr)
        .expect("new QR session");
    assert_eq!(snapshot.state, LoginState::AwaitingScan);
}

#[cfg(unix)]
#[test]
fn a_new_runtime_recovers_the_verified_installed_sidecar_without_redownloading() {
    let (signing_key, package, manifest, signature) = signed_sidecar();
    let root = tempdir().expect("runtime root");
    {
        let mut runtime = SocialOperationsRuntime::new(
            root.path(),
            Some(signing_key.verifying_key().to_bytes()),
            1024 * 1024,
        )
        .expect("first runtime");
        runtime
            .install_manifest(&manifest, &package, &signature)
            .expect("signed install");
    }

    let mut recovered = SocialOperationsRuntime::new(
        root.path(),
        Some(signing_key.verifying_key().to_bytes()),
        1024 * 1024,
    )
    .expect("recovered runtime");
    let executor = LocalExecutorManager::default();
    recovered
        .prepare_account("douyin", ACCOUNT_ID)
        .expect("profile");
    for signal in [
        LoginSignal::BeginQr,
        LoginSignal::QrScanned,
        LoginSignal::Authenticated,
    ] {
        recovered
            .apply_login_signal(ACCOUNT_ID, signal)
            .expect("login");
    }
    recovered
        .start_account(ACCOUNT_ID, &executor)
        .expect("start recovered install");
    assert!(executor.status_snapshot().expect("status").running);
}

#[cfg(unix)]
#[test]
fn awaiting_scan_account_cannot_start_an_installed_sidecar() {
    let (signing_key, package, manifest, signature) = signed_sidecar();
    let root = tempdir().expect("runtime root");
    let mut runtime = SocialOperationsRuntime::new(
        root.path(),
        Some(signing_key.verifying_key().to_bytes()),
        1024 * 1024,
    )
    .expect("runtime");
    let executor = LocalExecutorManager::default();
    runtime
        .install_manifest(&manifest, &package, &signature)
        .expect("signed install");
    runtime
        .prepare_account("douyin", ACCOUNT_ID)
        .expect("profile");

    assert_eq!(
        runtime
            .start_account(ACCOUNT_ID, &executor)
            .expect_err("awaiting scan must not execute"),
        SocialRuntimeError::AccountNotHealthy
    );
    assert!(!executor.status_snapshot().expect("stopped").running);
}

#[cfg(unix)]
#[test]
fn runtime_start_fails_closed_if_the_verified_sidecar_is_replaced_after_install() {
    use std::os::unix::fs::PermissionsExt;

    let signing_key = SigningKey::from_bytes(&[12_u8; 32]);
    let package = b"#!/bin/sh\nread token\nwhile read line; do echo '{\"ok\":true}'; done\n";
    let manifest = SignedSidecarManifest::for_current("5.0.0", package).expect("manifest");
    let signature = signing_key
        .sign(&manifest.signing_bytes())
        .to_bytes()
        .to_vec();
    let root = tempdir().expect("runtime root");
    let marker = root.path().join("tampered-sidecar-ran");
    let mut runtime = SocialOperationsRuntime::new(
        root.path(),
        Some(signing_key.verifying_key().to_bytes()),
        1024 * 1024,
    )
    .expect("runtime");
    let installed = runtime
        .install_manifest(&manifest, package, &signature)
        .expect("signed install");
    std::fs::write(
        &installed,
        format!(
            "#!/bin/sh\nread token\ntouch '{}'\nwhile read line; do echo '{{\"ok\":true}}'; done\n",
            marker.display()
        ),
    )
    .expect("replace installed executable");
    std::fs::set_permissions(&installed, std::fs::Permissions::from_mode(0o700))
        .expect("replacement permissions");
    runtime
        .prepare_account("douyin", ACCOUNT_ID)
        .expect("profile");
    for signal in [
        LoginSignal::BeginQr,
        LoginSignal::QrScanned,
        LoginSignal::Authenticated,
    ] {
        runtime
            .apply_login_signal(ACCOUNT_ID, signal)
            .expect("login");
    }
    let executor = LocalExecutorManager::default();

    let start_result = runtime.start_account(ACCOUNT_ID, &executor);
    let _ = executor.stop();

    assert_eq!(start_result, Err(SocialRuntimeError::PackageRejected));
    assert!(
        !marker.exists(),
        "replaced executable must never be spawned"
    );
}

#[cfg(unix)]
#[test]
fn managed_emergency_stop_preempts_a_blocked_account_invocation() {
    use std::sync::{Arc, Mutex};
    use std::time::Instant;

    let signing_key = SigningKey::from_bytes(&[13_u8; 32]);
    let root = tempdir().expect("runtime root");
    let request_seen = root.path().join("request-seen");
    let package = format!(
        "#!/bin/sh\nread token\nwhile read line; do touch '{}'; sleep 30; done\n",
        request_seen.display()
    )
    .into_bytes();
    let manifest = SignedSidecarManifest::for_current("6.0.0", &package).expect("manifest");
    let signature = signing_key
        .sign(&manifest.signing_bytes())
        .to_bytes()
        .to_vec();
    let mut runtime = SocialOperationsRuntime::new(
        root.path(),
        Some(signing_key.verifying_key().to_bytes()),
        1024 * 1024,
    )
    .expect("runtime");
    runtime
        .install_manifest(&manifest, &package, &signature)
        .expect("install");
    runtime
        .prepare_account("douyin", ACCOUNT_ID)
        .expect("profile");
    for signal in [
        LoginSignal::BeginQr,
        LoginSignal::QrScanned,
        LoginSignal::Authenticated,
    ] {
        runtime
            .apply_login_signal(ACCOUNT_ID, signal)
            .expect("login");
    }
    let executor = LocalExecutorManager::default();
    runtime.start_account(ACCOUNT_ID, &executor).expect("start");
    let runtime = Arc::new(Mutex::new(runtime));
    let invoking_runtime = Arc::clone(&runtime);
    let invoking_executor = executor.clone();
    let invocation = std::thread::spawn(move || {
        invoke_managed_account(
            &invoking_runtime,
            &invoking_executor,
            ACCOUNT_ID,
            json!({"protocol_version": "1.0", "message_type": "task.request"}),
            Duration::from_secs(30),
        )
    });
    let deadline = Instant::now() + Duration::from_secs(3);
    while !request_seen.exists() {
        assert!(Instant::now() < deadline, "sidecar did not receive request");
        std::thread::sleep(Duration::from_millis(10));
    }

    let stop_started = Instant::now();
    emergency_stop_managed_account(&runtime, &executor, ACCOUNT_ID).expect("emergency stop");
    let stop_elapsed = stop_started.elapsed();
    let invocation_result = invocation.join().expect("invocation thread");

    assert!(stop_elapsed < Duration::from_millis(500));
    assert_eq!(
        invocation_result,
        Err(SocialRuntimeError::ExecutorUnavailable)
    );
    let snapshot = runtime
        .lock()
        .expect("runtime lock")
        .account_snapshot(ACCOUNT_ID)
        .expect("snapshot");
    assert_eq!(snapshot.state, LoginState::HumanHandoff);
    assert!(snapshot.circuit_open);
}
