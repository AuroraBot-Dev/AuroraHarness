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

# 只清生成物：SIDECAR_DIR 自身留着，否则会连带删掉受版本控制的 .gitkeep。
rm -rf "$SIDECAR_DIR/python" "$SIDECAR_DIR/site-packages" "$TMP_VENV"
mkdir -p "$SIDECAR_DIR"

uv python install "$PYTHON_VERSION"

PYTHON_BIN="$(uv python find "$PYTHON_VERSION")"
# 解释器位置随平台不同（Windows 在 prefix 根、Unix 在 bin/），直接问解释器最稳。
PYTHON_HOME="$("$PYTHON_BIN" -c 'import sys; print(sys.prefix)')"
# 解释器自身 site-packages 相对 prefix 的位置：Windows 是 Lib/site-packages，
# Unix 是 lib/python3.x/site-packages。
PREFIX_SITE_REL="$("$PYTHON_BIN" -c 'import sys, sysconfig, os; print(os.path.relpath(sysconfig.get_paths()["purelib"], sys.prefix).replace(os.sep, "/"))')"
cp -R "$PYTHON_HOME" "$SIDECAR_DIR/python"

uv venv --python "$PYTHON_BIN" "$TMP_VENV"

# venv 解释器位置随平台不同：Unix 在 bin/，Windows 在 Scripts/。
if [[ -x "$TMP_VENV/bin/python" ]]; then
  VENV_PYTHON="$TMP_VENV/bin/python"
else
  VENV_PYTHON="$TMP_VENV/Scripts/python.exe"
fi

# 必须先构建 wheel 再安装，不能把源码目录直接交给 uv：工作区成员会被当成 editable 安装，
# 两个可编辑的 aurora 命名空间会互相遮蔽（agent 的 finder 挡住磁盘上的 aurora/cli），
# 运行时会直接 ModuleNotFoundError。wheel 是普通安装，两个分布正常合并到 aurora/ 下。
DIST_DIR="$REPO_ROOT/.tmp-sidecar-dist"
rm -rf "$DIST_DIR"
(cd "$REPO_ROOT" && uv build --all-packages --out-dir "$DIST_DIR")
uv pip install --python "$VENV_PYTHON" "$DIST_DIR"/*.whl

# site-packages 必须合并进解释器自己的 prefix，不能放在旁边再靠 PYTHONPATH 暴露：
# .pth 文件只在解释器自身的 site-packages 里被执行，而 pywin32 正是靠 pywin32.pth 里的
# bootstrap 注册 DLL 目录；走 PYTHONPATH 时它不会运行，Windows 上 import pywintypes 直接失败。
PREFIX_SITE="$SIDECAR_DIR/python/$PREFIX_SITE_REL"
rm -rf "$PREFIX_SITE"
mkdir -p "$PREFIX_SITE"
VENV_SITE_PACKAGES="$(find "$TMP_VENV" -maxdepth 4 -type d -name site-packages | head -1)"
cp -R "$VENV_SITE_PACKAGES"/. "$PREFIX_SITE"/

rm -rf "$TMP_VENV" "$DIST_DIR"

echo "sidecar 构建完成: $SIDECAR_DIR"
echo "解释器:   $SIDECAR_DIR/python"
echo "依赖目录: $PREFIX_SITE"
