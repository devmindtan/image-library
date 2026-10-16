#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
use std::{
    collections::HashMap,
    io::{BufRead, BufReader, Write},
    process::{Child, Command, Stdio},
    sync::{Arc, Mutex},
};
use tauri::{ipc::Channel, Manager};

type Jobs = Arc<Mutex<HashMap<String, Child>>>;
#[derive(Default)]
struct WorkerState {
    jobs: Jobs,
}

#[tauri::command]
async fn run_worker(
    app: tauri::AppHandle,
    state: tauri::State<'_, WorkerState>,
    job_id: String,
    request: serde_json::Value,
    on_event: Channel<serde_json::Value>,
) -> Result<(), String> {
    let jobs = state.jobs.clone();
    tauri::async_runtime::spawn_blocking(move || {
        let script = if cfg!(debug_assertions) {
            std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../backend/worker.py")
        } else { app.path().resource_dir().map_err(|e|e.to_string())?.join("backend/worker.py") };
        let python = std::env::var("IMAGE_LIBRARY_PYTHON").unwrap_or_else(|_|if cfg!(windows) {"python".into()} else {"python3".into()});
        let mut command = Command::new(python);
        command.arg("-u").arg(script).stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::inherit());
        #[cfg(windows)] {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x08000000);
        }
        let mut registry = jobs.lock().map_err(|e|e.to_string())?;
        if !registry.is_empty() { return Err("Một tác vụ khác đang chạy".into()); }
        let mut child = command.spawn().map_err(|e|format!("Không chạy được Python: {e}. Đặt IMAGE_LIBRARY_PYTHON tới Python trong .venv."))?;
        let stdout = child.stdout.take().ok_or("Worker stdout unavailable")?;
        let write_result = (|| -> Result<(), String> {
            let mut stdin = child.stdin.take().ok_or("Worker stdin unavailable")?;
            writeln!(stdin, "{}", request).map_err(|e|e.to_string())
        })();
        if let Err(error) = write_result { let _ = child.kill(); let _ = child.wait(); return Err(error); }
        registry.insert(job_id.clone(), child);
        drop(registry);
        let mut terminal = false;
        let mut read_error = None;
        for line in BufReader::new(stdout).lines() {
            match line {
                Ok(line) => match serde_json::from_str::<serde_json::Value>(&line) {
                    Ok(event) => {
                        if event["type"] == "done" || event["type"] == "error" { terminal = true; }
                        if on_event.send(event).is_err() { read_error = Some("Cửa sổ đã đóng".to_string()); break; }
                    },
                    Err(e) => { read_error = Some(format!("Worker protocol: {e}")); break; }
                },
                Err(e) => { read_error = Some(e.to_string()); break; }
            }
        }
        if let Some(mut child) = jobs.lock().map_err(|e|e.to_string())?.remove(&job_id) {
            if read_error.is_some() { let _ = child.kill(); }
            let status = child.wait().map_err(|e|e.to_string())?;
            if let Some(error) = read_error { return Err(error); }
            if !status.success() || !terminal { return Err("Tác vụ dừng trước khi hoàn tất (có thể đã hủy). Kiểm tra nhật ký nếu đã bắt đầu sao chép.".into()); }
        }
        Ok(())
    }).await.map_err(|e|e.to_string())?
}

#[tauri::command]
fn cancel_worker(state: tauri::State<'_, WorkerState>, job_id: String) -> Result<(), String> {
    if let Some(child) = state
        .jobs
        .lock()
        .map_err(|e| e.to_string())?
        .get_mut(&job_id)
    {
        child.kill().map_err(|e| e.to_string())?;
    }
    Ok(())
}
fn resolve_media(source: &str, path: &str) -> Result<std::path::PathBuf, String> {
    use std::path::{Component, Path};
    let root = std::fs::canonicalize(source).map_err(|e| e.to_string())?;
    let relative = Path::new(path);
    if relative
        .components()
        .any(|c| !matches!(c, Component::Normal(_)))
        || relative.as_os_str().is_empty()
    {
        return Err("Đường dẫn không hợp lệ".into());
    }
    let mut candidate = root.clone();
    for part in relative.components() {
        candidate.push(part.as_os_str());
        if std::fs::symlink_metadata(&candidate)
            .map_err(|e| e.to_string())?
            .file_type()
            .is_symlink()
        {
            return Err("Không mở liên kết tượng trưng".into());
        }
    }
    let file = std::fs::canonicalize(candidate).map_err(|e| e.to_string())?;
    if !file.starts_with(&root) || !file.is_file() {
        return Err("File phải nằm trong thư mục đang mở".into());
    }
    Ok(file)
}
#[tauri::command]
fn authorize_media(app: tauri::AppHandle, source: String, path: String) -> Result<String, String> {
    let file = resolve_media(&source, &path)?;
    app.asset_protocol_scope()
        .allow_file(&file)
        .map_err(|e| e.to_string())?;
    Ok(file.to_string_lossy().into_owned())
}

#[cfg(test)]
mod tests {
    use super::resolve_media;
    #[test]
    fn local_media_stays_in_root() {
        let root = std::env::temp_dir().join(format!(
            "image-library-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        std::fs::create_dir_all(root.join("nested")).unwrap();
        std::fs::write(root.join("nested/photo.jpg"), b"fixture").unwrap();
        let source = root.to_str().unwrap();
        assert!(resolve_media(source, "nested/photo.jpg").is_ok());
        assert!(resolve_media(source, "../outside.jpg").is_err());
        assert!(resolve_media(source, root.join("nested/photo.jpg").to_str().unwrap()).is_err());
        assert!(resolve_media(source, "nested").is_err());
        assert!(resolve_media(source, "").is_err());
        #[cfg(unix)]
        {
            std::os::unix::fs::symlink(root.join("nested/photo.jpg"), root.join("linked.jpg"))
                .unwrap();
            assert!(resolve_media(source, "linked.jpg").is_err());
        }
        std::fs::remove_dir_all(root).unwrap();
    }
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .manage(WorkerState::default())
        .invoke_handler(tauri::generate_handler![
            run_worker,
            cancel_worker,
            authorize_media
        ])
        .build(tauri::generate_context!())
        .expect("Cannot build desktop app")
        .run(|app, event| {
            if let tauri::RunEvent::Exit = event {
                let state = app.state::<WorkerState>();
                if let Ok(mut jobs) = state.jobs.lock() {
                    for child in jobs.values_mut() {
                        let _ = child.kill();
                        let _ = child.wait();
                    }
                };
            }
        });
}
