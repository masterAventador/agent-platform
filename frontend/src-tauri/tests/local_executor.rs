use agent_platform_desktop::local_executor::{
    authenticate_sidecar_request, new_session_token, run_sidecar_io, LocalExecutorError,
};
use serde_json::json;
use std::io::Cursor;

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
