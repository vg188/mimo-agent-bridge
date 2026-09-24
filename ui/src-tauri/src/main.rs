#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;

use serde::Serialize;
use tauri::{Manager, State};

#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

struct BridgeProc {
    child: Mutex<Option<Child>>,
    root: Mutex<PathBuf>,
}

#[derive(Serialize)]
struct CmdResult {
    ok: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    error: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    message: Option<String>,
}

fn ok(message: impl Into<String>) -> CmdResult {
    CmdResult { ok: true, error: None, message: Some(message.into()) }
}
fn err(error: impl Into<String>) -> CmdResult {
    CmdResult { ok: false, error: Some(error.into()), message: None }
}

fn dirs_like_home() -> PathBuf {
    std::env::var("USERPROFILE")
        .map(PathBuf::from)
        .or_else(|_| std::env::var("HOME").map(PathBuf::from))
        .unwrap_or_else(|_| PathBuf::from("."))
}

fn bridge_home() -> PathBuf {
    if let Ok(p) = std::env::var("MIMO_BRIDGE_HOME") {
        return PathBuf::from(p);
    }
    if let Ok(local) = std::env::var("LOCALAPPDATA") {
        return PathBuf::from(local).join("MiMoAgentBridge");
    }
    dirs_like_home().join(".mimo-agent-bridge")
}

fn python_exe() -> String {
    if let Ok(p) = std::env::var("MIMO_PYTHON") {
        if !p.trim().is_empty() {
            return p;
        }
    }
    if cfg!(windows) {
        // pythonw = GUI subsystem, never allocates a console
        for cand in ["pythonw", "python", "py"] {
            if Command::new(cand)
                .arg("--version")
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .status()
                .map(|s| s.success())
                .unwrap_or(false)
            {
                return cand.to_string();
            }
        }
        return "pythonw".into();
    }
    "python3".into()
}

fn python_windowless_exe() -> String {
    // For long-running serve: always prefer pythonw on Windows
    if cfg!(windows) {
        if std::path::Path::new("pythonw").exists()
            || Command::new("pythonw")
                .arg("--version")
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .status()
                .map(|s| s.success())
                .unwrap_or(false)
        {
            return "pythonw".into();
        }
        // if only `py` exists, use `pyw`
        if Command::new("pyw")
            .arg("--version")
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status()
            .map(|s| s.success())
            .unwrap_or(false)
        {
            return "pyw".into();
        }
    }
    python_exe()
}

/// Directory that contains the `mimo_bridge` package.
/// Prefer bundled resources (installer), then dev tree.
fn resolve_pkg_root(resource_dir: &Path) -> PathBuf {
    // installed: resources/mimo_bridge
    let bundled = resource_dir.join("mimo_bridge");
    if bundled.join("__main__.py").exists() || bundled.join("cli.py").exists() {
        return resource_dir.to_path_buf();
    }
    // portable layout next to exe: <app>/resources/mimo_bridge or <app>/mimo_bridge
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            for cand in [
                dir.join("resources"),
                dir.to_path_buf(),
                dir.join("..").join("resources"),
            ] {
                if cand.join("mimo_bridge").join("cli.py").exists() {
                    return cand.canonicalize().unwrap_or(cand);
                }
            }
        }
    }
    // dev: ui/src-tauri -> mimo-agent-bridge
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(|p| p.parent())
        .map(|p| p.to_path_buf())
        .unwrap_or_else(|| PathBuf::from("."))
}

fn hidden_command(program: &str) -> Command {
    let mut cmd = Command::new(program);
    cmd.stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::piped());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }
    cmd
}

fn spawn_hidden_detached(program: &str, args: &[&str], cwd: &Path, envs: &[(String, String)]) -> std::io::Result<Child> {
    let mut cmd = Command::new(program);
    cmd.args(args).current_dir(cwd);
    cmd.stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null());
    for (k, v) in envs {
        cmd.env(k, v);
    }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        // no console window + don't wait on parent exit
        cmd.creation_flags(CREATE_NO_WINDOW | 0x0000_0008 /* DETACHED_PROCESS */);
    }
    cmd.spawn()
}

fn run_bridge_cli(root: &Path, args: &[&str]) -> Result<String, String> {
    // need stdout — use python/py (not pythonw) but hide the console
    let program = if cfg!(windows) {
        if Command::new("py")
            .arg("--version")
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status()
            .map(|s| s.success())
            .unwrap_or(false)
        {
            "py".to_string()
        } else {
            python_exe().replace("pythonw", "python")
        }
    } else {
        python_exe()
    };
    let mut cmd = hidden_command(&program);
    if program == "py" || program == "pyw" {
        cmd.arg("-3");
    }
    cmd.arg("-m").arg("mimo_bridge").args(args).current_dir(root);
    cmd.env("PYTHONPATH", root.display().to_string());
    cmd.env("MIMO_BRIDGE_HOME", bridge_home());
    let out = cmd.output().map_err(|e| format!("spawn python failed: {e}"))?;
    let stdout = String::from_utf8_lossy(&out.stdout).to_string();
    let stderr = String::from_utf8_lossy(&out.stderr).to_string();
    if out.status.success() {
        Ok(stdout)
    } else {
        Err(if stderr.trim().is_empty() { stdout } else { stderr })
    }
}

#[tauri::command]
fn save_cookie(cookie: String, state: State<BridgeProc>) -> CmdResult {
    let root = state.root.lock().unwrap().clone();
    match run_bridge_cli(&root, &["auth", "--cookie", &cookie]) {
        Ok(msg) => ok(msg),
        Err(e) => err(e),
    }
}

#[tauri::command]
fn extract_cookie(state: State<BridgeProc>) -> CmdResult {
    let root = state.root.lock().unwrap().clone();
    // Auto-detect: try immediately, then wait for Desktop to release the cookie DB.
    match run_bridge_cli(&root, &["auth", "--auto", "--wait", "90"]) {
        Ok(msg) => ok(msg),
        Err(e) => err(e),
    }
}

#[tauri::command]
fn auth_status(state: State<BridgeProc>) -> CmdResult {
    let root = state.root.lock().unwrap().clone();
    match run_bridge_cli(&root, &["auth", "--status"]) {
        Ok(msg) => ok(msg),
        Err(e) => err(e),
    }
}

#[tauri::command]
fn plan_json(state: State<BridgeProc>) -> CmdResult {
    let root = state.root.lock().unwrap().clone();
    match run_bridge_cli(&root, &["plan"]) {
        Ok(msg) => ok(msg),
        Err(e) => err(e),
    }
}

#[tauri::command]
fn doctor_json(state: State<BridgeProc>) -> CmdResult {
    let root = state.root.lock().unwrap().clone();
    match run_bridge_cli(&root, &["doctor", "--json"]) {
        Ok(msg) => ok(msg),
        Err(e) => err(e),
    }
}

#[tauri::command]
fn start_bridge(state: State<BridgeProc>, port: Option<u16>) -> CmdResult {
    let mut guard = state.child.lock().unwrap();
    if let Some(child) = guard.as_mut() {
        if child.try_wait().ok().flatten().is_none() {
            return ok("already running");
        }
    }
    let root = state.root.lock().unwrap().clone();
    let port = port.unwrap_or(8787);
    let program = python_windowless_exe();
    let mut args: Vec<String> = Vec::new();
    if program == "py" || program == "pyw" {
        args.push("-3".into());
    }
    args.extend(["-m".into(), "mimo_bridge".into(), "serve".into(), "--port".into(), port.to_string()]);
    let arg_refs: Vec<&str> = args.iter().map(|s| s.as_str()).collect();
    match spawn_hidden_detached(
        &program,
        &arg_refs,
        &root,
        &[
            ("PYTHONPATH".into(), root.display().to_string()),
            ("MIMO_BRIDGE_HOME".into(), bridge_home().display().to_string()),
        ],
    ) {
        Ok(child) => {
            *guard = Some(child);
            ok(format!("bridge started on 127.0.0.1:{port}"))
        }
        Err(e) => err(format!("start failed: {e}")),
    }
}

#[tauri::command]
fn stop_bridge(state: State<BridgeProc>) -> CmdResult {
    let mut guard = state.child.lock().unwrap();
    if let Some(mut child) = guard.take() {
        let _ = child.kill();
        let _ = child.wait();
        ok("bridge stopped")
    } else {
        ok("not running")
    }
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
            let resource_dir = app
                .path()
                .resource_dir()
                .unwrap_or_else(|_| PathBuf::from("."));
            let root = resolve_pkg_root(&resource_dir);
            *app.state::<BridgeProc>().root.lock().unwrap() = root.clone();

            // auto-start gateway without console
            let program = python_windowless_exe();
            let mut args: Vec<String> = Vec::new();
            if program == "py" || program == "pyw" {
                args.push("-3".into());
            }
            args.extend(["-m".into(), "mimo_bridge".into(), "serve".into(), "--port".into(), "8787".into()]);
            let arg_refs: Vec<&str> = args.iter().map(|s| s.as_str()).collect();
            if let Ok(child) = spawn_hidden_detached(
                &program,
                &arg_refs,
                &root,
                &[
                    ("PYTHONPATH".into(), root.display().to_string()),
                    ("MIMO_BRIDGE_HOME".into(), bridge_home().display().to_string()),
                ],
            ) {
                *app.state::<BridgeProc>().child.lock().unwrap() = Some(child);
            }
            Ok(())
        })
        .manage(BridgeProc {
            child: Mutex::new(None),
            root: Mutex::new(PathBuf::from(".")),
        })
        .invoke_handler(tauri::generate_handler![
            save_cookie,
            extract_cookie,
            auth_status,
            plan_json,
            doctor_json,
            start_bridge,
            stop_bridge
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
