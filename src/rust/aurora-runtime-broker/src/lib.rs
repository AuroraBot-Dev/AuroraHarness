//! Aurora 桌面的 Python 运行时监管层。
//!
//! 职责：拉起 Python 运行时进程、在它的 stdin/stdout 上收发 protocol v1 的 NDJSON、
//! 按 `request_id` 把响应回送给等待中的调用方、在子进程意外退出后自动重启，并把状态、
//! 协议事件与诊断输出交给注入的 [`EventSink`]。
//!
//! 这一层**不依赖 tauri**：它只要求一个 [`EventSink`]（事件往哪去）和一个
//! [`SecretStore`]（密钥从哪来），由外层——Tauri 壳或测试——提供。因此代理逻辑
//! 可以脱离桌面壳单独编译、单独测试，桌面壳也只负责把 Tauri 的设施接上去。

pub mod broker;
pub mod event;
pub mod launch;
pub mod secrets;
pub mod status;

pub use broker::{BrokerOptions, RuntimeBroker};
pub use event::{EventSink, NullEventSink};
pub use launch::{repo_root_from, runtime_command, Launch};
pub use secrets::{redact_text, take_secret_update, KeyringSecretStore, SecretStore};
pub use status::RuntimeStatus;
