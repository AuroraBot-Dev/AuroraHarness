//! 运行时事件的出口。
//!
//! 代理不认识 Tauri，也不知道事件最终去哪：它只把状态快照、protocol v1 事件和
//! stderr 诊断交给一个 [`EventSink`]。Tauri 壳用 `AppHandle` 实现它（转发成
//! 前端可监听的事件），测试用 [`NullEventSink`]。

use serde_json::Value;

use crate::status::RuntimeStatus;

/// 运行时状态与事件的接收方。
pub trait EventSink: Send + Sync + 'static {
    /// 状态发生变化。
    fn status(&self, status: &RuntimeStatus);
    /// 收到一条 protocol v1 事件帧。
    fn event(&self, event: Value);
    /// 收到一行 stderr 输出，调用方可能已对其做脱敏。
    fn stderr(&self, line: &str);
}

/// 丢弃全部事件的实现，用于测试与无 UI 场景。
#[derive(Debug, Default)]
pub struct NullEventSink;

impl EventSink for NullEventSink {
    fn status(&self, _status: &RuntimeStatus) {}

    fn event(&self, _event: Value) {}

    fn stderr(&self, _line: &str) {}
}
