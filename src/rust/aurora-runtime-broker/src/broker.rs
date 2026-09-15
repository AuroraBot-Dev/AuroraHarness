//! 运行时进程监管与 protocol v1 的 NDJSON 代理。

use std::collections::HashMap;
use std::io::{BufRead, BufReader, Write};
use std::path::PathBuf;
use std::process::{Child, ChildStdin, Stdio};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{mpsc, Arc, Mutex};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use aurora_protocol::{classify_line, validate_request, Frame, PROTOCOL_VERSION};
use serde_json::{json, Value};

use crate::event::EventSink;
use crate::launch::runtime_command;
use crate::secrets::{redact_text, SecretStore};
use crate::status::RuntimeStatus;

/// 单次请求的最长等待时间。
const REQUEST_TIMEOUT: Duration = Duration::from_secs(300);
/// 子进程意外退出后的重启延迟。
const RESTART_DELAY: Duration = Duration::from_millis(750);

type Reply = Result<Value, String>;

/// 构造 [`RuntimeBroker`] 所需的外部依赖。
pub struct BrokerOptions {
    /// 应用资源目录：用于定位打包进 bundle 的内置 sidecar。
    pub resource_dir: PathBuf,
    /// 开发态回退目录：`uv run aurora runtime` 在这里执行，通常是仓库根。
    pub dev_repo_root: PathBuf,
    /// 状态、协议事件与诊断输出的出口。
    pub events: Arc<dyn EventSink>,
    /// 模型 API Key 的存取实现。
    pub secrets: Arc<dyn SecretStore>,
}

struct ManagedProcess {
    generation: u64,
    child: Child,
    stdin: Arc<Mutex<ChildStdin>>,
}

struct BrokerInner {
    options: BrokerOptions,
    process: Mutex<Option<ManagedProcess>>,
    pending: Mutex<HashMap<String, mpsc::Sender<Reply>>>,
    status: Mutex<RuntimeStatus>,
    lifecycle: Mutex<()>,
    generation: AtomicU64,
    shutdown: AtomicBool,
}

/// 监管 Python 运行时并代理协议请求。
///
/// 克隆是廉价的：所有克隆共享同一个子进程、待回送表与状态。
#[derive(Clone)]
pub struct RuntimeBroker {
    inner: Arc<BrokerInner>,
}

impl RuntimeBroker {
    pub fn new(options: BrokerOptions) -> Self {
        let status = RuntimeStatus {
            api_key_configured: options.secrets.is_configured(),
            ..RuntimeStatus::default()
        };
        Self {
            inner: Arc::new(BrokerInner {
                options,
                process: Mutex::new(None),
                pending: Mutex::new(HashMap::new()),
                status: Mutex::new(status),
                lifecycle: Mutex::new(()),
                generation: AtomicU64::new(0),
                shutdown: AtomicBool::new(false),
            }),
        }
    }

    /// 当前状态快照。
    pub fn status(&self) -> RuntimeStatus {
        self.inner.status.lock().expect("status lock").clone()
    }

    /// 确保运行时已启动；已启动时直接返回。
    pub fn ensure_started(&self) -> Result<(), String> {
        if self.inner.process.lock().map_err(lock_error)?.is_some() {
            return Ok(());
        }
        self.start_process(false)
    }

    /// 主动重启运行时，用于设置变更后重新注入密钥。
    pub fn restart(&self) -> Result<RuntimeStatus, String> {
        let _guard = self.inner.lifecycle.lock().map_err(lock_error)?;
        self.stop_locked("manual restart")?;
        self.spawn_locked(true)?;
        Ok(self.status())
    }

    /// 发送一条请求，并等待携带同一 `request_id` 的响应。
    ///
    /// 事件不在此返回，它们通过 [`EventSink`] 单独推送。
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
        self.set_status("starting", None, None, restarted);
        let generation = self.inner.generation.fetch_add(1, Ordering::SeqCst) + 1;
        let launch = runtime_command(
            &self.inner.options.resource_dir,
            &self.inner.options.dev_repo_root,
        );
        let mut command = launch.command;
        command
            .current_dir(launch.cwd)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        if let Some(secret) = self.inner.options.secrets.get() {
            command.env("AGENT_API_KEY", secret);
        }

        let mut child = command.spawn().map_err(|error| {
            let message = format!("Python runtime 启动失败: {error}");
            self.set_status("disconnected", None, Some(message.clone()), false);
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
        self.set_status("connecting", Some(pid), None, false);

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

        let broker = self.clone();
        std::thread::spawn(move || {
            for line in BufReader::new(stderr).lines().map_while(Result::ok) {
                broker.emit_stderr(&line);
            }
        });
        self.emit_status();
        Ok(())
    }

    fn stop_locked(&self, reason: &str) -> Result<(), String> {
        // 先让读线程失效，再杀进程：否则它们会在「主动重启」之后又安排一次自动重启。
        self.inner.generation.fetch_add(1, Ordering::SeqCst);
        if let Some(mut process) = self.inner.process.lock().map_err(lock_error)?.take() {
            process
                .child
                .kill()
                .map_err(|error| format!("停止 runtime 失败: {error}"))?;
            let _ = process.child.wait();
        }
        self.fail_pending(format!("runtime 已停止: {reason}"));
        self.set_status("disconnected", None, None, false);
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
                    self.set_status("ready", pid, None, false);
                    self.emit_status();
                }
                self.emit_event(event);
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
        self.set_status(
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
                std::thread::sleep(RESTART_DELAY);
                if broker.inner.generation.load(Ordering::SeqCst) == generation {
                    let _ = broker.start_process(true);
                }
            });
        }
    }

    fn mark_failed(&self, error: String) {
        let safe_error = self.redact(&error);
        self.set_status("disconnected", None, Some(safe_error), false);
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
        self.inner.options.events.status(&self.status());
    }

    fn emit_event(&self, event: Value) {
        self.inner.options.events.event(event);
    }

    fn emit_stderr(&self, line: &str) {
        self.inner.options.events.stderr(&self.redact(line));
    }

    fn emit_disconnected_event(&self) {
        let millis = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_millis();
        self.emit_event(json!({
            "protocol_version": PROTOCOL_VERSION,
            "event_id": format!("evt_bridge_{millis}"),
            "type": "runtime.disconnected",
            "occurred_at": millis,
            "payload": { "reason": "sidecar_exit" }
        }));
    }

    /// 用当前密钥对文本脱敏；每次读取，保证设置变更后立即生效。
    fn redact(&self, value: &str) -> String {
        redact_text(value, self.inner.options.secrets.get().as_deref())
    }

    fn set_status(
        &self,
        state: &str,
        pid: Option<u32>,
        last_error: Option<String>,
        restarted: bool,
    ) {
        if let Ok(mut status) = self.inner.status.lock() {
            status.state = state.into();
            status.pid = pid;
            status.last_error = last_error.map(|value| self.redact(&value));
            status.api_key_configured = self.inner.options.secrets.is_configured();
            if restarted {
                status.restart_count += 1;
            }
        }
    }
}

impl Drop for RuntimeBroker {
    fn drop(&mut self) {
        // 只有最后一个句柄负责收尾：Tauri 通常会随应用结束终止子进程，这里额外保证
        // 不会再安排迟到的自动重启。
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

fn lock_error<T>(error: std::sync::PoisonError<T>) -> String {
    format!("运行时内部锁异常: {error}")
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

#[cfg(test)]
mod tests {
    use super::*;

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
    fn ignores_responses_without_request_id() {
        let pending = Mutex::new(HashMap::new());
        assert!(!deliver_response(&pending, json!({"ok": true})));
    }
}
