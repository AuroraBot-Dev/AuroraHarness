//! 把 Tauri 的具体设施接到 Agent 运行时代理上。
//!
//! `aurora-runtime-broker` 不认识 Tauri：它只要求一个 [`EventSink`]（事件往哪去）和一个
//! [`SecretStore`]（密钥从哪来）。本模块提供这两个实现，以及开发态路径推导——所有
//! Tauri 相关的耦合都收在这里，代理层因此可以脱离桌面壳单独编译和测试。

use std::path::{Path, PathBuf};
use std::sync::Arc;

use aurora_runtime_broker::event::EventSink;
use aurora_runtime_broker::launch::repo_root_from;
use aurora_runtime_broker::secrets::SecretStore;
use aurora_runtime_broker::status::RuntimeStatus;
use serde_json::Value;
use tauri::{AppHandle, Emitter};

/// 系统钥匙串中的服务名。
///
/// 与拆分前的 `sidecar.rs` 保持完全一致：一旦改动，用户已保存的密钥会读不到。
pub const KEYRING_SERVICE: &str = "com.aurora.agent";
/// 系统钥匙串中的账号名。
pub const KEYRING_USER: &str = "model-api-key";

/// 基于系统钥匙串的密钥实现（macOS Keychain / Windows 凭据管理器 / Linux Secret Service）。
///
/// 刻意留在壳这一层而不是代理层：钥匙串是平台集成，Linux 后端依赖 dbus，放进纯 Rust 层
/// 会让代理被迫依赖系统库，也就失去了「可脱离桌面壳单独编译」的意义。
pub struct KeyringSecretStore {
    service: String,
    user: String,
}

impl KeyringSecretStore {
    pub fn new(service: impl Into<String>, user: impl Into<String>) -> Self {
        Self {
            service: service.into(),
            user: user.into(),
        }
    }

    fn entry(&self) -> Result<keyring::Entry, keyring::Error> {
        keyring::Entry::new(&self.service, &self.user)
    }
}

impl SecretStore for KeyringSecretStore {
    fn get(&self) -> Option<String> {
        self.entry().ok()?.get_password().ok()
    }

    fn set(&self, secret: &str) -> Result<(), String> {
        self.entry()
            .and_then(|entry| entry.set_password(secret))
            .map_err(|_| "API Key 无法写入系统钥匙串".to_string())
    }

    fn delete(&self) -> Result<(), String> {
        let entry = self.entry().map_err(|_| "系统钥匙串不可用".to_string())?;
        match entry.delete_credential() {
            Ok(()) | Err(keyring::Error::NoEntry) => Ok(()),
            Err(_) => Err("API Key 无法从系统钥匙串删除".into()),
        }
    }
}

/// 通过 Tauri 事件通道把运行时状态、协议事件与诊断输出推给前端。
pub struct TauriEventSink {
    app: AppHandle,
}

impl TauriEventSink {
    pub fn new(app: AppHandle) -> Self {
        Self { app }
    }
}

impl EventSink for TauriEventSink {
    fn status(&self, status: &RuntimeStatus) {
        let _ = self.app.emit("runtime-status", status.clone());
    }

    fn event(&self, event: Value) {
        let _ = self.app.emit("runtime-event", event);
    }

    fn stderr(&self, line: &str) {
        let _ = self.app.emit("runtime-stderr", line.to_owned());
    }
}

/// 发布版的密钥存放位置：系统钥匙串。
pub fn secrets() -> Arc<dyn SecretStore> {
    Arc::new(KeyringSecretStore::new(KEYRING_SERVICE, KEYRING_USER))
}

/// 开发态的仓库根：由本 crate 的 manifest 目录（`<repo>/src/tauri`）推出。
///
/// `uv run aurora runtime` 必须在 uv 工作区根执行。
pub fn dev_repo_root() -> PathBuf {
    repo_root_from(Path::new(env!("CARGO_MANIFEST_DIR")))
}
