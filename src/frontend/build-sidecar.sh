#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_ROOT="${AURORA_ROOT:-$ROOT/../AuroraAgentBackend}"
cd "$ROOT"

if [[ ! -f "$BACKEND_ROOT/pyproject.toml" ]]; then
  echo "错误: 后端目录无效: $BACKEND_ROOT" >&2
  echo "请将 AuroraAgentBackend 与 AuroraAgentFrontend 放在同一目录，或设置 AURORA_ROOT。" >&2
  exit 1
fi

PYTHON_VERSION="${PYTHON_VERSION:-3.13}"
SIDECAR_DIR="$ROOT/src-tauri/resources/sidecar"
TMP_VENV="$ROOT/.tmp-sidecar-venv"

rm -rf "$SIDECAR_DIR" "$TMP_VENV"
mkdir -p "$SIDECAR_DIR"

uv python install "$PYTHON_VERSION"

PYTHON_BIN="$(uv python find "$PYTHON_VERSION")"
PYTHON_HOME="$(dirname "$(dirname "$PYTHON_BIN")")"
cp -R "$PYTHON_HOME" "$SIDECAR_DIR/python"

uv venv --python "$PYTHON_BIN" "$TMP_VENV"
uv pip install --python "$TMP_VENV/bin/python" "$BACKEND_ROOT"

SITE_PACKAGES="$(find "$TMP_VENV" -maxdepth 4 -type d -name site-packages | head -1)"
cp -R "$SITE_PACKAGES" "$SIDECAR_DIR/site-packages"

rm -rf "$TMP_VENV"

echo "sidecar 构建完成: $SIDECAR_DIR"
echo "Python: $(find "$SIDECAR_DIR/python" -name 'python3*' -type f | head -1)"
