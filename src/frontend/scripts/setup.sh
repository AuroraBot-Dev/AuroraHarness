#!/usr/bin/env bash
set -euo pipefail

FRONTEND_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_ROOT="${AURORA_ROOT:-$FRONTEND_ROOT/../AuroraAgentBackend}"

if [[ ! -f "$BACKEND_ROOT/pyproject.toml" ]]; then
  echo "错误: 后端目录无效: $BACKEND_ROOT" >&2
  echo "请将 AuroraAgentBackend 与 AuroraAgentFrontend 放在同一目录，或设置 AURORA_ROOT。" >&2
  exit 1
fi

cd "$FRONTEND_ROOT"
pnpm install --frozen-lockfile

cd "$BACKEND_ROOT"
if [[ ! -f .env ]]; then
  cp .env.example .env
fi
uv sync --frozen

echo "Setup 完成。请检查 $BACKEND_ROOT/.env 中的模型配置。"
