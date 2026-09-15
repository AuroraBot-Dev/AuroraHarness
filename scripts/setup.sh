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

# 前端：pnpm install 会顺带执行 nuxt prepare。
cd "$REPO_ROOT/src/frontend"
pnpm install --frozen-lockfile

# Git 钩子必须从仓库根安装：唯一有效的配置是根目录的 lefthook.yml。若在子目录里执行，
# lefthook 找不到配置就会就地生成一份游离的默认模板，两处配置并存会导致「到底用了哪份」
# 完全不确定。因此钩子安装放在这里，而不是前端包的 prepare 生命周期脚本里。
cd "$REPO_ROOT"
LEFTHOOK="$REPO_ROOT/src/frontend/node_modules/.bin/lefthook"
if [[ -f "$LEFTHOOK" ]]; then
  "$LEFTHOOK" install
else
  echo "提示: 未找到 lefthook，跳过 Git 钩子安装。" >&2
fi

echo "Setup 完成。请检查 src/agent/.env 中的模型配置。"
