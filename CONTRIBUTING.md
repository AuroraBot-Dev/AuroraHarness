# 贡献指南

感谢你愿意为 AuroraHarness 出力。本文说明开发环境、分层约定、提交规范与自检清单。

## 开始之前

先读这两份：

- 根 [`README.md`](README.md)——项目定位、分层结构与常用命令
- [`docs/architecture.md`](docs/architecture.md)——层次职责与边界，以及这些边界如何被守住

改动前请确认你要动的层。**跨层改动需要同时更新 `docs/architecture.md`**，因为那里记录了
层间契约。

## 环境准备

前置：`uv`、Node.js 24、pnpm 11、Rust stable；做桌面开发还需要 Tauri 2 的系统依赖。

```bash
make setup
```

这会依次执行 `uv sync`（装好 `aurora-agent` 与 `aurora-cli`）、`pnpm install`
（含 Nuxt 类型生成），并安装 Git 钩子。

模型密钥只允许写在 `src/agent/.env`（已被忽略）或桌面端的系统钥匙串里，**绝不要提交到 Git**。

## Git 钩子

钩子统一由 **lefthook** 管理，配置在仓库根的 [`lefthook.yml`](lefthook.yml)。

改动 Python 文件时跑 ruff check --fix、ruff format 与 pyright；改动前端文件时跑 eslint --fix
与 typecheck。命令会自动切到对应的工作目录，`{staged_files}` 也会被改写成该目录下的相对路径。

> 合并前这里同时存在 lefthook（前端）与 pre-commit（Python）两套配置。它们会争抢同一个
> `.git/hooks/pre-commit`，后安装的一方会让另一方静默失效，因此已统一到 lefthook。
> 不要重新引入 `.pre-commit-config.yaml`。

## 提交规范

使用 [Conventional Commits](https://www.conventionalcommits.org/)，Release Please 依赖它生成
版本与 CHANGELOG：

```
<type>(<scope>): <subject>
```

- `type`：`feat` / `fix` / `refactor` / `perf` / `docs` / `test` / `build` / `ci` / `chore`
- `scope`：建议用层次名，例如 `frontend`、`tauri`、`rust`、`agent`、`cli`、`repo`
- 一次提交只做一件事；跨层重构请拆成可独立审查的提交

分支命名用简短的 kebab-case，例如 `fix-sandbox-permissions`。

## 提交前自检

按改动层次选择，能全跑就全跑（`make test` 会都跑一遍）：

```bash
# Python（在仓库根执行）
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run pyright

# Rust
cargo test --workspace          # 含纯 Rust 两层与 Tauri 壳

# 前端
cd src/frontend
pnpm lint && pnpm typecheck && pnpm test
```

CI 分三条流水线（`.github/workflows/python.yml`、`rust.yml`、`frontend.yml`），都带 `paths`
过滤，只在你改动的层被触发。

## 分层约定（重要）

- **不要**让 `src/agent` 导入 `aurora.cli`。`src/agent/tests/test_layering.py` 会失败。
- **不要**让 `src/rust/aurora-runtime-broker` 依赖 `tauri`。CI 会失败。
- 新增与 Tauri 相关的耦合只能放在 `src/tauri`，并通过 `EventSink` / `SecretStore` 之类的
  trait 注入给代理。
- 改动协议时，三处版本号必须同改：`aurora/protocol.py`、`aurora-protocol/src/lib.rs`、
  `app/utils/protocol.ts`。
- 桌面端需要新的 Python 依赖时，加到 `src/agent/pyproject.toml` 或 `src/cli/pyproject.toml`，
  然后提交更新后的根 `uv.lock`。

## 文档

- 面向使用者与贡献者的说明放仓库根与 `docs/`
- 各层的实现细节放该层的 `README.md`
- 新的架构决策请新增 `docs/adr/` 记录，格式沿用已有文件

## 许可证

贡献即表示同意以 [MIT License](LICENSE) 授权你的成果。行为准则见
[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。
