//! Aurora Agent 桌面壳。
//!
//! 这一层只做「把 Tauri 接到 Agent 运行时上」：暴露命令、装配事件桥与钥匙串，
//! 业务逻辑全部在 `aurora-runtime-broker` 与 Python 侧。
//!
//! 目录约定：`<repo>/src/tauri`，与 Python 运行时（`<repo>/src/agent`、`<repo>/src/cli`）
//! 并列；具体路径推导见 [`bridge::dev_repo_root`]。

mod bridge;

use std::sync::Arc;

use aurora_runtime_broker::event::EventSink;
use aurora_runtime_broker::secrets::{take_secret_update, SecretStore};
use aurora_runtime_broker::status::RuntimeStatus;
use aurora_runtime_broker::{BrokerOptions, RuntimeBroker};
use serde_json::Value;
use tauri::{Manager, State};

use bridge::{dev_repo_root, secrets, TauriEventSink};

#[tauri::command]
fn ping() -> &'static str {
    "pong"
}

/// 向 Python 运行时转发一条 protocol v1 请求，并等待同一 request_id 的响应。
///
/// 事件不在此返回，它们通过 `runtime-event` 单独推送。`settings.update` 里携带的
/// 密钥会在写进管道前被摘出并转存到钥匙串，随后重启运行时使其生效。
#[tauri::command]
async fn runtime_request(
    broker: State<'_, RuntimeBroker>,
    secrets: State<'_, Arc<dyn SecretStore>>,
    mut request: Value,
) -> Result<Value, String> {
    let is_settings_update =
        request.get("method").and_then(Value::as_str) == Some("settings.update");
    let (secret, clear_secret) = if is_settings_update {
        take_secret_update(&mut request)
    } else {
        (None, false)
    };

    let broker = broker.inner().clone();
    let request_broker = broker.clone();
    let response = tauri::async_runtime::spawn_blocking(move || request_broker.request(request))
        .await
        .map_err(|error| format!("runtime 请求任务异常: {error}"))??;

    if response.get("ok").and_then(Value::as_bool) == Some(true) {
        // `secrets` 是 State<Arc<dyn SecretStore>>，方法调用会自动穿过 State 与 Arc 两层解引用。
        if let Some(secret) = secret {
            secrets.set(&secret)?;
            restart(broker).await?;
        } else if clear_secret {
            secrets.delete()?;
            restart(broker).await?;
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
    restart(broker).await
}

/// 是否已在钥匙串里配置了模型 API Key。
#[tauri::command]
fn api_key_status(secrets: State<'_, Arc<dyn SecretStore>>) -> bool {
    secrets.is_configured()
}

async fn restart(broker: RuntimeBroker) -> Result<RuntimeStatus, String> {
    tauri::async_runtime::spawn_blocking(move || broker.restart())
        .await
        .map_err(|error| format!("runtime 重启任务异常: {error}"))?
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .setup(|app| {
            let secrets = secrets();
            let events: Arc<dyn EventSink> = Arc::new(TauriEventSink::new(app.handle().clone()));
            let broker = RuntimeBroker::new(BrokerOptions {
                resource_dir: app.path().resource_dir().unwrap_or_default(),
                dev_repo_root: dev_repo_root(),
                events,
                secrets: secrets.clone(),
            });
            app.manage(broker.clone());
            app.manage(secrets);
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
