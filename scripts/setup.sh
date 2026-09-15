#!/usr/bin/env bash
set -euo pipefail

# 一次性装好仓库的全部开发依赖：Python（uv 工作区）、前端（pnpm）与 Git 钩子。
# 各层的路径都相对于仓库根，不再需要 AURORA_ROOT。

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

for dist in agent cli; do
  if [[ ! -f "src/$dist/pyproject.toml" ]]; then
    echo "错误: 缺少 Python 分布: src/$dist" >&2
    exit 1
  fi
done

# Python：aurora-agent 与 aurora-cli 在同一个 uv 工作区里，一次 sync 全部就绪。
if [[ ! -f src/agent/.env ]]; then
  cp src/agent/.env.example src/agent/.env
fi
uv sync --frozen

# 前端：pnpm install 会顺带执行 nuxt prepare 与 lefthook install（钩子配置在仓库根）。
cd "$REPO_ROOT/src/frontend"
pnpm install --frozen-lockfile

echo "Setup 完成。请检查 src/agent/.env 中的模型配置。"
