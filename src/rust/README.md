# src/rust（纯 Rust 层）

两个**不依赖 tauri** 的 crate。它们把「与 UI 无关」的部分从桌面壳里抽出来，因此可以脱离
Tauri 单独编译、单独测试——这正是本次拆分要解决的问题。

```
src/rust/
├── aurora-protocol/          # 协议 v1 契约
└── aurora-runtime-broker/    # Python 运行时监管与 NDJSON 代理
```

## aurora-protocol

只描述「线上长什么样」，不碰进程、传输与 UI：

- `PROTOCOL_VERSION`——与 Python 的 `aurora.protocol.PROTOCOL_VERSION`、前端的
  `app/utils/protocol.ts` 三处必须一致
- `validate_request`——交给 Python 之前先校验请求信封
- `classify_line` / `Frame`——把一行 NDJSON 判别为响应、事件或无效

## aurora-runtime-broker

代理的全部逻辑：拉起 Python 子进程、从 stdout 读 NDJSON、按 `request_id` 把响应回送给等待中
的调用方、子进程意外退出后自动重启、状态与事件交给注入的 sink。

它对外要求两个注入点，因此不认识 Tauri：

```rust
pub trait EventSink   { fn status(..); fn event(..); fn stderr(..); }   // 事件往哪去
pub trait SecretStore { fn get(..); fn set(..); fn delete(..); }        // 密钥从哪来
```

| 模块 | 内容 |
|---|---|
| `broker.rs` | `RuntimeBroker` / `BrokerOptions`：进程监管、请求关联、自动重启 |
| `event.rs` | `EventSink` 与用于测试的 `NullEventSink` |
| `secrets.rs` | `SecretStore`、基于系统钥匙串的实现、密钥脱敏、从请求里摘出密钥 |
| `launch.rs` | 决定怎么拉起运行时（三种方式）+ 由壳目录推出仓库根 |
| `status.rs` | `RuntimeStatus` 快照 |

`RuntimeBroker` 的克隆共享同一个子进程与状态，并且只有最后一个句柄负责收尾（`Drop`）——
这样 Tauri 结束应用时不会留下孤儿进程，也不会安排迟到的自动重启。

## 开发

```bash
# 纯 Rust 层：不需要任何桌面系统库，秒级完成
cargo test -p aurora-protocol -p aurora-runtime-broker

# 守住分层：broker 一旦依赖 tauri，这条断言就会失败
cargo tree -p aurora-runtime-broker --prefix none | grep -E '\btauri(-[a-z]+)* v' && exit 1
```

`Cargo.lock` 与 `target/` 都在仓库根——两种工作区（uv / cargo）共用仓库根，见
[`docs/architecture.md`](../../docs/architecture.md)。
