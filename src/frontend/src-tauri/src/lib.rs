mod protocol;
mod sidecar;

use serde_json::Value;
use sidecar::{RuntimeBroker, RuntimeStatus};
use tauri::{Manager, State};

#[tauri::command]
fn ping() -> &'static str {
    "pong"
}

/// Send one protocol-v1 request to the Python runtime and wait for the response
/// carrying the same request_id. Events are emitted separately as runtime-event.
#[tauri::command]
async fn runtime_request(
    broker: State<'_, RuntimeBroker>,
    mut request: Value,
) -> Result<Value, String> {
    let is_settings_update =
        request.get("method").and_then(Value::as_str) == Some("settings.update");
    let (secret, clear_secret) = if is_settings_update {
        sidecar::take_secret_update(&mut request)
    } else {
        (None, false)
    };

    let broker = broker.inner().clone();
    let request_broker = broker.clone();
    let response = tauri::async_runtime::spawn_blocking(move || request_broker.request(request))
        .await
        .map_err(|error| format!("runtime 请求任务异常: {error}"))??;

    if response.get("ok").and_then(Value::as_bool) == Some(true) {
        if let Some(secret) = secret {
            sidecar::save_secret(&secret)?;
            let restart_broker = broker.clone();
            tauri::async_runtime::spawn_blocking(move || restart_broker.restart())
                .await
                .map_err(|error| format!("runtime 重启任务异常: {error}"))??;
        } else if clear_secret {
            sidecar::delete_secret()?;
            let restart_broker = broker.clone();
            tauri::async_runtime::spawn_blocking(move || restart_broker.restart())
                .await
                .map_err(|error| format!("runtime 重启任务异常: {error}"))??;
        }
    }
    Ok(response)
}

#[tauri::command]
fn runtime_status(broker: State<'_, RuntimeBroker>) -> RuntimeStatus {
    broker.status()
}

#[tauri::command]
async fn runtime_restart(broker: State<'_, RuntimeBroker>) -> Result<RuntimeStatus, String> {
    let broker = broker.inner().clone();
    tauri::async_runtime::spawn_blocking(move || broker.restart())
        .await
        .map_err(|error| format!("runtime 重启任务异常: {error}"))?
}

#[tauri::command]
fn api_key_status() -> bool {
    sidecar::secret_exists()
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .setup(|app| {
            let broker = RuntimeBroker::new(app.handle().clone());
            app.manage(broker.clone());
            tauri::async_runtime::spawn_blocking(move || {
                let _ = broker.ensure_started();
            });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            ping,
            runtime_request,
            runtime_status,
            runtime_restart,
            api_key_status
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
