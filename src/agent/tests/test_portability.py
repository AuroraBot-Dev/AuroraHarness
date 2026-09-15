"""跨平台守卫：文本文件读写必须显式声明编码。

`open()` / `Path.read_text()` / `Path.write_text()` 不指定编码时用的是宿主 locale 编码：
macOS 与 Linux 上是 UTF-8，而英文 Windows 上是 cp1252。于是「在 macOS 上写、在 macOS 上测」
都正常的代码，一遇到中文文件名或中文内容就会在 Windows 上抛 UnicodeEncodeError，或者
静默解出乱码路径——这类问题只在别人的机器上出现，因此适合做成可执行断言。

与 `test_layering.py` 同一思路：把约定写成测试，而不是写在文档里等人遵守。
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).parents[3]
SOURCE_ROOTS = [REPO_ROOT / "src" / "agent", REPO_ROOT / "src" / "cli"]

# `os.open` 返回文件描述符，只做二进制读写，不需要编码。
_BINARY_ONLY_RECEIVERS = {"os"}
# urllib 风格的 `opener.open(url, timeout=...)` 不是文件读写，用 timeout 参数即可区分。
_URL_TIMEOUT_KWARG = "timeout"
_TEXT_IO_METHODS = {"read_text", "write_text"}


def _iter_python_files():
    for root in SOURCE_ROOTS:
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" not in path.parts:
                yield path


def _is_binary_mode(call: ast.Call, position: int) -> bool:
    """判断这次调用的模式是否为二进制（二进制读写不涉及编码）。"""
    for keyword in call.keywords:
        if keyword.arg == "mode":
            return "b" in ast.unparse(keyword.value)
    if len(call.args) > position:
        return "b" in ast.unparse(call.args[position])
    return False


def _declares_encoding(call: ast.Call) -> bool:
    return any(keyword.arg == "encoding" for keyword in call.keywords)


def _needs_encoding(call: ast.Call) -> bool:
    """判断这次调用是否属于「文本文件读写却没给编码」。"""
    if _declares_encoding(call):
        return False

    func = call.func
    if isinstance(func, ast.Attribute):
        if func.attr in _TEXT_IO_METHODS:
            return True
        if func.attr != "open":
            return False
        if isinstance(func.value, ast.Name) and func.value.id in _BINARY_ONLY_RECEIVERS:
            return False
        if any(keyword.arg == _URL_TIMEOUT_KWARG for keyword in call.keywords):
            return False
        return not _is_binary_mode(call, 0)

    if isinstance(func, ast.Name) and func.id == "open":
        return not _is_binary_mode(call, 1)

    return False


def test_text_file_io_declares_encoding():
    offenders = []
    for path in _iter_python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _needs_encoding(node):
                offenders.append(f"{path.relative_to(REPO_ROOT).as_posix()}:{node.lineno}")
    assert offenders == []
