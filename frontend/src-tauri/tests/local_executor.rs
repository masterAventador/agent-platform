use agent_platform_desktop::local_executor::{
    authenticate_sidecar_request, new_session_token, run_sidecar_io, LocalExecutorError,
    LocalExecutorManager,
};
use agent_platform_desktop::sidecar_package::VerifiedSidecarExecutable;
use serde_json::json;
use std::io::Cursor;
use std::time::{Duration, Instant};

#[cfg(unix)]
static PROCESS_TEST_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());

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
fn wait_for_pids(path: &std::path::Path, count: usize) -> Vec<i32> {
    let deadline = Instant::now() + Duration::from_secs(3);
    loop {
        let pids = std::fs::read_to_string(path)
            .unwrap_or_default()
            .lines()
            .filter_map(|line| line.parse::<i32>().ok())
            .collect::<Vec<_>>();
        if pids.len() >= count {
            return pids;
        }
        assert!(
            Instant::now() < deadline,
            "sidecar did not report child pids"
        );
        std::thread::sleep(Duration::from_millis(20));
    }
}

#[cfg(unix)]
fn process_exists(pid: i32) -> bool {
    let result = unsafe { libc::kill(pid, 0) };
    result == 0 || std::io::Error::last_os_error().raw_os_error() != Some(libc::ESRCH)
}

#[cfg(unix)]
fn assert_processes_exit_and_cleanup(pids: &[i32]) {
    let deadline = Instant::now() + Duration::from_secs(1);
    let all_exited = loop {
        if pids.iter().all(|pid| !process_exists(*pid)) {
            break true;
        }
        if Instant::now() >= deadline {
            break false;
        }
        std::thread::sleep(Duration::from_millis(20));
    };
    for pid in pids {
        if process_exists(*pid) {
            unsafe { libc::kill(*pid, libc::SIGKILL) };
        }
    }
    assert!(
        all_exited,
        "sidecar descendant processes must be terminated"
    );
}

#[cfg(unix)]
#[test]
fn installed_sidecar_is_restarted_only_twice_after_real_process_crashes() {
    let _process_guard = PROCESS_TEST_LOCK.lock().expect("process test lock");
    let launches = tempfile::NamedTempFile::new().expect("launch counter");
    let script = executable_script(&format!(
        "#!/bin/sh\nread token\necho launch >> '{}'\nexit 1\n",
        launches.path().display()
    ));
    let manager = LocalExecutorManager::default();
    manager.start_installed(&script).expect("start installed");

    let deadline = Instant::now() + Duration::from_secs(3);
    loop {
        std::thread::sleep(Duration::from_millis(20));
        let launch_count = std::fs::read_to_string(launches.path())
            .expect("read launch counter")
            .lines()
            .count();
        if launch_count == 3 {
            break;
        }
        assert!(
            Instant::now() < deadline,
            "background watchdog did not restart without manager polling"
        );
    }
    std::thread::sleep(Duration::from_millis(100));
    assert_eq!(
        std::fs::read_to_string(launches.path())
            .expect("final launch counter")
            .lines()
            .count(),
        3,
        "initial launch plus exactly two restarts"
    );
    assert!(!manager.status_snapshot().expect("bounded stop").running);
}

#[cfg(unix)]
#[test]
fn hung_sidecar_call_times_out_and_is_terminated() {
    let _process_guard = PROCESS_TEST_LOCK.lock().expect("process test lock");
    let script = executable_script("#!/bin/sh\nread token\nwhile read line; do sleep 30; done\n");
    let manager = LocalExecutorManager::default();
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
    let _process_guard = PROCESS_TEST_LOCK.lock().expect("process test lock");
    let script = executable_script(
        "#!/bin/sh\nread token\necho 'Cookie: sid=private Set-Cookie: auth=hidden password=hunter2 /var/folders/zz/alice/file' >&2\nwhile read line; do echo '{\"ok\":true}'; done\n",
    );
    let manager = LocalExecutorManager::default();
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
    for secret in ["private", "hidden", "hunter2", "alice"] {
        assert!(!diagnostics[0].contains(secret));
    }
}

#[cfg(unix)]
#[test]
fn retained_diagnostics_are_bounded_by_line_count_total_bytes_and_line_bytes() {
    let _process_guard = PROCESS_TEST_LOCK.lock().expect("process test lock");
    let script = executable_script(
        "#!/bin/sh\nread token\ni=0\nwhile [ $i -lt 400 ]; do printf 'Cookie: secret-%s ' \"$i\" >&2; head -c 5000 /dev/zero | tr '\\0' x >&2; printf '\\n' >&2; i=$((i + 1)); done\nwhile read line; do echo '{\"ok\":true}'; done\n",
    );
    let manager = LocalExecutorManager::default();
    manager.start_installed(&script).expect("start installed");
    manager
        .invoke_with_timeout(
            json!({"protocol_version": "1.0", "message_type": "task.request"}),
            Duration::from_secs(5),
        )
        .expect("invoke");
    std::thread::sleep(Duration::from_millis(300));
    let diagnostics = manager.take_safe_diagnostics();
    manager.stop().expect("stop");

    assert!(!diagnostics.is_empty());
    assert!(diagnostics.len() <= 200);
    assert!(diagnostics.iter().all(|line| line.len() <= 4096));
    assert!(diagnostics.iter().map(String::len).sum::<usize>() <= 64 * 1024);
    assert!(diagnostics.iter().all(|line| !line.contains("secret-")));
}

#[cfg(unix)]
#[test]
fn stop_terminates_the_sidecar_process_tree() {
    let _process_guard = PROCESS_TEST_LOCK.lock().expect("process test lock");
    let pids = tempfile::NamedTempFile::new().expect("child pid file");
    let script = executable_script(&format!(
        "#!/bin/sh\nread token\nsleep 30 &\necho $! >> '{}'\nwhile read line; do echo '{{\"ok\":true}}'; done\n",
        pids.path().display()
    ));
    let manager = LocalExecutorManager::default();
    manager.start_installed(&script).expect("start installed");
    let child_pids = wait_for_pids(pids.path(), 1);

    manager.stop().expect("stop process tree");

    assert_processes_exit_and_cleanup(&child_pids);
}

#[cfg(unix)]
#[test]
fn timeout_terminates_the_sidecar_process_tree() {
    let _process_guard = PROCESS_TEST_LOCK.lock().expect("process test lock");
    let pids = tempfile::NamedTempFile::new().expect("child pid file");
    let script = executable_script(&format!(
        "#!/bin/sh\nread token\nsleep 30 &\necho $! >> '{}'\nwhile read line; do sleep 30; done\n",
        pids.path().display()
    ));
    let manager = LocalExecutorManager::default();
    manager.start_installed(&script).expect("start installed");
    let child_pids = wait_for_pids(pids.path(), 1);

    assert_eq!(
        manager.invoke_with_timeout(
            json!({"protocol_version": "1.0", "message_type": "task.request"}),
            Duration::from_millis(80),
        ),
        Err(LocalExecutorError::InvocationTimedOut)
    );

    assert_processes_exit_and_cleanup(&child_pids);
}

#[cfg(unix)]
#[test]
fn crash_recovery_terminates_each_abandoned_sidecar_process_tree() {
    let _process_guard = PROCESS_TEST_LOCK.lock().expect("process test lock");
    let pids = tempfile::NamedTempFile::new().expect("child pid file");
    let script = executable_script(&format!(
        "#!/bin/sh\nread token\nsleep 30 &\necho $! >> '{}'\nexit 1\n",
        pids.path().display()
    ));
    let manager = LocalExecutorManager::default();
    manager.start_installed(&script).expect("start installed");
    let child_pids = wait_for_pids(pids.path(), 3);
    let deadline = Instant::now() + Duration::from_secs(3);
    while manager.status_snapshot().expect("status").running {
        assert!(Instant::now() < deadline, "recovery did not stop");
        std::thread::sleep(Duration::from_millis(20));
    }

    assert_processes_exit_and_cleanup(&child_pids);
}

#[cfg(unix)]
#[test]
fn stop_preempts_a_blocked_inflight_invocation() {
    let _process_guard = PROCESS_TEST_LOCK.lock().expect("process test lock");
    let request_seen = tempfile::NamedTempFile::new().expect("request marker");
    std::fs::remove_file(request_seen.path()).expect("clear marker");
    let script = executable_script(&format!(
        "#!/bin/sh\nread token\nwhile read line; do touch '{}'; sleep 30; done\n",
        request_seen.path().display()
    ));
    let manager = LocalExecutorManager::default();
    manager.start_installed(&script).expect("start installed");
    let invoking_manager = manager.clone();
    let invocation = std::thread::spawn(move || {
        invoking_manager.invoke_with_timeout(
            json!({"protocol_version": "1.0", "message_type": "task.request"}),
            Duration::from_secs(30),
        )
    });
    let deadline = Instant::now() + Duration::from_secs(3);
    while !request_seen.path().exists() {
        assert!(Instant::now() < deadline, "sidecar did not receive request");
        std::thread::sleep(Duration::from_millis(10));
    }

    let stop_started = Instant::now();
    manager.stop().expect("preemptive stop");
    let stop_elapsed = stop_started.elapsed();
    let invocation_result = invocation.join().expect("invocation thread");

    assert!(
        stop_elapsed < Duration::from_millis(500),
        "emergency stop took {stop_elapsed:?}"
    );
    assert!(
        matches!(
            invocation_result,
            Err(LocalExecutorError::IpcUnavailable | LocalExecutorError::NotRunning)
        ),
        "in-flight invocation must fail safely: {invocation_result:?}"
    );
}

#[cfg(unix)]
#[test]
fn stop_preempts_a_blocked_invocation_after_crash_recovery() {
    let _process_guard = PROCESS_TEST_LOCK.lock().expect("process test lock");
    let launches = tempfile::NamedTempFile::new().expect("launch counter");
    let request_seen = tempfile::NamedTempFile::new().expect("request marker");
    std::fs::remove_file(request_seen.path()).expect("clear marker");
    let script = executable_script(&format!(
        "#!/bin/sh\nread token\ncount=$(wc -l < '{}')\necho launch >> '{}'\nif [ \"$count\" -eq 0 ]; then exit 1; fi\nwhile read line; do touch '{}'; sleep 30; done\n",
        launches.path().display(),
        launches.path().display(),
        request_seen.path().display(),
    ));
    let manager = LocalExecutorManager::default();
    manager.start_installed(&script).expect("start installed");
    let deadline = Instant::now() + Duration::from_secs(3);
    while std::fs::read_to_string(launches.path())
        .unwrap_or_default()
        .lines()
        .count()
        < 2
    {
        assert!(Instant::now() < deadline, "sidecar did not recover");
        std::thread::sleep(Duration::from_millis(10));
    }
    let invoking_manager = manager.clone();
    let invocation = std::thread::spawn(move || {
        invoking_manager.invoke_with_timeout(
            json!({"protocol_version": "1.0", "message_type": "task.request"}),
            Duration::from_secs(2),
        )
    });
    while !request_seen.path().exists() {
        assert!(
            Instant::now() < deadline,
            "recovered sidecar did not receive request"
        );
        std::thread::sleep(Duration::from_millis(10));
    }

    let stop_started = Instant::now();
    manager.stop().expect("preempt recovered process");
    let stop_elapsed = stop_started.elapsed();
    let invocation_result = invocation.join().expect("invocation thread");

    assert!(
        stop_elapsed < Duration::from_millis(500),
        "recovered sidecar stop took {stop_elapsed:?}"
    );
    assert!(
        matches!(
            invocation_result,
            Err(LocalExecutorError::IpcUnavailable | LocalExecutorError::NotRunning)
        ),
        "recovered in-flight invocation must fail safely: {invocation_result:?}"
    );
}

#[cfg(unix)]
#[test]
fn verified_launch_executes_the_verified_bytes_not_a_replaced_path() {
    use std::os::unix::fs::PermissionsExt;
    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::sync::Arc;

    let _process_guard = PROCESS_TEST_LOCK.lock().expect("process test lock");
    let marker = tempfile::NamedTempFile::new().expect("marker path");
    std::fs::remove_file(marker.path()).expect("clear marker");
    let executable = executable_script(
        "#!/bin/sh\nread token\nwhile read line; do echo '{\"ok\":true}'; done\n",
    );
    let executable_path = executable.to_path_buf();
    let replacement = format!(
        "#!/bin/sh\nread token\ntouch '{}'\nwhile read line; do echo '{{\"ok\":true}}'; done\n",
        marker.path().display()
    );
    let verifier_calls = Arc::new(AtomicUsize::new(0));
    let calls = Arc::clone(&verifier_calls);
    let verifier_path = executable_path.clone();
    let manager = LocalExecutorManager::default();

    manager
        .start_verified(move || {
            let verified = std::fs::read(&verifier_path)
                .map_err(|_| LocalExecutorError::LaunchVerificationFailed)?;
            if !verified.starts_with(b"#!/bin/sh") {
                return Err(LocalExecutorError::LaunchVerificationFailed);
            }
            if calls.fetch_add(1, Ordering::SeqCst) == 1 {
                std::fs::write(&verifier_path, replacement.as_bytes())
                    .map_err(|_| LocalExecutorError::LaunchVerificationFailed)?;
                std::fs::set_permissions(&verifier_path, std::fs::Permissions::from_mode(0o700))
                    .map_err(|_| LocalExecutorError::LaunchVerificationFailed)?;
            }
            let file = std::fs::File::open(&verifier_path)
                .map_err(|_| LocalExecutorError::LaunchVerificationFailed)?;
            Ok(VerifiedSidecarExecutable {
                path: verifier_path.clone(),
                file,
                contents: verified,
            })
        })
        .expect("start verified sidecar");
    manager
        .invoke_with_timeout(
            json!({"protocol_version": "1.0", "message_type": "task.request"}),
            Duration::from_secs(1),
        )
        .expect("verified sidecar response");
    manager.stop().expect("stop sidecar");

    assert!(
        !marker.path().exists(),
        "a replacement made after verification must never execute"
    );
}
