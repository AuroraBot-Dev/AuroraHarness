"""分层边界守卫。

依赖方向必须是单向的：``src/cli`` 可以依赖 ``src/agent``，反之不行。
这条守卫把约定变成可执行断言，避免回归——agent 层一旦反向导入 cli，
它就无法再被独立安装与使用。

横向的对应测试在 ``src/cli/tests``；本文件只检查 agent 层的源码。
"""

from __future__ import annotations

import ast
from pathlib import Path

AGENT_SRC = Path(__file__).parents[1] / "src" / "aurora"


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_agent_layer_does_not_import_cli_layer():
    offenders = sorted(
        str(path.relative_to(AGENT_SRC))
        for path in AGENT_SRC.rglob("*.py")
        if any(
            module == "aurora.cli" or module.startswith("aurora.cli.")
            for module in _imported_modules(path)
        )
    )
    assert offenders == []
