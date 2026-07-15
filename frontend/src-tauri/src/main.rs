// Prevents additional console window on Windows in release, DO NOT REMOVE!!
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    if std::env::args().any(|argument| argument == "--social-operations-sidecar") {
        std::process::exit(
            match agent_platform_desktop::local_executor::run_sidecar() {
                Ok(()) => 0,
                Err(_) => 1,
            },
        );
    }
    agent_platform_desktop::run();
}
