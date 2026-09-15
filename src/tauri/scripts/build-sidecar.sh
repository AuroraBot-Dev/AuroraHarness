#!/usr/bin/env bash
set -euo pipefail

# 把 Python 运行时打包进 Tauri 资源目录，供发行版使用。
#
# 本脚本属于 tauri 层：它决定「打包后的桌面应用怎么启动 runtime」。运行时本体来自
# 两个 Python 分布——装 aurora-cli 就会带出它依赖的 aurora-agent。

TAURI_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "$TAURI_ROOT/../.." && pwd)"

PYTHON_VERSION="${PYTHON_VERSION:-3.13}"
SIDECAR_DIR="$TAURI_ROOT/resources/sidecar"
TMP_VENV="$REPO_ROOT/.tmp-sidecar-venv"

for dist in agent cli; do
  if [[ ! -f "$REPO_ROOT/src/$dist/pyproject.toml" ]]; then
    echo "错误: 缺少 Python 分布: src/$dist" >&2
    exit 1
  fi
done

rm -rf "$SIDECAR_DIR" "$TMP_VENV"
mkdir -p "$SIDECAR_DIR"

uv python install "$PYTHON_VERSION"

PYTHON_BIN="$(uv python find "$PYTHON_VERSION")"
PYTHON_HOME="$(dirname "$(dirname "$PYTHON_BIN")")"
cp -R "$PYTHON_HOME" "$SIDECAR_DIR/python"

uv venv --python "$PYTHON_BIN" "$TMP_VENV"
# 两个分布都显式指定：aurora-cli 依赖的 aurora-agent 只存在于本仓库，不在任何索引上。
uv pip install --python "$TMP_VENV/bin/python" "$REPO_ROOT/src/agent" "$REPO_ROOT/src/cli"

SITE_PACKAGES="$(find "$TMP_VENV" -maxdepth 4 -type d -name site-packages | head -1)"
cp -R "$SITE_PACKAGES" "$SIDECAR_DIR/site-packages"

rm -rf "$TMP_VENV"

echo "sidecar 构建完成: $SIDECAR_DIR"
echo "Python: $(find "$SIDECAR_DIR/python" -name 'python3*' -type f | head -1)"
