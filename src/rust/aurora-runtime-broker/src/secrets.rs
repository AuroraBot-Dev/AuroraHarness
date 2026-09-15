//! 模型 API Key 的存取与脱敏。
//!
//! 发布版的密钥只落在系统钥匙串里；环境变量仅用于开发/测试注入。密钥本身绝不进入
//! 协议帧、图状态、日志或 SQLite——[`take_secret_update`] 负责把它从请求里摘掉。

use serde_json::Value;

/// 密钥的存取后端。
///
/// 抽象成 trait 是为了让代理层不绑定具体凭据系统，测试可使用内存实现。
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

/// 基于系统钥匙串的实现（macOS Keychain / Windows Credential Manager / Linux Secret Service）。
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
