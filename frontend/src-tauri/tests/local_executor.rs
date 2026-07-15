use agent_platform_desktop::local_executor::{
    authenticate_sidecar_request, new_session_token, run_sidecar_io, LocalExecutorError,
    LocalExecutorManager,
};
use serde_json::json;
use std::io::Cursor;
use std::time::{Duration, Instant};

#[test]
fn authenticated_ipc_accepts_only_matching_random_session_token() {
    let token = new_session_token().expect("session token");
    assert_eq!(token.len(), 64);
    assert!(token.bytes().all(|byte| byte.is_ascii_hexdigit()));

    let request = json!({
        "protocol_version": "1.0",
        "message_type": "task.request"
    });
    assert_eq!(
        authenticate_sidecar_request(&token, &token, &request),
        Ok("task.request".to_owned())
    );
    assert_eq!(
        authenticate_sidecar_request(&token, &"0".repeat(64), &request),
        Err(LocalExecutorError::AuthenticationFailed)
    );
}

#[test]
fn authenticated_ipc_rejects_unknown_protocol_without_reflecting_payload() {
    let request = json!({
        "protocol_version": "2.0",
        "message_type": "task.request",
        "secret": "must-not-appear-in-errors"
    });

    assert_eq!(
        authenticate_sidecar_request("a", "a", &request),
        Err(LocalExecutorError::UnsupportedProtocol)
    );
    assert!(!format!("{:?}", LocalExecutorError::UnsupportedProtocol).contains("secret"));
}

#[test]
fn sidecar_stdio_session_returns_a_versioned_non_reflective_acknowledgement() {
    let token = "a".repeat(64);
    let transcript = format!(
        "{token}\n{{\"protocol_version\":\"1.0\",\"session_token\":\"{token}\",\"request\":{{\"protocol_version\":\"1.0\",\"message_type\":\"task.request\",\"secret\":\"private\"}}}}\n"
    );
    let mut output = Vec::new();

    run_sidecar_io(Cursor::new(transcript), &mut output).expect("sidecar transcript");

    let response: serde_json::Value =
        serde_json::from_slice(&output).expect("sidecar JSON response");
    assert_eq!(response["ok"], true);
    assert_eq!(response["protocol_version"], "1.0");
    assert_eq!(response["message_type"], "task.request");
    assert!(!String::from_utf8(output).expect("utf8").contains("private"));
}

#[cfg(unix)]
fn executable_script(contents: &str) -> tempfile::TempPath {
    use std::io::Write;
    use std::os::unix::fs::PermissionsExt;

    let mut file = tempfile::NamedTempFile::new().expect("script");
    file.write_all(contents.as_bytes()).expect("write script");
    std::fs::set_permissions(file.path(), std::fs::Permissions::from_mode(0o700))
        .expect("script permissions");
    file.into_temp_path()
}

#[cfg(unix)]
#[test]
fn installed_sidecar_is_restarted_only_twice_after_real_process_crashes() {
    let script = executable_script("#!/bin/sh\nread token\nexit 1\n");
    let mut manager = LocalExecutorManager::default();
    manager.start_installed(&script).expect("start installed");

    let deadline = Instant::now() + Duration::from_secs(3);
    loop {
        std::thread::sleep(Duration::from_millis(20));
        if !manager.status_snapshot().expect("watchdog tick").running {
            break;
        }
        assert!(
            Instant::now() < deadline,
            "bounded crash recovery did not stop"
        );
    }
    assert!(!manager.status_snapshot().expect("bounded stop").running);
}

#[cfg(unix)]
#[test]
fn hung_sidecar_call_times_out_and_is_terminated() {
    let script = executable_script("#!/bin/sh\nread token\nwhile read line; do sleep 30; done\n");
    let mut manager = LocalExecutorManager::default();
    manager.start_installed(&script).expect("start installed");

    assert_eq!(
        manager
            .invoke_with_timeout(
                json!({"protocol_version": "1.0", "message_type": "task.request"}),
                Duration::from_millis(80),
            )
            .expect_err("hung call must fail"),
        LocalExecutorError::InvocationTimedOut
    );
    assert!(!manager.status_snapshot().expect("stopped").running);
}

#[cfg(unix)]
#[test]
fn real_sidecar_stderr_is_redacted_before_diagnostics_are_retained() {
    let script = executable_script(
        "#!/bin/sh\nread token\necho 'Set-Cookie: sid=private password=hunter2 /Users/alice/file' >&2\nwhile read line; do echo '{\"ok\":true}'; done\n",
    );
    let mut manager = LocalExecutorManager::default();
    manager.start_installed(&script).expect("start installed");
    manager
        .invoke_with_timeout(
            json!({"protocol_version": "1.0", "message_type": "task.request"}),
            Duration::from_secs(5),
        )
        .expect("sidecar acknowledgement synchronizes stderr production");
    let mut diagnostics = Vec::new();
    for _ in 0..20 {
        std::thread::sleep(Duration::from_millis(20));
        diagnostics.extend(manager.take_safe_diagnostics());
        if !diagnostics.is_empty() {
            break;
        }
    }
    manager.stop().expect("stop");

    assert_eq!(diagnostics.len(), 1);
    for secret in ["private", "hunter2", "alice"] {
        assert!(!diagnostics[0].contains(secret));
    }
}
