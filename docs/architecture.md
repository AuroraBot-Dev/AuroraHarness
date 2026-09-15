# 架构与分层边界

本文说明 AuroraHarness 的五个层次、它们之间的契约，以及这些边界如何被自动守住。
目录树与快速开始见根 [`README.md`](../README.md)。

## 为什么分这些层

合并前的两个仓库里，前端目录同时装着 Nuxt 界面、Tauri 壳和 Rust 代理，后端目录同时装着
运行时和 CLI。这带来两个具体问题：

1. **Rust 代理无法脱离桌面壳测试**——它直接依赖 `tauri::{AppHandle, Emitter}` 来发事件、
   取资源目录，任何单元测试都得先编译整个 Tauri。
2. **agent 层无法脱离 CLI 使用**——CLI 的 argparse 分发与运行时逻辑混在同一个包里。

因此这次拆分的目标不是「目录好看」，而是让每一层都能被单独编译、单独测试、单独安装。

## 层次与职责

| 层 | 路径 | 语言/技术 | 职责 | 明确不做什么 |
|---|---|---|---|---|
| 前端 | `src/frontend` | Nuxt 4 + Vue 3 + TS | 界面、状态管理、协议客户端、E2E | 不碰进程管理，不知道 Python 怎么启动 |
| Tauri 胶水 | `src/tauri` | Rust + Tauri 2 | 暴露命令、把 `AppHandle` 接成事件桥、钥匙串读写、打包配置 | 不含代理逻辑，不知道怎么收发 NDJSON |
| 纯 Rust | `src/rust` | Rust | 协议 v1 契约、Python 子进程监管、请求/响应关联、密钥脱敏 | 不依赖 tauri，不认识 UI |
| python-agent | `src/agent` | Python | 运行时、委派图、工具、沙箱、存储、传输协议 | 不导入 `aurora.cli` |
| python-cli | `src/cli` | Python | 参数解析、子命令注册、退出码 | 不承载业务逻辑 |

## 依赖方向

```
src/frontend ──(Tauri IPC)──▶ src/tauri ──▶ src/rust ──(stdio NDJSON)──▶ src/cli ──▶ src/agent
      │
      └──────────(WebSocket, 仅开发期)────────────────────────────────────────▶ src/cli ──▶ src/agent
```

反向依赖一律禁止。三条最容易被破坏的边界及其实施方式：

| 边界 | 为什么重要 | 如何守住 |
|---|---|---|
| `src/agent` 不导入 `aurora.cli` | agent 必须能脱离 CLI 独立安装 | `src/agent/tests/test_layering.py` 用 AST 扫描全部源码 |
| `src/rust/aurora-runtime-broker` 不依赖 `tauri` | 代理必须能脱离桌面壳编译与测试 | CI 里 `cargo tree -p aurora-runtime-broker` 断言无 tauri |
| 前端不知道 Python 怎么启动 | 换传输方式不应改动界面代码 | 前端只依赖协议客户端（`app/utils/runtimeClient.ts`）与 IPC 命令 |

`src/tauri/src/bridge.rs` 是唯一允许同时知道 Tauri 与代理两边的地方：它把 `AppHandle`
实现成 `EventSink`、把系统钥匙串实现成 `SecretStore`，然后交给代理。代理只认这两个 trait。

## 协议 v1

协议版本号在**三处**必须保持一致，改动时三处同改：

- `src/agent/src/aurora/protocol.py` → `PROTOCOL_VERSION`
- `src/rust/aurora-protocol/src/lib.rs` → `PROTOCOL_VERSION`
- `src/frontend/app/utils/protocol.ts`

线上结构（NDJSON / WebSocket 帧完全相同）：

```jsonc
// 请求：前端 → Python
{ "protocol_version": 1, "request_id": "req_x", "method": "session.create", "params": { } }

// 响应：携带同一 request_id
{ "protocol_version": 1, "request_id": "req_x", "ok": true, "result": { } }

// 事件：服务端推送，与请求无关联
{ "protocol_version": 1, "event_id": "evt_x", "type": "run.started", "occurred_at": 0, "payload": { } }
```

判定规则集中在 `aurora-protocol`：带 `request_id` + `ok` 的是响应，带 `event_id` + `type`
的是事件，其余静默丢弃。同一份规则在 Python 侧（`aurora/agent/transport/api.py`）与前端
（`app/utils/protocol.ts`）各有一份实现。

## Python 子进程的三种启动方式

`src/rust/aurora-runtime-broker/src/launch.rs` 按优先级决定怎么拉起运行时：

| 优先级 | 条件 | 命令 | 工作目录 |
|---|---|---|---|
| 1 | 设置了 `AURORA_SIDECAR` | 该可执行文件 `--stdio` | 可执行文件所在目录 |
| 2 | `resource_dir/sidecar/python` 存在 | 内置解释器 `-m aurora.cli.main runtime` | 资源目录 |
| 3 | 开发态回退 | `uv run --no-sync aurora runtime` | 仓库根（`AURORA_ROOT` 可覆盖） |

开发态回退的工作目录由 `repo_root_from("<repo>/src/tauri")` 推出仓库根——`uv` 必须在
uv 工作区根执行才能解析 `aurora` 命令。

## Tauri CLI 如何定位壳工程

Tauri CLI 只会在当前目录及其**最多三层子目录**里寻找 `tauri.conf.json`，而本仓库把壳放在
`src/tauri`、前端放在 `src/frontend`，两者互不为子目录。因此：

- 本地：`pnpm tauri` 实际执行 `src/tauri/scripts/tauri.mjs`，它显式设置
  `TAURI_APP_PATH` / `TAURI_FRONTEND_PATH` 后再调用 CLI。
- CI：`tauri-action` 的 `projectPath` 设为 `src/tauri`——以该目录为工作目录时，CLI 的
  默认探测恰好能找到 `tauri.conf.json`，并向下找到 `src/frontend/package.json`。

`src/tauri/tauri.conf.json` 里跨层的路径都写成了相对路径：

```jsonc
"beforeDevCommand":  { "script": "pnpm dev",      "cwd": "../frontend" },
"beforeBuildCommand":{ "script": "pnpm generate", "cwd": "../frontend" },
"frontendDist": "../frontend/.output/public"
```

## 工作区布局

仓库根同时是两种工作区的根，这是刻意的：让 `uv run` / `cargo` 从任意子目录都能解析到同一份
锁文件与构建缓存。

```
pyproject.toml     uv 虚拟工作区根（无 [project]）      → uv.lock, .venv/
Cargo.toml         cargo 工作区根                       → Cargo.lock, target/
src/frontend/      pnpm 包（pnpm-workspace.yaml 在此）   → pnpm-lock.yaml
pyrightconfig.json Python 类型检查（只覆盖两层源码）
lefthook.yml       唯一的 Git 钩子来源（git 根）
```

## 已知的偏离与待办

诚实记录当前状态，避免把它们当成「已解决」：

- `AURORA_SIDECAR` 分支会给可执行文件传 `--stdio`，但 `aurora runtime` 并不接受该参数。
  这是拆分前就存在的问题，本次迁移未改动行为，需要单独确认 sidecar 入口约定。
- 协议版本号仍是三处手写常量，尚未做生成或校验（见 `docs/adr/001-protocol-version.md`）。
- `docs/notes/` 下的两份文档记录了合并前两个仓库的工程化过程，其中的目录约定与命令已经
  过时，保留作为历史参考。
