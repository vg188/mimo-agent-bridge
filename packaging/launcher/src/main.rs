//! Single-file launcher for MiMo Agent Bridge.
//! Embeds ui/ + WebView2Loader.dll + mimo_bridge package and extracts on first run.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::env;
use std::fs::{self, File};
use std::path::{Path, PathBuf};
use std::process::Command;

const APP_ZIP: &[u8] = include_bytes!("../assets/app.zip");
const VERSION: &str = "0.1.0";

fn data_dir() -> PathBuf {
    if let Ok(p) = env::var("MIMO_BRIDGE_HOME") {
        return PathBuf::from(p).join("runtime");
    }
    if let Ok(local) = env::var("LOCALAPPDATA") {
        return PathBuf::from(local).join("MiMoAgentBridge").join("runtime");
    }
    env::temp_dir().join("MiMoAgentBridge-runtime")
}

fn extract_zip(bytes: &[u8], dest: &Path) -> std::io::Result<()> {
    let cursor = std::io::Cursor::new(bytes);
    let mut zip = zip::ZipArchive::new(cursor).map_err(|e| std::io::Error::other(e.to_string()))?;
    for i in 0..zip.len() {
        let mut file = zip.by_index(i).map_err(|e| std::io::Error::other(e.to_string()))?;
        let Some(name) = file.enclosed_name().map(PathBuf::from) else {
            continue;
        };
        let out = dest.join(&name);
        if file.is_dir() {
            fs::create_dir_all(&out)?;
            continue;
        }
        if let Some(parent) = out.parent() {
            fs::create_dir_all(parent)?;
        }
        let mut of = File::create(&out)?;
        std::io::copy(&mut file, &mut of)?;
    }
    Ok(())
}

fn stamp_path(root: &Path) -> PathBuf {
    root.join(".stamp")
}

fn ensure_extracted() -> std::io::Result<PathBuf> {
    let root = data_dir();
    let stamp = stamp_path(&root);
    let want = format!("{VERSION}:{}", APP_ZIP.len());
    if stamp.exists() {
        let got = fs::read_to_string(&stamp).unwrap_or_default();
        if got.trim() == want && root.join("ui").join("mimo-agent-bridge-ui.exe").exists() {
            return Ok(root);
        }
    }
    // clean extract
    if root.exists() {
        let _ = fs::remove_dir_all(&root);
    }
    fs::create_dir_all(&root)?;
    extract_zip(APP_ZIP, &root)?;
    fs::write(&stamp, want)?;
    Ok(root)
}

fn find_python() -> String {
    if let Ok(p) = env::var("MIMO_PYTHON") {
        if !p.is_empty() {
            return p;
        }
    }
    for cand in ["python", "python3", "py"] {
        if Command::new(cand).arg("--version").output().map(|o| o.status.success()).unwrap_or(false) {
            return cand.to_string();
        }
    }
    "python".into()
}

fn show_error(msg: &str) {
    #[cfg(windows)]
    {
        let _ = Command::new("powershell")
            .args([
                "-NoProfile",
                "-Command",
                &format!(
                    "Add-Type -AssemblyName PresentationFramework; [System.Windows.MessageBox]::Show('MiMo Agent Bridge','{0}','OK','Error') | Out-Null",
                    msg.replace('\'', "''")
                ),
            ])
            .status();
    }
    eprintln!("{msg}");
}

fn main() {
    if let Err(e) = run() {
        show_error(&format!("启动失败: {e}"));
        std::process::exit(1);
    }
}

fn run() -> Result<(), Box<dyn std::error::Error>> {
    let root = ensure_extracted()?;
    let ui = root.join("ui").join("mimo-agent-bridge-ui.exe");
    if !ui.exists() {
        return Err(format!("缺少 {}", ui.display()).into());
    }
    let loader = root.join("ui").join("WebView2Loader.dll");
    if !loader.exists() {
        return Err("缺少 WebView2Loader.dll（已内嵌解压，请勿删除 runtime 目录）".into());
    }

    // make python package importable for child processes
    let pythonpath = root.display().to_string();
    let path_key = if cfg!(windows) { "PYTHONPATH" } else { "PYTHONPATH" };
    let prev = env::var(path_key).unwrap_or_default();
    let joined = if prev.is_empty() {
        pythonpath
    } else {
        format!("{pythonpath};{prev}")
    };

    let py = find_python();
    let mut cmd = Command::new(&ui);
    cmd.current_dir(&root)
        .env(path_key, &joined)
        .env("MIMO_PYTHON", &py)
        .env("MIMO_BRIDGE_HOME", data_dir().join("home"))
        .env("MIMO_AGENT_BRIDGE_ROOT", &root);
    // detach launcher: don't wait forever; spawn UI and exit
    let _child = cmd.spawn()?;
    Ok(())
}
