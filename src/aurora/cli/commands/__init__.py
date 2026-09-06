"""CLI 子命令：一个命令对应一个模块。"""

from . import demo, evaluate, ruff, runtime, sandbox, serve

__all__ = ["demo", "evaluate", "ruff", "runtime", "sandbox", "serve"]
