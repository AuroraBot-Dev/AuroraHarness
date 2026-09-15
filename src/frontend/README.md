# Aurora Agent Frontend

Aurora 的 Nuxt 4、Vue 3、TypeScript、Naive UI、Pinia 与 Tauri 2 桌面前端。

## 快速开始

需要 Node.js 24、pnpm 11、Rust stable 和后端要求的 Python/uv。推荐直接运行 `pnpm setup`，随后运行 `pnpm dev`。

## 前后端位置

默认目录结构：

```text
GitHub/
├─ AuroraAgentBackend/   # Python 后端
└─ AuroraAgentFrontend/  # Vue/Tauri 前端
```

如果后端不在同级目录，设置绝对路径：

```bash
export AURORA_ROOT=/absolute/path/to/AuroraAgentBackend
```

## 浏览器开发

首次开发可在前端目录一键安装两个仓库的依赖并生成后端 `.env`：

```bash
pnpm setup
```

也可以手动在后端根目录配置模型：

```bash
cd ../AuroraAgentBackend
cp .env.example .env
# 填写 AGENT_API_KEY、AGENT_BASE_URL、AGENT_MODEL
uv sync
```

再启动前端：

```bash
cd ../AuroraAgentFrontend
pnpm install
pnpm dev
```

打开 `http://127.0.0.1:3000`。Nuxt 会通过 `uv` 自动启动 Python WebSocket 运行时；如需连接其他运行时，可设置 `VITE_RUNTIME_WS`。

## Tauri 桌面开发

准备 Python、Node.js 20+、pnpm 11、Rust stable 与 Tauri 2 系统依赖后运行：

```bash
cd ../AuroraAgentBackend
uv sync

cd ../AuroraAgentFrontend
pnpm install
pnpm tauri dev
```

Tauri 会通过 stdio 自动启动同级 Aurora Python 后端，因此不需要另开后端终端。

## 检查与构建

```bash
pnpm lint
pnpm typecheck
pnpm test:coverage
pnpm test:e2e
pnpm generate

cd src-tauri
cargo test --locked
cargo check --locked
```

发布桌面应用前：

```bash
cd AuroraAgentFrontend
bash build-sidecar.sh
pnpm tauri build
```

当前后端会话存于内存，重启运行时后需要新建会话。工作区列表仅保存在浏览器本地；模型配置由 Python 后端 `.env` 管理。

## 架构概览

Nuxt SPA 负责界面与状态管理；浏览器开发模式通过 WebSocket 通信，Tauri 桌面模式由 Rust broker 通过 stdio 管理 Python sidecar。两种传输共享同一协议版本和事件结构。

## 目录结构

- `app/`：页面、组件、Store、协议类型和运行时客户端。
- `modules/`：Nuxt 开发期后端启动器。
- `src-tauri/`：桌面壳、stdio broker 与打包配置。
- `tests/`、`e2e/`：单元测试和 Playwright 测试。

## 开发指南

分支、提交和 PR 规范见 [`CONTRIBUTING.md`](CONTRIBUTING.md)。`pnpm install` 会安装 lefthook；也可运行 `pnpm lefthook install` 重新安装提交钩子。

模型密钥只允许写入后端 `.env` 或系统钥匙串，绝不要提交到 Git。

## 部署与发布

版本遵循 SemVer。Release Please 根据 Conventional Commits 自动维护版本 PR、`CHANGELOG.md` 及前端/Tauri 版本号；版本 PR 合并产生的 `v*` tag 会构建并上传 Tauri 安装包。发布前必须完成代码签名与平台密钥配置。

## 常见问题

- 找不到后端：保持两个仓库同级，或设置 `AURORA_ROOT`。
- 端口冲突：设置 `VITE_RUNTIME_WS` 后手动管理 WebSocket 后端。
- 桌面运行时失败：检查 `uv`、sidecar 资源以及系统钥匙串权限。

## 许可证

本项目采用 [MIT License](LICENSE)。
