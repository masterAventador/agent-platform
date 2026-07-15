use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::io::{BufRead, BufReader, Write};
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};
use std::sync::Mutex;
use tauri::State;

const PROTOCOL_VERSION: &str = "1.0";
const CAPABILITY_ID: &str = "social-operations";
const SIDECAR_ARGUMENT: &str = "--social-operations-sidecar";
const MAX_MESSAGE_BYTES: usize = 1024 * 1024;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum LocalExecutorError {
    InvalidCapability,
    InvalidRequest,
    UnsupportedProtocol,
    AuthenticationFailed,
    AlreadyRunning,
    NotRunning,
    ProcessUnavailable,
    IpcUnavailable,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct LocalExecutorStatus {
    running: bool,
    protocol_version: &'static str,
    capability_id: &'static str,
}

#[derive(Deserialize)]
struct SidecarEnvelope {
    protocol_version: String,
    session_token: String,
    request: Value,
}

struct RunningSidecar {
    child: Child,
    stdin: ChildStdin,
    stdout: BufReader<ChildStdout>,
    session_token: String,
}

#[derive(Default)]
pub struct LocalExecutorManager {
    sidecar: Option<RunningSidecar>,
}

impl Drop for LocalExecutorManager {
    fn drop(&mut self) {
        let _ = self.stop();
    }
}

impl LocalExecutorManager {
    fn refresh(&mut self) -> Result<(), LocalExecutorError> {
        let exited = match self.sidecar.as_mut() {
            Some(sidecar) => sidecar
                .child
                .try_wait()
                .map_err(|_| LocalExecutorError::ProcessUnavailable)?
                .is_some(),
            None => false,
        };
        if exited {
            self.sidecar = None;
        }
        Ok(())
    }

    fn status(&mut self) -> Result<LocalExecutorStatus, LocalExecutorError> {
        self.refresh()?;
        Ok(LocalExecutorStatus {
            running: self.sidecar.is_some(),
            protocol_version: PROTOCOL_VERSION,
            capability_id: CAPABILITY_ID,
        })
    }

    fn start(&mut self) -> Result<LocalExecutorStatus, LocalExecutorError> {
        self.refresh()?;
        if self.sidecar.is_some() {
            return Err(LocalExecutorError::AlreadyRunning);
        }
        let session_token = new_session_token()?;
        let executable =
            std::env::current_exe().map_err(|_| LocalExecutorError::ProcessUnavailable)?;
        let mut child = Command::new(executable)
            .arg(SIDECAR_ARGUMENT)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .map_err(|_| LocalExecutorError::ProcessUnavailable)?;
        let stdin = child.stdin.take();
        let stdout = child.stdout.take();
        let (Some(mut stdin), Some(stdout)) = (stdin, stdout) else {
            let _ = child.kill();
            let _ = child.wait();
            return Err(LocalExecutorError::IpcUnavailable);
        };
        if writeln!(stdin, "{session_token}").is_err() || stdin.flush().is_err() {
            let _ = child.kill();
            let _ = child.wait();
            return Err(LocalExecutorError::IpcUnavailable);
        }
        self.sidecar = Some(RunningSidecar {
            child,
            stdin,
            stdout: BufReader::new(stdout),
            session_token,
        });
        self.status()
    }

    fn invoke(&mut self, request: Value) -> Result<Value, LocalExecutorError> {
        self.refresh()?;
        let sidecar = self
            .sidecar
            .as_mut()
            .ok_or(LocalExecutorError::NotRunning)?;
        authenticate_sidecar_request(&sidecar.session_token, &sidecar.session_token, &request)?;
        let envelope = json!({
            "protocol_version": PROTOCOL_VERSION,
            "session_token": sidecar.session_token,
            "request": request,
        });
        let serialized =
            serde_json::to_string(&envelope).map_err(|_| LocalExecutorError::InvalidRequest)?;
        if serialized.len() > MAX_MESSAGE_BYTES {
            return Err(LocalExecutorError::InvalidRequest);
        }
        writeln!(sidecar.stdin, "{serialized}")
            .and_then(|_| sidecar.stdin.flush())
            .map_err(|_| LocalExecutorError::IpcUnavailable)?;

        let mut response = String::new();
        sidecar
            .stdout
            .read_line(&mut response)
            .map_err(|_| LocalExecutorError::IpcUnavailable)?;
        if response.is_empty() || response.len() > MAX_MESSAGE_BYTES {
            return Err(LocalExecutorError::IpcUnavailable);
        }
        serde_json::from_str(&response).map_err(|_| LocalExecutorError::IpcUnavailable)
    }

    fn stop(&mut self) -> Result<LocalExecutorStatus, LocalExecutorError> {
        if let Some(mut sidecar) = self.sidecar.take() {
            sidecar
                .child
                .kill()
                .map_err(|_| LocalExecutorError::ProcessUnavailable)?;
            sidecar
                .child
                .wait()
                .map_err(|_| LocalExecutorError::ProcessUnavailable)?;
        }
        self.status()
    }
}

pub fn new_session_token() -> Result<String, LocalExecutorError> {
    let mut bytes = [0_u8; 32];
    getrandom::fill(&mut bytes).map_err(|_| LocalExecutorError::ProcessUnavailable)?;
    Ok(bytes.iter().map(|byte| format!("{byte:02x}")).collect())
}

fn tokens_equal(expected: &str, actual: &str) -> bool {
    if expected.len() != actual.len() {
        return false;
    }
    expected
        .bytes()
        .zip(actual.bytes())
        .fold(0_u8, |difference, (left, right)| {
            difference | (left ^ right)
        })
        == 0
}

pub fn authenticate_sidecar_request(
    expected_token: &str,
    session_token: &str,
    request: &Value,
) -> Result<String, LocalExecutorError> {
    if !tokens_equal(expected_token, session_token) {
        return Err(LocalExecutorError::AuthenticationFailed);
    }
    if request.get("protocol_version").and_then(Value::as_str) != Some(PROTOCOL_VERSION) {
        return Err(LocalExecutorError::UnsupportedProtocol);
    }
    request
        .get("message_type")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty() && value.len() <= 128)
        .map(ToOwned::to_owned)
        .ok_or(LocalExecutorError::InvalidRequest)
}

fn write_sidecar_response(
    output: &mut impl Write,
    response: &Value,
) -> Result<(), LocalExecutorError> {
    serde_json::to_writer(&mut *output, response)
        .map_err(|_| LocalExecutorError::IpcUnavailable)?;
    writeln!(output).map_err(|_| LocalExecutorError::IpcUnavailable)?;
    output
        .flush()
        .map_err(|_| LocalExecutorError::IpcUnavailable)
}

pub fn run_sidecar_io(
    mut input: impl BufRead,
    mut output: impl Write,
) -> Result<(), LocalExecutorError> {
    let mut session_token = String::new();
    input
        .read_line(&mut session_token)
        .map_err(|_| LocalExecutorError::IpcUnavailable)?;
    let session_token = session_token.trim_end_matches(['\r', '\n']).to_owned();
    if session_token.len() != 64 || !session_token.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        return Err(LocalExecutorError::AuthenticationFailed);
    }

    for line in input.lines() {
        let line = line.map_err(|_| LocalExecutorError::IpcUnavailable)?;
        if line.len() > MAX_MESSAGE_BYTES {
            write_sidecar_response(
                &mut output,
                &json!({"ok": false, "error": "invalid_request"}),
            )?;
            continue;
        }
        let envelope: SidecarEnvelope = match serde_json::from_str(&line) {
            Ok(value) => value,
            Err(_) => {
                write_sidecar_response(
                    &mut output,
                    &json!({"ok": false, "error": "invalid_request"}),
                )?;
                continue;
            }
        };
        if envelope.protocol_version != PROTOCOL_VERSION {
            write_sidecar_response(
                &mut output,
                &json!({"ok": false, "error": "unsupported_protocol"}),
            )?;
            continue;
        }
        match authenticate_sidecar_request(
            &session_token,
            &envelope.session_token,
            &envelope.request,
        ) {
            Ok(message_type) => write_sidecar_response(
                &mut output,
                &json!({
                    "ok": true,
                    "protocol_version": PROTOCOL_VERSION,
                    "message_type": message_type,
                    "status": "accepted",
                }),
            )?,
            Err(LocalExecutorError::AuthenticationFailed) => write_sidecar_response(
                &mut output,
                &json!({"ok": false, "error": "authentication_failed"}),
            )?,
            Err(LocalExecutorError::UnsupportedProtocol) => write_sidecar_response(
                &mut output,
                &json!({"ok": false, "error": "unsupported_protocol"}),
            )?,
            Err(_) => write_sidecar_response(
                &mut output,
                &json!({"ok": false, "error": "invalid_request"}),
            )?,
        }
    }
    Ok(())
}

pub fn run_sidecar() -> Result<(), LocalExecutorError> {
    let stdin = std::io::stdin();
    let stdout = std::io::stdout();
    run_sidecar_io(stdin.lock(), stdout.lock())
}

fn require_capability(capability_id: &str) -> Result<(), LocalExecutorError> {
    if capability_id == CAPABILITY_ID {
        Ok(())
    } else {
        Err(LocalExecutorError::InvalidCapability)
    }
}

fn manager<'a>(
    state: &'a State<'_, Mutex<LocalExecutorManager>>,
) -> Result<std::sync::MutexGuard<'a, LocalExecutorManager>, LocalExecutorError> {
    state
        .lock()
        .map_err(|_| LocalExecutorError::ProcessUnavailable)
}

#[tauri::command]
pub fn local_executor_start(
    capability_id: String,
    state: State<'_, Mutex<LocalExecutorManager>>,
) -> Result<LocalExecutorStatus, LocalExecutorError> {
    require_capability(&capability_id)?;
    manager(&state)?.start()
}

#[tauri::command]
pub fn local_executor_invoke(
    capability_id: String,
    request: Value,
    state: State<'_, Mutex<LocalExecutorManager>>,
) -> Result<Value, LocalExecutorError> {
    require_capability(&capability_id)?;
    manager(&state)?.invoke(request)
}

#[tauri::command]
pub fn local_executor_status(
    capability_id: String,
    state: State<'_, Mutex<LocalExecutorManager>>,
) -> Result<LocalExecutorStatus, LocalExecutorError> {
    require_capability(&capability_id)?;
    manager(&state)?.status()
}

#[tauri::command]
pub fn local_executor_stop(
    capability_id: String,
    state: State<'_, Mutex<LocalExecutorManager>>,
) -> Result<LocalExecutorStatus, LocalExecutorError> {
    require_capability(&capability_id)?;
    manager(&state)?.stop()
}
