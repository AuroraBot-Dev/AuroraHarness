# uv

项目需要严格使用uv进行环境管理。

# 有关注释

项目中不要出现零散注释，只要用简短语言描述函数功能即可

# 仓库分层

本仓库是单仓分层结构，改动前先确认自己在哪一层：

```
src/frontend  前端（Nuxt SPA）      src/tauri   Tauri 2 壳（胶水层）
src/rust      纯 Rust crate         src/agent   python-agent
src/cli       python-cli
```

依赖方向单向，外层可依赖内层，反向禁止：

- `src/agent` 不得导入 `aurora.cli`（`src/agent/tests/test_layering.py` 会失败）
- `src/rust/aurora-runtime-broker` 不得依赖 `tauri`（CI 会失败）
- 与 Tauri 的耦合只能放在 `src/tauri`，并经 `EventSink` / `SecretStore` 注入给代理

分层详解与层间契约见 `docs/architecture.md`。

# 路径约定

仓库根同时是 uv 工作区根与 cargo 工作区根，因此：

- Python 命令一律在仓库根执行（`uv run pytest`、`uv run ruff check .`、`uv run pyright`）
- Rust 命令也在仓库根（`cargo test --workspace`）
- 前端命令在 `src/frontend` 内执行，且必须用 pnpm 11

# 改动后必须验证

```bash
uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run pyright
cargo test --workspace
cd src/frontend && pnpm lint && pnpm typecheck && pnpm test
```

只跑与改动层次对应的部分即可，但不要跳过。
