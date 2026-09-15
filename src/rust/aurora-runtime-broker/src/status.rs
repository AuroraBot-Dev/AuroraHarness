//! 运行时状态快照。

use serde::Serialize;

/// 桌面壳展示与轮询用的运行时状态。
///
/// 这个结构同时是 Tauri 命令的返回类型，因此需要序列化。
#[derive(Clone, Debug, Serialize)]
pub struct RuntimeStatus {
    /// `disconnected` / `starting` / `connecting` / `ready`。
    pub state: String,
    /// 当前子进程 pid，未运行时为 `None`。
    pub pid: Option<u32>,
    /// 累计自动重启次数。
    pub restart_count: u64,
    /// 最近一次失败的说明，已做密钥脱敏。
    pub last_error: Option<String>,
    /// 是否已在钥匙串/环境中配置了模型 API Key。
    pub api_key_configured: bool,
}

impl Default for RuntimeStatus {
    fn default() -> Self {
        Self {
            state: "disconnected".into(),
            pid: None,
            restart_count: 0,
            last_error: None,
            api_key_configured: false,
        }
    }
}
