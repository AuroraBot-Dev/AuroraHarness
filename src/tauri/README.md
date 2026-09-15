# src/tauri（Tauri 胶水层）

Aurora Agent 的桌面壳。这一层只做一件事：**把 Tauri 接到 Agent 运行时上**。所有业务逻辑
都在 `src/rust` 与 Python 侧，这里只提供接线与打包配置。

## 职责边界

属于本层：

- 暴露给前端的 IPC 命令（`ping` / `runtime_request` / `runtime_status` / `runtime_restart` / `api_key_status`）
- 把 `AppHandle` 实现成代理需要的 `EventSink`（转发为 `runtime-status`、`runtime-event`、`runtime-stderr`）
- 系统钥匙串实现 `KeyringSecretStore`（Linux 需要 dbus，属于平台集成，因此留在本层）
- 由 crate manifest 推出开发态仓库根
- Tauri 配置、图标、capabilities 与打包所需的 sidecar 目录

不属于本层：进程监管、协议编解码、请求/响应关联、脱敏——都在 `src/rust/aurora-runtime-broker`。

```
src/tauri/
├── Cargo.toml            # package aurora-desktop；依赖两个 aurora-* crate，不直接依赖 keyring
├── build.rs
├── tauri.conf.json       # 跨层路径：beforeDevCommand/beforeBuildCommand/frontendDist
├── capabilities/         # IPC 权限
├── icons/                # 各平台图标
├── resources/sidecar/    # 打包时由 scripts/build-sidecar.sh 生成，产物不入库
├── scripts/
│   ├── build-sidecar.sh  # 把 Python 运行时连同解释器打进来
│   └── tauri.mjs         # 让 Tauri CLI 找到 src/tauri 与 src/frontend
└── src/
    ├── main.rs           # 入口，只调用 aurora_desktop_lib::run()
    ├── lib.rs            # 命令定义与 Builder 装配
    └── bridge.rs         # TauriEventSink / 钥匙串 / 开发态路径推导
```

## 唯一同时认识两边的文件

`src/bridge.rs` 是这一层的存在理由：`aurora-runtime-broker` 只要求一个 `EventSink` 和一个
`SecretStore`，不认识 Tauri；Tauri 也不认识代理。bridge 负责把前者接到后者——事件桥
（`TauriEventSink`）与系统钥匙串（`KeyringSecretStore`）都在这里实现，从而让代理可以脱离
桌面壳编译与测试。

钥匙串的服务名与账号名（`com.aurora.agent` / `model-api-key`）与拆分前完全一致，改动会导致
用户已保存的密钥读不到。钥匙串实现放在本层而非代理层，是因为它属于平台集成：Linux 的
Secret Service 后端要求 dbus，放进纯 Rust 层会让代理被迫依赖系统库。

## sidecar 布局

`scripts/build-sidecar.sh` 产出的结构必须与 `launch.rs` 的约定一致：

```
resources/sidecar/
├── python/                      # 解释器副本（Windows 含 python.exe，Unix 含 bin/python3）
│   └── Lib/site-packages/       # 依赖直接合并进解释器自身的 site-packages
└── ...
```

依赖**必须**合并到解释器自己的 `site-packages`，不能放在旁边再靠 `PYTHONPATH` 暴露：
`.pth` 文件只在解释器自身的 `site-packages` 里被执行，而 pywin32 正是靠 `pywin32.pth` 的
bootstrap 注册 DLL 目录；走 `PYTHONPATH` 时它不会运行，Windows 上 `import pywintypes` 会直接
失败（整个 `mcp` 导入链随之崩掉）。

## 构建与运行

```bash
# 开发模式（会在 ../frontend 里起前端）
cd src/frontend && pnpm tauri dev

# 只检查 Rust 侧
cargo check -p aurora-desktop

# 打包发行版：先生成 sidecar，再构建 bundle
make build-sidecar
cd src/frontend && pnpm tauri build
```

`tauri.conf.json` 中的跨层路径都指回 `../frontend`，Tauri CLI 的定位方式见
[`docs/architecture.md`](../../docs/architecture.md)。
