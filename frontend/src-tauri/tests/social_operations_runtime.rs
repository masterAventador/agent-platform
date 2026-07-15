use agent_platform_desktop::browser_session::{LoginSignal, LoginState};
use agent_platform_desktop::local_executor::LocalExecutorManager;
use agent_platform_desktop::sidecar_package::SignedSidecarManifest;
use agent_platform_desktop::social_operations_runtime::SocialOperationsRuntime;
use agent_platform_desktop::social_operations_runtime::SocialRuntimeError;
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
    let mut executor = LocalExecutorManager::default();

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
        .start_account(ACCOUNT_ID, &mut executor)
        .expect("healthy account starts installed sidecar");
    assert!(executor.status_snapshot().expect("status").running);
    assert_eq!(
        runtime
            .invoke_account(
                ACCOUNT_ID,
                &mut executor,
                json!({"protocol_version": "1.0", "message_type": "task.request"}),
                Duration::from_secs(1),
            )
            .expect("invoke")["ok"],
        true
    );

    runtime
        .logout_account(ACCOUNT_ID, &mut executor)
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
    let mut executor = LocalExecutorManager::default();
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
    runtime
        .start_account(ACCOUNT_ID, &mut executor)
        .expect("start");

    runtime
        .emergency_stop(ACCOUNT_ID, &mut executor)
        .expect("emergency stop");

    assert!(!executor.status_snapshot().expect("stopped").running);
    let snapshot = runtime.account_snapshot(ACCOUNT_ID).expect("snapshot");
    assert_eq!(snapshot.state, LoginState::HumanHandoff);
    assert!(snapshot.circuit_open);
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
    let mut executor = LocalExecutorManager::default();
    runtime
        .install_manifest(&manifest, &package, &signature)
        .expect("signed install");
    runtime
        .prepare_account("douyin", ACCOUNT_ID)
        .expect("profile");

    assert_eq!(
        runtime
            .start_account(ACCOUNT_ID, &mut executor)
            .expect_err("awaiting scan must not execute"),
        SocialRuntimeError::AccountNotHealthy
    );
    assert!(!executor.status_snapshot().expect("stopped").running);
}
