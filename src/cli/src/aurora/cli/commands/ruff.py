"""ruff 子命令：格式化 Python 代码。"""

from __future__ import annotations

import argparse
import shutil
import subprocess


def register(subparsers: argparse._SubParsersAction) -> None:
    """注册 ruff 代码格式化子命令。"""
    parser = subparsers.add_parser("ruff", help="使用 Ruff 格式化 Python 代码")
    parser.add_argument("paths", nargs="*", default=["."], help="要格式化的文件或目录")
    parser.add_argument("--check", action="store_true", help="仅检查格式，不修改文件")
    parser.set_defaults(handler=_run)


def _run(args: argparse.Namespace) -> int:
    """执行 Ruff formatter 并透传退出码。"""
    executable = shutil.which("ruff")
    if executable is None:
        print("未找到 Ruff，请先运行 uv sync --group dev")
        return 127
    command = [executable, "format"]
    if args.check:
        command.append("--check")
    command.extend(args.paths or ["."])
    return subprocess.run(command, check=False).returncode
