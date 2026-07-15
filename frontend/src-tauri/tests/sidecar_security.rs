use agent_platform_desktop::sidecar_package::{
    redact_log_line, CrashRecoveryAction, CrashRecoveryPolicy, SidecarPackageError,
    TrustedSidecarInstaller,
};
use ed25519_dalek::{Signer, SigningKey};
use std::io::{Read, Write};
use std::net::TcpListener;
use std::thread;
use tempfile::tempdir;

fn signed_package() -> (SigningKey, Vec<u8>, Vec<u8>) {
    let signing_key = SigningKey::from_bytes(&[7_u8; 32]);
    let package = b"signed social operations sidecar".to_vec();
    let signature = signing_key.sign(&package).to_bytes().to_vec();
    (signing_key, package, signature)
}

#[test]
fn downloaded_sidecar_is_installed_only_after_trusted_signature_verification() {
    let (signing_key, package, signature) = signed_package();
    let listener = TcpListener::bind("127.0.0.1:0").expect("bind loopback");
    let address = listener.local_addr().expect("listener address");
    let response_body = package.clone();
    let server = thread::spawn(move || {
        let (mut stream, _) = listener.accept().expect("accept request");
        let mut request = [0_u8; 1024];
        let _ = stream.read(&mut request).expect("read request");
        write!(
            stream,
            "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
            response_body.len()
        )
        .expect("write headers");
        stream.write_all(&response_body).expect("write package");
    });
    let root = tempdir().expect("temp root");
    let installer =
        TrustedSidecarInstaller::new(root.path(), signing_key.verifying_key().to_bytes(), 1024)
            .expect("installer");

    let installed = installer
        .download_and_install(&format!("http://{address}/sidecar"), "1.2.3", &signature)
        .expect("verified install");
    server.join().expect("server join");

    assert_eq!(std::fs::read(&installed).expect("installed bytes"), package);
    assert!(installed.starts_with(root.path()));
}

#[test]
fn invalid_signature_leaves_no_executable_or_staging_file() {
    let (signing_key, package, _) = signed_package();
    let root = tempdir().expect("temp root");
    let installer =
        TrustedSidecarInstaller::new(root.path(), signing_key.verifying_key().to_bytes(), 1024)
            .expect("installer");

    let error = installer
        .install_bytes("1.2.3", &package, &[0_u8; 64])
        .expect_err("signature must fail");

    assert_eq!(error, SidecarPackageError::SignatureInvalid);
    assert!(!root.path().join("sidecars/1.2.3").exists());
}

#[test]
fn download_rejects_untrusted_transport_and_oversized_package() {
    let (signing_key, package, signature) = signed_package();
    let root = tempdir().expect("temp root");
    let installer = TrustedSidecarInstaller::new(
        root.path(),
        signing_key.verifying_key().to_bytes(),
        package.len() - 1,
    )
    .expect("installer");

    assert_eq!(
        installer
            .download_and_install("http://example.com/sidecar", "1.2.3", &signature)
            .expect_err("public HTTP must fail"),
        SidecarPackageError::UntrustedDownloadUrl
    );
    assert_eq!(
        installer
            .install_bytes("1.2.3", &package, &signature)
            .expect_err("oversized package must fail"),
        SidecarPackageError::PackageTooLarge
    );
}

#[test]
fn crash_recovery_is_bounded_and_clean_stop_never_restarts() {
    let mut policy = CrashRecoveryPolicy::new(2);
    assert_eq!(policy.on_unexpected_exit(), CrashRecoveryAction::Restart);
    assert_eq!(policy.on_unexpected_exit(), CrashRecoveryAction::Restart);
    assert_eq!(policy.on_unexpected_exit(), CrashRecoveryAction::Stop);
    policy.reset_after_stable_start();
    assert_eq!(policy.on_unexpected_exit(), CrashRecoveryAction::Restart);
    assert_eq!(policy.on_clean_stop(), CrashRecoveryAction::Stop);
}

#[test]
fn executor_logs_redact_credentials_and_private_paths() {
    let safe = redact_log_line(
        "Bearer abc123 cookie=session-value password=hunter2 /Users/alice/profile token=xyz",
    );

    for secret in ["abc123", "session-value", "hunter2", "alice", "xyz"] {
        assert!(!safe.contains(secret));
    }
    assert!(safe.contains("[REDACTED]"));
}

#[cfg(unix)]
#[test]
fn installer_rejects_symlinked_sidecar_directory_without_writing_outside_root() {
    use std::os::unix::fs::symlink;

    let (signing_key, package, signature) = signed_package();
    let root = tempdir().expect("temp root");
    let outside = tempdir().expect("outside root");
    symlink(outside.path(), root.path().join("sidecars")).expect("symlink sidecars");
    let installer =
        TrustedSidecarInstaller::new(root.path(), signing_key.verifying_key().to_bytes(), 1024)
            .expect("installer");

    assert_eq!(
        installer
            .install_bytes("1.2.3", &package, &signature)
            .expect_err("symlinked root must fail"),
        SidecarPackageError::InvalidRoot
    );
    assert!(!outside.path().join("1.2.3").exists());
}
