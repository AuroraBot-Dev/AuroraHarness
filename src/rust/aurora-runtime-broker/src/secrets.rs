//! 模型 API Key 的存取契约与脱敏。
//!
//! 这里只放**与平台无关**的部分：存取契约、从请求里摘出密钥、以及脱敏。具体的钥匙串
//! 实现由外层提供——Linux 的 Secret Service 需要 dbus、Windows 用凭据管理器，属于平台
//! 集成，放在这一层会让代理被迫依赖系统库。

use serde_json::Value;

/// 密钥的存取后端。
///
/// 抽象成 trait 是为了让代理层不绑定具体凭据系统：桌面壳接系统钥匙串，测试接内存实现。
pub trait SecretStore: Send + Sync {
    /// 读取已保存的密钥；未配置或后端不可用时返回 `None`。
    fn get(&self) -> Option<String>;

    /// 写入密钥。
    fn set(&self, secret: &str) -> Result<(), String>;

    /// 删除密钥；本来就不存在时同样算成功。
    fn delete(&self) -> Result<(), String>;

    /// 是否已配置非空密钥。
    fn is_configured(&self) -> bool {
        self.get().is_some_and(|secret| !secret.is_empty())
    }
}

/// 不保存任何密钥的实现：用于无钥匙串场景与测试。
#[derive(Debug, Default)]
pub struct NullSecretStore;

impl SecretStore for NullSecretStore {
    fn get(&self) -> Option<String> {
        None
    }

    fn set(&self, _secret: &str) -> Result<(), String> {
        Err("当前环境未提供密钥存储".into())
    }

    fn delete(&self) -> Result<(), String> {
        Ok(())
    }
}

/// 从请求参数里摘出待写入/清除的密钥。
///
/// 返回 `(待写入的密钥, 是否要求清除)`。摘出的同时把 `api_key_configured` 写回参数，
/// 保证真正发往 Python 侧的帧里不含明文密钥。
pub fn take_secret_update(request: &mut Value) -> (Option<String>, bool) {
    let Some(params) = request.get_mut("params").and_then(Value::as_object_mut) else {
        return (None, false);
    };
    let secret = params
        .remove("api_key")
        .or_else(|| params.remove("apiKey"))
        .and_then(|value| value.as_str().map(str::to_owned));
    let clear = params
        .remove("clear_api_key")
        .or_else(|| params.remove("clearApiKey"))
        .and_then(|value| value.as_bool())
        .unwrap_or(false);
    if secret.is_some() {
        params.insert("api_key_configured".into(), Value::Bool(true));
    } else if clear {
        params.insert("api_key_configured".into(), Value::Bool(false));
    }
    (secret, clear)
}

/// 对可能进入日志或界面的文本做脱敏。
///
/// 已知密钥会被替换为占位符；文本中出现敏感字段名时整段隐藏，宁可丢掉信息也不泄露。
pub fn redact_text(value: &str, secret: Option<&str>) -> String {
    let mut sanitized = value.to_owned();
    if let Some(secret) = secret.filter(|secret| !secret.is_empty()) {
        sanitized = sanitized.replace(secret, "[敏感信息已隐藏]");
    }
    let lower = sanitized.to_ascii_lowercase();
    if lower.contains("api_key") || lower.contains("apikey") || lower.contains("authorization") {
        "[敏感信息已隐藏]".into()
    } else {
        sanitized
    }
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;

    #[test]
    fn extracts_secret_before_serializing_request() {
        let mut request = json!({
            "protocol_version": 1,
            "request_id": "req_settings",
            "method": "settings.update",
            "params": {"provider": "mock", "api_key": "secret-value"}
        });
        let (secret, clear) = take_secret_update(&mut request);
        assert_eq!(secret.as_deref(), Some("secret-value"));
        assert!(!clear);
        assert!(!request.to_string().contains("secret-value"));
        assert_eq!(request["params"]["api_key_configured"], true);
    }

    #[test]
    fn marks_cleared_secret_as_unconfigured() {
        let mut request = json!({
            "protocol_version": 1,
            "request_id": "req_settings",
            "method": "settings.update",
            "params": {"clearApiKey": true}
        });
        let (secret, clear) = take_secret_update(&mut request);
        assert!(secret.is_none());
        assert!(clear);
        assert_eq!(request["params"]["api_key_configured"], false);
    }

    #[test]
    fn redacts_sensitive_diagnostics_but_not_stream_tokens() {
        assert_eq!(redact_text("api_key=secret", None), "[敏感信息已隐藏]");
        assert_eq!(redact_text("task token delta", None), "task token delta");

        // 已知密钥在文本中被就地替换，绝不会原样漏出。
        let redacted = redact_text("using sk-live-123", Some("sk-live-123"));
        assert!(!redacted.contains("sk-live-123"));
        assert_eq!(redacted, "using [敏感信息已隐藏]");
    }
}
