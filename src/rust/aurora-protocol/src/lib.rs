//! Aurora 桌面协议 v1 的共享契约。
//!
//! 这一层只描述「线上长什么样」：版本号、请求信封校验，以及把 Python 运行时写出的一行
//! NDJSON 判别成响应帧还是事件帧。它不碰进程、传输和 UI，所以既被纯 Rust 的
//! runtime broker 使用，也被 Tauri 壳使用。
//!
//! 版本号在三处必须一致：本文件、Python 的 `aurora.protocol.PROTOCOL_VERSION`、
//! 前端的 `app/utils/protocol.ts`。

use serde_json::Value;

/// 前后端共同遵守的协议版本。
pub const PROTOCOL_VERSION: u64 = 1;

/// 一行 NDJSON 的判别结果。
#[derive(Debug)]
pub enum Frame {
    /// 携带 `request_id` 与 `ok` 的响应，需要按 id 回送给等待中的调用方。
    Response(Value),
    /// 携带 `event_id` 与 `type` 的服务端推送。
    Event(Value),
    /// 既不是响应也不是事件，调用方应静默丢弃。
    Invalid,
}

/// 按字段形状判别一行输出。
pub fn classify_line(line: &str) -> Frame {
    let Ok(value) = serde_json::from_str::<Value>(line) else {
        return Frame::Invalid;
    };
    if value.get("request_id").and_then(Value::as_str).is_some()
        && value.get("ok").and_then(Value::as_bool).is_some()
    {
        Frame::Response(value)
    } else if value.get("event_id").and_then(Value::as_str).is_some()
        && value.get("type").and_then(Value::as_str).is_some()
    {
        Frame::Event(value)
    } else {
        Frame::Invalid
    }
}

/// 在把请求写进管道之前校验信封，避免把明显非法的请求送到 Python 侧。
pub fn validate_request(request: &Value) -> Result<(), String> {
    if request.get("protocol_version").and_then(Value::as_u64) != Some(PROTOCOL_VERSION) {
        return Err(format!("仅支持 protocol_version={PROTOCOL_VERSION}"));
    }
    if request
        .get("request_id")
        .and_then(Value::as_str)
        .is_none_or(str::is_empty)
    {
        return Err("request_id 不能为空".into());
    }
    if request
        .get("method")
        .and_then(Value::as_str)
        .is_none_or(str::is_empty)
    {
        return Err("method 不能为空".into());
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;

    #[test]
    fn classifies_response_and_event_frames() {
        assert!(matches!(
            classify_line(r#"{"protocol_version":1,"request_id":"req_1","ok":true,"result":{}}"#),
            Frame::Response(_)
        ));
        assert!(matches!(
            classify_line(
                r#"{"protocol_version":1,"event_id":"evt_1","type":"run.started","occurred_at":0,"payload":{}}"#
            ),
            Frame::Event(_)
        ));
        assert!(matches!(classify_line("not-json"), Frame::Invalid));
    }

    #[test]
    fn validates_v1_request_envelope() {
        let request = json!({
            "protocol_version": 1,
            "request_id": "req_test",
            "method": "runtime.status",
            "params": {}
        });
        assert!(validate_request(&request).is_ok());
        assert!(validate_request(&json!({"protocol_version": 2})).is_err());
        assert!(validate_request(&json!({"protocol_version": 1, "request_id": "", "method": "x"})).is_err());
    }
}
