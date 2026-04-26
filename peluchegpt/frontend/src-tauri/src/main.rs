// Inicialização do app Tauri e gerenciamento do processo backend.exe.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
use std::path::PathBuf;
use std::process::{Child, Command};
use std::sync::{Arc, Mutex};
use tauri::{AppHandle, Manager, State};

struct BackendState {
    child: Arc<Mutex<Option<Child>>>,
}

fn backend_path(app: &AppHandle) -> PathBuf {
    // Em produção, o backend fica ao lado do executável principal.
    let exe_path = std::env::current_exe().unwrap_or_else(|_| PathBuf::from("."));
    exe_path
        .parent()
        .unwrap_or_else(|| std::path::Path::new("."))
        .join("backend.exe")
}

fn start_backend(app: &AppHandle, state: &BackendState) {
    let path = backend_path(app);
    if !path.exists() {
        eprintln!("backend.exe não encontrado em {:?}", path);
        return;
    }
    let child = Command::new(path)
        .arg("--host")
        .arg("127.0.0.1")
        .arg("--port")
        .arg("7474")
        .spawn();
    if let Ok(proc_child) = child {
        if let Ok(mut lock) = state.child.lock() {
            *lock = Some(proc_child);
        }
    }
}

fn stop_backend(state: &BackendState) {
    if let Ok(mut lock) = state.child.lock() {
        if let Some(mut child) = lock.take() {
            let _ = child.kill();
        }
    }
}

fn main() {
    tauri::Builder::default()
        .manage(BackendState {
            child: Arc::new(Mutex::new(None)),
        })
        .setup(|app| {
            let state: State<BackendState> = app.state();
            start_backend(&app.handle(), &state);
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::Destroyed = event {
                let state: State<BackendState> = window.state();
                stop_backend(&state);
            }
        })
        .run(tauri::generate_context!())
        .expect("erro ao iniciar PelucheGPT");
}