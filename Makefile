# AuroraHarness 统一入口。所有目标都从仓库根执行，与 src/ 下的分层一一对应。
#
#   src/frontend 前端（Nuxt SPA）      src/tauri   Tauri 2 壳（胶水层）
#   src/rust      纯 Rust crate        src/agent   python-agent
#   src/cli       python-cli

FRONTEND := src/frontend
TAURI    := src/tauri

.PHONY: setup dev-runtime dev-web dev-desktop lint fmt test test-python test-rust test-web build-sidecar build-desktop clean

# 安装全部依赖（Python 工作区 + 前端 + Git 钩子）
setup:
	@bash scripts/setup.sh

# 只启动 Python 运行时（浏览器联调用的 WebSocket 传输，端口 8765）
dev-runtime:
	@uv run aurora runtime --port 8765

# 只启动前端开发服务器；dev 模块会自动拉起上面的运行时
dev-web:
	@cd $(FRONTEND) && pnpm dev

# 桌面应用开发模式：Tauri 壳 + 前端热更新
dev-desktop:
	@cd $(FRONTEND) && pnpm tauri dev

# 全量质量门禁：Python、Rust、前端各跑一遍
test: lint test-python test-rust test-web

lint:
	@uv run --frozen ruff check .
	@uv run --frozen ruff format --check .
	@uv run --frozen pyright
	@cd $(FRONTEND) && pnpm lint

fmt:
	@uv run --frozen ruff format .
	@uv run --frozen ruff check --fix .
	@cd $(FRONTEND) && pnpm lint:fix

test-python:
	@uv run --frozen pytest

test-rust:
	@cargo test --workspace

test-web:
	@cd $(FRONTEND) && pnpm typecheck
	@cd $(FRONTEND) && pnpm test:coverage

# 打包发行版用的 Python sidecar 到 src/tauri/resources/sidecar
build-sidecar:
	@bash $(TAURI)/scripts/build-sidecar.sh

# 构建桌面发行包：sidecar + 前端静态产物 + Tauri bundle
build-desktop: build-sidecar
	@cd $(FRONTEND) && pnpm tauri build

clean:
	@cargo clean
	@rm -rf $(TAURI)/resources/sidecar
	@rm -rf $(FRONTEND)/.output $(FRONTEND)/.nuxt
