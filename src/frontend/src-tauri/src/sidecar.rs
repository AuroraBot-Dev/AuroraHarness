//! Supervised Python runtime and protocol-v1 NDJSON broker.

use std::collections::HashMap;
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{mpsc, Arc, Mutex};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use serde::Serialize;
use serde_json::{json, Value};
use tauri::{AppHandle, Emitter, Manager};

use crate::protocol::PROTOCOL_VERSION;
const REQUEST_TIMEOUT: Duration = Duration::from_secs(300);
const KEYRING_SERVICE: &str = "com.aurora.agent";
const KEYRING_USER: &str = "model-api-key";

type Reply = Result<Value, String>;

#[derive(Clone, Debug, Serialize)]
pub struct RuntimeStatus {
    pub state: String,
    pub pid: Option<u32>,
    pub restart_count: u64,
    pub last_error: Option<String>,
    pub api_key_configured: bool,
}

impl Default for RuntimeStatus {
    fn default() -> Self {
        Self {
            state: "disconnected".into(),
            pid: None,
            restart_count: 0,
            last_error: None,
            api_key_configured: secret_exists(),
        }
    }
}

struct ManagedProcess {
    generation: u64,
    child: Child,
    stdin: Arc<Mutex<ChildStdin>>,
}

struct BrokerInner {
    app: AppHandle,
    resource_dir: PathBuf,
    process: Mutex<Option<ManagedProcess>>,
    pending: Mutex<HashMap<String, mpsc::Sender<Reply>>>,
    status: Mutex<RuntimeStatus>,
    lifecycle: Mutex<()>,
    generation: AtomicU64,
    shutdown: AtomicBool,
}

#[derive(Clone)]
pub struct RuntimeBroker {
    inner: Arc<BrokerInner>,
}

impl RuntimeBroker {
    pub fn new(app: AppHandle) -> Self {
        Self {
            inner: Arc::new(BrokerInner {
                app: app.clone(),
                resource_dir: app.path().resource_dir().unwrap_or_default(),
                process: Mutex::new(None),
                pending: Mutex::new(HashMap::new()),
                status: Mutex::new(RuntimeStatus::default()),
                lifecycle: Mutex::new(()),
                generation: AtomicU64::new(0),
                shutdown: AtomicBool::new(false),
            }),
        }
    }

    pub fn status(&self) -> RuntimeStatus {
        self.inner.status.lock().expect("status lock").clone()
    }

    pub fn ensure_started(&self) -> Result<(), String> {
        if self.inner.process.lock().map_err(lock_error)?.is_some() {
            return Ok(());
        }
        self.start_process(false)
    }

    pub fn restart(&self) -> Result<RuntimeStatus, String> {
        let _guard = self.inner.lifecycle.lock().map_err(lock_error)?;
        self.stop_locked("manual restart")?;
        self.spawn_locked(true)?;
        Ok(self.status())
    }

    pub fn request(&self, request: Value) -> Result<Value, String> {
        validate_request(&request)?;
        self.ensure_started()?;

        let request_id = request["request_id"]
            .as_str()
            .expect("validated request id")
            .to_owned();
        let (sender, receiver) = mpsc::channel();
        self.inner
            .pending
            .lock()
            .map_err(lock_error)?
            .insert(request_id.clone(), sender);

        let line = serde_json::to_string(&request).map_err(|_| "请求无法序列化".to_string())?;
        if let Err(error) = self.send_line(&line) {
            self.remove_pending(&request_id);
            return Err(error);
        }

        match receiver.recv_timeout(REQUEST_TIMEOUT) {
            Ok(reply) => reply,
            Err(mpsc::RecvTimeoutError::Timeout) => {
                self.remove_pending(&request_id);
                Err(format!("运行时请求超时: {request_id}"))
            }
            Err(mpsc::RecvTimeoutError::Disconnected) => Err("运行时响应通道已断开".into()),
        }
    }

    fn start_process(&self, restarted: bool) -> Result<(), String> {
        let _guard = self.inner.lifecycle.lock().map_err(lock_error)?;
        if self.inner.process.lock().map_err(lock_error)?.is_some() {
            return Ok(());
        }
        self.spawn_locked(restarted)
    }

    fn spawn_locked(&self, restarted: bool) -> Result<(), String> {
        set_status(&self.inner, "starting", None, None, restarted);
        let generation = self.inner.generation.fetch_add(1, Ordering::SeqCst) + 1;
        let (mut command, cwd) = runtime_command(&self.inner.resource_dir);
        command
            .current_dir(cwd)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        if let Some(secret) = read_secret() {
            command.env("AGENT_API_KEY", secret);
        }

        let mut child = command.spawn().map_err(|error| {
            let message = format!("Python runtime 启动失败: {error}");
            set_status(
                &self.inner,
                "disconnected",
                None,
                Some(message.clone()),
                false,
            );
            message
        })?;
        let pid = child.id();
        let stdin = child.stdin.take().ok_or("Python runtime stdin 不可用")?;
        let stdout = child.stdout.take().ok_or("Python runtime stdout 不可用")?;
        let stderr = child.stderr.take().ok_or("Python runtime stderr 不可用")?;
        let stdin = Arc::new(Mutex::new(stdin));

        *self.inner.process.lock().map_err(lock_error)? = Some(ManagedProcess {
            generation,
            child,
            stdin,
        });
        set_status(&self.inner, "connecting", Some(pid), None, false);

        let broker = self.clone();
        std::thread::spawn(move || {
            for line in BufReader::new(stdout).lines() {
                match line {
                    Ok(line) => broker.handle_stdout(&line),
                    Err(error) => {
                        broker.mark_failed(format!("读取 runtime 输出失败: {error}"));
                        break;
                    }
                }
            }
            broker.handle_exit(generation);
        });

        let app = self.inner.app.clone();
        std::thread::spawn(move || {
            for line in BufReader::new(stderr).lines().map_while(Result::ok) {
                let _ = app.emit("runtime-stderr", redact_text(&line));
            }
        });
        self.emit_status();
        Ok(())
    }

    fn stop_locked(&self, reason: &str) -> Result<(), String> {
        // Invalidate reader threads before killing the old process so they cannot
        // schedule an automatic restart after a deliberate restart.
        self.inner.generation.fetch_add(1, Ordering::SeqCst);
        if let Some(mut process) = self.inner.process.lock().map_err(lock_error)?.take() {
            process
                .child
                .kill()
                .map_err(|error| format!("停止 runtime 失败: {error}"))?;
            let _ = process.child.wait();
        }
        self.fail_pending(format!("runtime 已停止: {reason}"));
        set_status(&self.inner, "disconnected", None, None, false);
        self.emit_status();
        Ok(())
    }

    fn send_line(&self, line: &str) -> Result<(), String> {
        let stdin = self
            .inner
            .process
            .lock()
            .map_err(lock_error)?
            .as_ref()
            .map(|process| process.stdin.clone())
            .ok_or("Python runtime 未连接")?;
        let mut stdin = stdin.lock().map_err(lock_error)?;
        stdin
            .write_all(line.as_bytes())
            .and_then(|_| stdin.write_all(b"\n"))
            .and_then(|_| stdin.flush())
            .map_err(|error| format!("写入 Python runtime 失败: {error}"))
    }

    fn handle_stdout(&self, line: &str) {
        let frame = match classify_line(line) {
            Frame::Response(response) => response,
            Frame::Event(event) => {
                if event["type"] == "runtime.ready" {
                    let pid = self.status().pid;
                    set_status(&self.inner, "ready", pid, None, false);
                    self.emit_status();
                }
                let _ = self.inner.app.emit("runtime-event", event);
                return;
            }
            Frame::Invalid => return,
        };

        deliver_response(&self.inner.pending, frame);
    }

    fn handle_exit(&self, generation: u64) {
        if self.inner.generation.load(Ordering::SeqCst) != generation {
            return;
        }
        if let Ok(mut process) = self.inner.process.lock() {
            if process
                .as_ref()
                .is_some_and(|managed| managed.generation == generation)
            {
                process.take();
            }
        }
        self.fail_pending("Python runtime 意外退出".into());
        set_status(
            &self.inner,
            "disconnected",
            None,
            Some("Python runtime 意外退出".into()),
            false,
        );
        self.emit_status();
        self.emit_disconnected_event();

        if !self.inner.shutdown.load(Ordering::SeqCst) {
            let broker = self.clone();
            std::thread::spawn(move || {
                std::thread::sleep(Duration::from_millis(750));
                if broker.inner.generation.load(Ordering::SeqCst) == generation {
                    let _ = broker.start_process(true);
                }
            });
        }
    }

    fn mark_failed(&self, error: String) {
        let safe_error = redact_text(&error);
        set_status(&self.inner, "disconnected", None, Some(safe_error), false);
        self.emit_status();
    }

    fn remove_pending(&self, request_id: &str) {
        if let Ok(mut pending) = self.inner.pending.lock() {
            pending.remove(request_id);
        }
    }

    fn fail_pending(&self, error: String) {
        if let Ok(mut pending) = self.inner.pending.lock() {
            for (_, sender) in pending.drain() {
                let _ = sender.send(Err(error.clone()));
            }
        }
    }

    fn emit_status(&self) {
        let _ = self.inner.app.emit("runtime-status", self.status());
    }

    fn emit_disconnected_event(&self) {
        let millis = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_millis();
        let _ = self.inner.app.emit(
            "runtime-event",
            json!({
                "protocol_version": PROTOCOL_VERSION,
                "event_id": format!("evt_bridge_{millis}"),
                "type": "runtime.disconnected",
                "occurred_at": millis,
                "payload": { "reason": "sidecar_exit" }
            }),
        );
    }
}

impl Drop for RuntimeBroker {
    fn drop(&mut self) {
        // Only the final handle owns shutdown. Tauri normally terminates child
        // processes with the app, but this also prevents late auto-restarts.
        if Arc::strong_count(&self.inner) == 1 {
            self.inner.shutdown.store(true, Ordering::SeqCst);
            if let Ok(mut process) = self.inner.process.lock() {
                if let Some(mut process) = process.take() {
                    let _ = process.child.kill();
                    let _ = process.child.wait();
                }
            }
        }
    }
}

enum Frame {
    Response(Value),
    Event(Value),
    Invalid,
}

fn classify_line(line: &str) -> Frame {
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

fn deliver_response(pending: &Mutex<HashMap<String, mpsc::Sender<Reply>>>, frame: Value) -> bool {
    let Some(request_id) = frame
        .get("request_id")
        .and_then(Value::as_str)
        .map(str::to_owned)
    else {
        return false;
    };
    let Ok(mut pending) = pending.lock() else {
        return false;
    };
    let Some(sender) = pending.remove(&request_id) else {
        return false;
    };
    sender.send(Ok(frame)).is_ok()
}

fn validate_request(request: &Value) -> Result<(), String> {
    if request.get("protocol_version").and_then(Value::as_u64) != Some(PROTOCOL_VERSION) {
        return Err("仅支持 protocol_version=1".into());
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

fn runtime_command(resource_dir: &Path) -> (Command, PathBuf) {
    if let Ok(executable) = std::env::var("AURORA_SIDECAR") {
        let path = PathBuf::from(executable);
        let cwd = path
            .parent()
            .map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from("."));
        let mut command = Command::new(path);
        command.arg("--stdio");
        return (command, cwd);
    }

    let sidecar = resource_dir.join("sidecar");
    let python = sidecar.join("python").join(if cfg!(windows) {
        "python.exe"
    } else {
        "bin/python3"
    });
    if python.exists() {
        let mut command = Command::new(python);
        command.args(["-m", "aurora.cli.main", "runtime"]);
        command.env("PYTHONPATH", sidecar.join("site-packages"));
        return (command, resource_dir.to_path_buf());
    }

    let repo_root = std::env::var_os("AURORA_ROOT")
        .map(PathBuf::from)
        .unwrap_or_else(|| default_backend_root(Path::new(env!("CARGO_MANIFEST_DIR"))));
    let mut command = Command::new("uv");
    command.args(["run", "--no-sync", "aurora", "runtime"]);
    (command, repo_root)
}

fn default_backend_root(manifest_dir: &Path) -> PathBuf {
    manifest_dir
        .parent()
        .and_then(Path::parent)
        .map(|path| path.join("AuroraAgentBackend"))
        .unwrap_or_else(|| PathBuf::from("../AuroraAgentBackend"))
}

fn set_status(
    inner: &BrokerInner,
    state: &str,
    pid: Option<u32>,
    last_error: Option<String>,
    restarted: bool,
) {
    if let Ok(mut status) = inner.status.lock() {
        status.state = state.into();
        status.pid = pid;
        status.last_error = last_error.map(|value| redact_text(&value));
        status.api_key_configured = secret_exists();
        if restarted {
            status.restart_count += 1;
        }
    }
}

fn lock_error<T>(error: std::sync::PoisonError<T>) -> String {
    format!("运行时内部锁异常: {error}")
}

fn keyring_entry() -> Result<keyring::Entry, keyring::Error> {
    keyring::Entry::new(KEYRING_SERVICE, KEYRING_USER)
}

fn read_secret() -> Option<String> {
    keyring_entry().ok()?.get_password().ok()
}

pub fn secret_exists() -> bool {
    read_secret().is_some_and(|secret| !secret.is_empty())
}

pub fn save_secret(secret: &str) -> Result<(), String> {
    keyring_entry()
        .and_then(|entry| entry.set_password(secret))
        .map_err(|_| "API Key 无法写入系统钥匙串".to_string())
}

pub fn delete_secret() -> Result<(), String> {
    let entry = keyring_entry().map_err(|_| "系统钥匙串不可用".to_string())?;
    match entry.delete_credential() {
        Ok(()) | Err(keyring::Error::NoEntry) => Ok(()),
        Err(_) => Err("API Key 无法从系统钥匙串删除".into()),
    }
}

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

fn redact_text(value: &str) -> String {
    let mut sanitized = value.to_owned();
    if let Some(secret) = read_secret().filter(|secret| !secret.is_empty()) {
        sanitized = sanitized.replace(&secret, "[敏感信息已隐藏]");
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
    }

    #[test]
    fn correlates_out_of_order_responses_by_request_id() {
        let pending = Mutex::new(HashMap::new());
        let (sender_a, receiver_a) = mpsc::channel();
        let (sender_b, receiver_b) = mpsc::channel();
        pending.lock().unwrap().insert("req_a".into(), sender_a);
        pending.lock().unwrap().insert("req_b".into(), sender_b);

        assert!(deliver_response(
            &pending,
            json!({"request_id": "req_b", "ok": true, "result": {"value": 2}}),
        ));
        assert_eq!(
            receiver_b
                .recv_timeout(Duration::from_millis(10))
                .unwrap()
                .unwrap()["result"]["value"],
            2
        );
        assert!(receiver_a.try_recv().is_err());
        assert!(pending.lock().unwrap().contains_key("req_a"));
    }

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
        let wire = request.to_string();
        assert!(!wire.contains("secret-value"));
        assert_eq!(request["params"]["api_key_configured"], true);
    }

    #[test]
    fn redacts_sensitive_diagnostics_but_not_stream_tokens() {
        assert_eq!(redact_text("api_key=secret"), "[敏感信息已隐藏]");
        assert_eq!(redact_text("task token delta"), "task token delta");
    }

    #[test]
    fn resolves_backend_beside_frontend_repository() {
        let root = default_backend_root(Path::new("/workspace/AuroraAgentFrontend/src-tauri"));
        assert_eq!(root, PathBuf::from("/workspace/AuroraAgentBackend"));
    }
}
