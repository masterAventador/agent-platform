use crate::sidecar_package::{
    redact_log_line, CrashRecoveryAction, CrashRecoveryPolicy, VerifiedSidecarExecutable,
};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::VecDeque;
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::{mpsc, Arc, Mutex};
use std::thread::JoinHandle;
use std::time::Duration;
use tauri::State;

const PROTOCOL_VERSION: &str = "1.0";
const CAPABILITY_ID: &str = "social-operations";
const SIDECAR_ARGUMENT: &str = "--social-operations-sidecar";
const MAX_MESSAGE_BYTES: usize = 1024 * 1024;
const WATCHDOG_INTERVAL: Duration = Duration::from_millis(20);
const MAX_RETAINED_DIAGNOSTIC_LINES: usize = 200;
const MAX_RETAINED_DIAGNOSTIC_BYTES: usize = 64 * 1024;
const MAX_DIAGNOSTIC_LINE_BYTES: usize = 4096;

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
    InvocationTimedOut,
    LaunchVerificationFailed,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct LocalExecutorStatus {
    pub running: bool,
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
    terminator: ProcessTerminator,
    _prepared_executable: Option<PreparedVerifiedExecutable>,
    stdin: ChildStdin,
    responses: mpsc::Receiver<String>,
    session_token: String,
}

#[derive(Clone)]
struct LaunchSpec {
    executable: PathBuf,
    arguments: Vec<String>,
    verifier: Option<
        Arc<dyn Fn() -> Result<VerifiedSidecarExecutable, LocalExecutorError> + Send + Sync>,
    >,
}

#[derive(Default)]
struct SafeDiagnostics {
    lines: VecDeque<String>,
    total_bytes: usize,
}

impl SafeDiagnostics {
    fn retain(&mut self, line: &str) {
        let safe = truncate_utf8(&redact_log_line(line), MAX_DIAGNOSTIC_LINE_BYTES);
        self.total_bytes += safe.len();
        self.lines.push_back(safe);
        while self.lines.len() > MAX_RETAINED_DIAGNOSTIC_LINES
            || self.total_bytes > MAX_RETAINED_DIAGNOSTIC_BYTES
        {
            if let Some(removed) = self.lines.pop_front() {
                self.total_bytes = self.total_bytes.saturating_sub(removed.len());
            } else {
                self.total_bytes = 0;
                break;
            }
        }
    }

    fn take(&mut self) -> Vec<String> {
        self.total_bytes = 0;
        self.lines.drain(..).collect()
    }
}

#[derive(Default)]
struct SupervisorState {
    running: bool,
}

enum SupervisorCommand {
    Invoke {
        request: Value,
        timeout: Duration,
        reply: mpsc::Sender<Result<Value, LocalExecutorError>>,
    },
    Stop {
        reply: mpsc::Sender<Result<(), LocalExecutorError>>,
    },
}

struct SupervisorHandle {
    commands: mpsc::Sender<SupervisorCommand>,
    state: Arc<Mutex<SupervisorState>>,
    terminator: Arc<Mutex<ProcessTerminator>>,
    thread: Option<JoinHandle<()>>,
}

#[derive(Clone)]
pub struct LocalExecutorManager {
    supervisor: Arc<Mutex<Option<SupervisorHandle>>>,
    lifecycle: Arc<Mutex<()>>,
    safe_diagnostics: Arc<Mutex<SafeDiagnostics>>,
}

impl Default for LocalExecutorManager {
    fn default() -> Self {
        Self {
            supervisor: Arc::new(Mutex::new(None)),
            lifecycle: Arc::new(Mutex::new(())),
            safe_diagnostics: Arc::new(Mutex::new(SafeDiagnostics::default())),
        }
    }
}

impl Drop for LocalExecutorManager {
    fn drop(&mut self) {
        if Arc::strong_count(&self.supervisor) == 1 {
            let _ = self.stop();
        }
    }
}

impl LocalExecutorManager {
    fn status(&self) -> Result<LocalExecutorStatus, LocalExecutorError> {
        let supervisor = self
            .supervisor
            .lock()
            .map_err(|_| LocalExecutorError::ProcessUnavailable)?;
        let running = supervisor
            .as_ref()
            .map(|supervisor| {
                supervisor
                    .state
                    .lock()
                    .map(|state| state.running)
                    .map_err(|_| LocalExecutorError::ProcessUnavailable)
            })
            .transpose()?
            .unwrap_or(false);
        Ok(LocalExecutorStatus {
            running,
            protocol_version: PROTOCOL_VERSION,
            capability_id: CAPABILITY_ID,
        })
    }

    fn start(&self) -> Result<LocalExecutorStatus, LocalExecutorError> {
        let executable =
            std::env::current_exe().map_err(|_| LocalExecutorError::ProcessUnavailable)?;
        self.start_launch(LaunchSpec {
            executable,
            arguments: vec![SIDECAR_ARGUMENT.to_owned()],
            verifier: None,
        })
    }

    pub fn start_installed(
        &self,
        executable: impl AsRef<Path>,
    ) -> Result<LocalExecutorStatus, LocalExecutorError> {
        self.start_launch(LaunchSpec {
            executable: executable.as_ref().to_path_buf(),
            arguments: Vec::new(),
            verifier: None,
        })
    }

    pub fn start_verified<F>(&self, verifier: F) -> Result<LocalExecutorStatus, LocalExecutorError>
    where
        F: Fn() -> Result<VerifiedSidecarExecutable, LocalExecutorError> + Send + Sync + 'static,
    {
        let verifier = Arc::new(verifier);
        let executable = verifier()?.path;
        self.start_launch(LaunchSpec {
            executable,
            arguments: Vec::new(),
            verifier: Some(verifier),
        })
    }

    fn start_launch(
        &self,
        launch_spec: LaunchSpec,
    ) -> Result<LocalExecutorStatus, LocalExecutorError> {
        let _lifecycle = self
            .lifecycle
            .lock()
            .map_err(|_| LocalExecutorError::ProcessUnavailable)?;
        if self.status()?.running {
            return Err(LocalExecutorError::AlreadyRunning);
        }
        if self
            .supervisor
            .lock()
            .map_err(|_| LocalExecutorError::ProcessUnavailable)?
            .is_some()
        {
            self.stop_inner()?;
        }
        let sidecar = spawn_sidecar(&launch_spec, Arc::clone(&self.safe_diagnostics))?;
        let (commands, receiver) = mpsc::channel();
        let state = Arc::new(Mutex::new(SupervisorState { running: true }));
        let watchdog_state = Arc::clone(&state);
        let diagnostics = Arc::clone(&self.safe_diagnostics);
        let terminator = Arc::new(Mutex::new(sidecar.terminator.clone()));
        let supervisor_terminator = Arc::clone(&terminator);
        let thread = std::thread::spawn(move || {
            supervise_sidecar(
                sidecar,
                launch_spec,
                receiver,
                watchdog_state,
                diagnostics,
                supervisor_terminator,
            );
        });
        let mut supervisor = self
            .supervisor
            .lock()
            .map_err(|_| LocalExecutorError::ProcessUnavailable)?;
        *supervisor = Some(SupervisorHandle {
            commands,
            state,
            terminator,
            thread: Some(thread),
        });
        drop(supervisor);
        self.status()
    }

    pub fn invoke(&self, request: Value) -> Result<Value, LocalExecutorError> {
        self.invoke_with_timeout(request, Duration::from_secs(30))
    }

    pub fn invoke_with_timeout(
        &self,
        request: Value,
        timeout: Duration,
    ) -> Result<Value, LocalExecutorError> {
        if !self.status()?.running {
            return Err(LocalExecutorError::NotRunning);
        }
        let commands = self
            .supervisor
            .lock()
            .map_err(|_| LocalExecutorError::ProcessUnavailable)?
            .as_ref()
            .map(|supervisor| supervisor.commands.clone())
            .ok_or(LocalExecutorError::NotRunning)?;
        let (reply, response) = mpsc::channel();
        commands
            .send(SupervisorCommand::Invoke {
                request,
                timeout,
                reply,
            })
            .map_err(|_| LocalExecutorError::NotRunning)?;
        response
            .recv()
            .map_err(|_| LocalExecutorError::NotRunning)?
    }

    pub fn status_snapshot(&self) -> Result<LocalExecutorStatus, LocalExecutorError> {
        self.status()
    }

    pub fn take_safe_diagnostics(&self) -> Vec<String> {
        self.safe_diagnostics
            .lock()
            .map(|mut diagnostics| diagnostics.take())
            .unwrap_or_default()
    }

    pub fn stop(&self) -> Result<LocalExecutorStatus, LocalExecutorError> {
        let _lifecycle = self
            .lifecycle
            .lock()
            .map_err(|_| LocalExecutorError::ProcessUnavailable)?;
        self.stop_inner()
    }

    fn stop_inner(&self) -> Result<LocalExecutorStatus, LocalExecutorError> {
        let supervisor = self
            .supervisor
            .lock()
            .map_err(|_| LocalExecutorError::ProcessUnavailable)?
            .take();
        if let Some(mut supervisor) = supervisor {
            let terminator = supervisor
                .terminator
                .lock()
                .map_err(|_| LocalExecutorError::ProcessUnavailable)?
                .clone();
            terminator.terminate_tree()?;
            let (reply, response) = mpsc::channel();
            let stop_result = if supervisor
                .commands
                .send(SupervisorCommand::Stop { reply })
                .is_ok()
            {
                response.recv().unwrap_or(Ok(()))
            } else {
                Ok(())
            };
            if let Some(thread) = supervisor.thread.take() {
                thread
                    .join()
                    .map_err(|_| LocalExecutorError::ProcessUnavailable)?;
            }
            stop_result?;
        }
        self.status()
    }
}

fn truncate_utf8(value: &str, max_bytes: usize) -> String {
    if value.len() <= max_bytes {
        return value.to_owned();
    }
    let mut boundary = max_bytes;
    while !value.is_char_boundary(boundary) {
        boundary -= 1;
    }
    value[..boundary].to_owned()
}

fn spawn_sidecar(
    launch: &LaunchSpec,
    diagnostics: Arc<Mutex<SafeDiagnostics>>,
) -> Result<RunningSidecar, LocalExecutorError> {
    let session_token = new_session_token()?;
    let verified_executable = launch
        .verifier
        .as_ref()
        .map(|verifier| verifier())
        .transpose()?;
    let prepared_executable = verified_executable
        .map(prepare_verified_execution)
        .transpose()?;
    let executable = prepared_executable
        .as_ref()
        .map(|prepared| prepared.path.clone())
        .unwrap_or_else(|| launch.executable.clone());
    let mut command = Command::new(&executable);
    command
        .args(&launch.arguments)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    configure_process_isolation(&mut command);
    let mut child = command
        .spawn()
        .map_err(|_| LocalExecutorError::ProcessUnavailable)?;
    let terminator = ProcessTerminator::attach(&child)?;
    let stdin = child.stdin.take();
    let stdout = child.stdout.take();
    let stderr = child.stderr.take();
    let (Some(mut stdin), Some(stdout), Some(stderr)) = (stdin, stdout, stderr) else {
        let _ = terminate_process_tree(&terminator, &mut child);
        return Err(LocalExecutorError::IpcUnavailable);
    };
    if writeln!(stdin, "{session_token}").is_err() || stdin.flush().is_err() {
        let _ = terminate_process_tree(&terminator, &mut child);
        return Err(LocalExecutorError::IpcUnavailable);
    }
    let (response_sender, responses) = mpsc::channel();
    std::thread::spawn(move || {
        for line in BufReader::new(stdout).lines() {
            match line {
                Ok(line) => {
                    if response_sender.send(line).is_err() {
                        break;
                    }
                }
                Err(_) => break,
            }
        }
    });
    std::thread::spawn(move || {
        for line in BufReader::new(stderr).lines().map_while(Result::ok) {
            if let Ok(mut retained) = diagnostics.lock() {
                retained.retain(&line);
            }
        }
    });
    Ok(RunningSidecar {
        child,
        terminator,
        _prepared_executable: prepared_executable,
        stdin,
        responses,
        session_token,
    })
}

fn invoke_sidecar(
    sidecar: &mut RunningSidecar,
    request: Value,
    timeout: Duration,
) -> Result<Value, LocalExecutorError> {
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
    let response = match sidecar.responses.recv_timeout(timeout) {
        Ok(response) => response,
        Err(mpsc::RecvTimeoutError::Timeout) => {
            terminate_sidecar(sidecar)?;
            return Err(LocalExecutorError::InvocationTimedOut);
        }
        Err(mpsc::RecvTimeoutError::Disconnected) => {
            return Err(LocalExecutorError::IpcUnavailable)
        }
    };
    if response.is_empty() || response.len() > MAX_MESSAGE_BYTES {
        return Err(LocalExecutorError::IpcUnavailable);
    }
    serde_json::from_str(&response).map_err(|_| LocalExecutorError::IpcUnavailable)
}

fn supervise_sidecar(
    mut sidecar: RunningSidecar,
    launch: LaunchSpec,
    commands: mpsc::Receiver<SupervisorCommand>,
    state: Arc<Mutex<SupervisorState>>,
    diagnostics: Arc<Mutex<SafeDiagnostics>>,
    current_terminator: Arc<Mutex<ProcessTerminator>>,
) {
    let mut recovery = CrashRecoveryPolicy::new(2);
    loop {
        match commands.recv_timeout(WATCHDOG_INTERVAL) {
            Ok(SupervisorCommand::Invoke {
                request,
                timeout,
                reply,
            }) => {
                let result = invoke_sidecar(&mut sidecar, request, timeout);
                let timed_out = result == Err(LocalExecutorError::InvocationTimedOut);
                let _ = reply.send(result);
                if timed_out {
                    set_running(&state, false);
                    return;
                }
            }
            Ok(SupervisorCommand::Stop { reply }) => {
                let result = terminate_sidecar(&mut sidecar);
                set_running(&state, false);
                let _ = reply.send(result);
                return;
            }
            Err(mpsc::RecvTimeoutError::Disconnected) => {
                let _ = terminate_sidecar(&mut sidecar);
                set_running(&state, false);
                return;
            }
            Err(mpsc::RecvTimeoutError::Timeout) => {}
        }

        let exited = match sidecar.child.try_wait() {
            Ok(status) => status.is_some(),
            Err(_) => {
                set_running(&state, false);
                return;
            }
        };
        if !exited {
            continue;
        }
        if terminate_sidecar(&mut sidecar).is_err() {
            set_running(&state, false);
            return;
        }
        match recovery.on_unexpected_exit() {
            CrashRecoveryAction::Restart => {
                match spawn_sidecar(&launch, Arc::clone(&diagnostics)) {
                    Ok(restarted) => {
                        if let Ok(mut terminator) = current_terminator.lock() {
                            *terminator = restarted.terminator.clone();
                        } else {
                            let mut restarted = restarted;
                            let _ = terminate_sidecar(&mut restarted);
                            set_running(&state, false);
                            return;
                        }
                        sidecar = restarted;
                    }
                    Err(_) => {
                        set_running(&state, false);
                        return;
                    }
                }
            }
            CrashRecoveryAction::Stop => {
                set_running(&state, false);
                return;
            }
        }
    }
}

fn set_running(state: &Arc<Mutex<SupervisorState>>, running: bool) {
    if let Ok(mut state) = state.lock() {
        state.running = running;
    }
}

fn terminate_sidecar(sidecar: &mut RunningSidecar) -> Result<(), LocalExecutorError> {
    terminate_process_tree(&sidecar.terminator, &mut sidecar.child)
}

fn terminate_process_tree(
    terminator: &ProcessTerminator,
    child: &mut Child,
) -> Result<(), LocalExecutorError> {
    terminator.terminate_tree()?;
    match child
        .try_wait()
        .map_err(|_| LocalExecutorError::ProcessUnavailable)?
    {
        Some(_) => Ok(()),
        None => {
            child
                .wait()
                .map_err(|_| LocalExecutorError::ProcessUnavailable)?;
            Ok(())
        }
    }
}

#[derive(Clone)]
struct ProcessTerminator {
    termination_requested: Arc<std::sync::atomic::AtomicBool>,
    #[cfg(unix)]
    process_group_id: i32,
    #[cfg(windows)]
    job: Arc<WindowsJob>,
}

impl ProcessTerminator {
    #[cfg(unix)]
    fn attach(child: &Child) -> Result<Self, LocalExecutorError> {
        let process_group_id =
            i32::try_from(child.id()).map_err(|_| LocalExecutorError::ProcessUnavailable)?;
        Ok(Self {
            termination_requested: Arc::new(std::sync::atomic::AtomicBool::new(false)),
            process_group_id,
        })
    }

    #[cfg(windows)]
    fn attach(child: &Child) -> Result<Self, LocalExecutorError> {
        WindowsJob::attach(child).map(|job| Self {
            termination_requested: Arc::new(std::sync::atomic::AtomicBool::new(false)),
            job: Arc::new(job),
        })
    }

    #[cfg(all(not(unix), not(windows)))]
    fn attach(_child: &Child) -> Result<Self, LocalExecutorError> {
        Err(LocalExecutorError::ProcessUnavailable)
    }

    #[cfg(unix)]
    fn terminate_tree(&self) -> Result<(), LocalExecutorError> {
        if self
            .termination_requested
            .swap(true, std::sync::atomic::Ordering::AcqRel)
        {
            return Ok(());
        }
        let result = unsafe { libc::kill(-self.process_group_id, libc::SIGKILL) };
        if result == 0 || std::io::Error::last_os_error().raw_os_error() == Some(libc::ESRCH) {
            Ok(())
        } else {
            self.termination_requested
                .store(false, std::sync::atomic::Ordering::Release);
            Err(LocalExecutorError::ProcessUnavailable)
        }
    }

    #[cfg(windows)]
    fn terminate_tree(&self) -> Result<(), LocalExecutorError> {
        if self
            .termination_requested
            .swap(true, std::sync::atomic::Ordering::AcqRel)
        {
            return Ok(());
        }
        let result = self.job.terminate();
        if result.is_err() {
            self.termination_requested
                .store(false, std::sync::atomic::Ordering::Release);
        }
        result
    }

    #[cfg(all(not(unix), not(windows)))]
    fn terminate_tree(&self) -> Result<(), LocalExecutorError> {
        Err(LocalExecutorError::ProcessUnavailable)
    }
}

#[cfg(unix)]
fn configure_process_isolation(command: &mut Command) {
    use std::os::unix::process::CommandExt;
    command.process_group(0);
}

#[cfg(windows)]
fn configure_process_isolation(command: &mut Command) {
    use std::os::windows::process::CommandExt;
    use windows_sys::Win32::System::Threading::CREATE_SUSPENDED;

    command.creation_flags(CREATE_SUSPENDED);
}

#[cfg(all(not(unix), not(windows)))]
fn configure_process_isolation(_command: &mut Command) {}

struct PreparedVerifiedExecutable {
    path: PathBuf,
    _verified: VerifiedSidecarExecutable,
    #[cfg(unix)]
    _staged: tempfile::NamedTempFile,
}

#[cfg(unix)]
fn prepare_verified_execution(
    executable: VerifiedSidecarExecutable,
) -> Result<PreparedVerifiedExecutable, LocalExecutorError> {
    use std::os::unix::fs::PermissionsExt;

    let parent = executable
        .path
        .parent()
        .ok_or(LocalExecutorError::LaunchVerificationFailed)?;
    let mut staged = tempfile::Builder::new()
        .prefix(".verified-launch-")
        .tempfile_in(parent)
        .map_err(|_| LocalExecutorError::LaunchVerificationFailed)?;
    staged
        .write_all(&executable.contents)
        .and_then(|_| staged.flush())
        .and_then(|_| {
            staged
                .as_file()
                .set_permissions(std::fs::Permissions::from_mode(0o700))
        })
        .map_err(|_| LocalExecutorError::LaunchVerificationFailed)?;
    Ok(PreparedVerifiedExecutable {
        path: staged.path().to_path_buf(),
        _verified: executable,
        _staged: staged,
    })
}

#[cfg(windows)]
fn prepare_verified_execution(
    executable: VerifiedSidecarExecutable,
) -> Result<PreparedVerifiedExecutable, LocalExecutorError> {
    Ok(PreparedVerifiedExecutable {
        path: executable.path.clone(),
        _verified: executable,
    })
}

#[cfg(all(not(unix), not(windows)))]
fn prepare_verified_execution(
    _executable: VerifiedSidecarExecutable,
) -> Result<PreparedVerifiedExecutable, LocalExecutorError> {
    Err(LocalExecutorError::LaunchVerificationFailed)
}

#[cfg(windows)]
struct WindowsJob {
    handle: windows_sys::Win32::Foundation::HANDLE,
}

#[cfg(windows)]
unsafe impl Send for WindowsJob {}

#[cfg(windows)]
unsafe impl Sync for WindowsJob {}

#[cfg(windows)]
impl WindowsJob {
    fn attach(child: &Child) -> Result<Self, LocalExecutorError> {
        use std::mem::{size_of, zeroed};
        use std::os::windows::io::AsRawHandle;
        use windows_sys::Win32::Foundation::{CloseHandle, FALSE};
        use windows_sys::Win32::System::JobObjects::{
            AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
            SetInformationJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
        };
        use windows_sys::Win32::System::Threading::TerminateProcess;

        let handle = unsafe { CreateJobObjectW(std::ptr::null(), std::ptr::null()) };
        if handle.is_null() {
            unsafe { TerminateProcess(child.as_raw_handle() as _, 1) };
            return Err(LocalExecutorError::ProcessUnavailable);
        }
        let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = unsafe { zeroed() };
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        let configured = unsafe {
            SetInformationJobObject(
                handle,
                JobObjectExtendedLimitInformation,
                &limits as *const _ as _,
                size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
            )
        };
        let assigned = configured != FALSE
            && unsafe { AssignProcessToJobObject(handle, child.as_raw_handle() as _) } != FALSE;
        if !assigned || resume_suspended_process(child.id()).is_err() {
            unsafe {
                TerminateProcess(child.as_raw_handle() as _, 1);
                CloseHandle(handle);
            }
            return Err(LocalExecutorError::ProcessUnavailable);
        }
        Ok(Self { handle })
    }

    fn terminate(&self) -> Result<(), LocalExecutorError> {
        use windows_sys::Win32::System::JobObjects::TerminateJobObject;
        if unsafe { TerminateJobObject(self.handle, 1) } == 0 {
            Err(LocalExecutorError::ProcessUnavailable)
        } else {
            Ok(())
        }
    }
}

#[cfg(windows)]
impl Drop for WindowsJob {
    fn drop(&mut self) {
        unsafe { windows_sys::Win32::Foundation::CloseHandle(self.handle) };
    }
}

#[cfg(windows)]
fn resume_suspended_process(process_id: u32) -> Result<(), LocalExecutorError> {
    use std::mem::{size_of, zeroed};
    use windows_sys::Win32::Foundation::{CloseHandle, INVALID_HANDLE_VALUE};
    use windows_sys::Win32::System::Diagnostics::ToolHelp::{
        CreateToolhelp32Snapshot, Thread32First, Thread32Next, TH32CS_SNAPTHREAD, THREADENTRY32,
    };
    use windows_sys::Win32::System::Threading::{OpenThread, ResumeThread, THREAD_SUSPEND_RESUME};

    let snapshot = unsafe { CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0) };
    if snapshot == INVALID_HANDLE_VALUE {
        return Err(LocalExecutorError::ProcessUnavailable);
    }
    let mut entry: THREADENTRY32 = unsafe { zeroed() };
    entry.dwSize = size_of::<THREADENTRY32>() as u32;
    let mut found = false;
    let mut has_entry = unsafe { Thread32First(snapshot, &mut entry) } != 0;
    while has_entry {
        if entry.th32OwnerProcessID == process_id {
            let thread = unsafe { OpenThread(THREAD_SUSPEND_RESUME, 0, entry.th32ThreadID) };
            if !thread.is_null() {
                found = true;
                unsafe {
                    ResumeThread(thread);
                    CloseHandle(thread);
                }
            }
        }
        has_entry = unsafe { Thread32Next(snapshot, &mut entry) } != 0;
    }
    unsafe { CloseHandle(snapshot) };
    if found {
        Ok(())
    } else {
        Err(LocalExecutorError::ProcessUnavailable)
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

#[tauri::command]
pub fn local_executor_start(
    capability_id: String,
    state: State<'_, LocalExecutorManager>,
) -> Result<LocalExecutorStatus, LocalExecutorError> {
    require_capability(&capability_id)?;
    state.start()
}

#[tauri::command]
pub fn local_executor_invoke(
    capability_id: String,
    request: Value,
    state: State<'_, LocalExecutorManager>,
) -> Result<Value, LocalExecutorError> {
    require_capability(&capability_id)?;
    state.invoke(request)
}

#[tauri::command]
pub fn local_executor_status(
    capability_id: String,
    state: State<'_, LocalExecutorManager>,
) -> Result<LocalExecutorStatus, LocalExecutorError> {
    require_capability(&capability_id)?;
    state.status()
}

#[tauri::command]
pub fn local_executor_stop(
    capability_id: String,
    state: State<'_, LocalExecutorManager>,
) -> Result<LocalExecutorStatus, LocalExecutorError> {
    require_capability(&capability_id)?;
    state.stop()
}
