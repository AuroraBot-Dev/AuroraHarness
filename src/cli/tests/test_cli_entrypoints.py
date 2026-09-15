"""cli 层入口测试：子命令注册与终端渲染。

这些用例原先散落在 agent 层测试里，按分层边界迁入此处——
agent 层不得依赖 cli 层，反向依赖只允许出现在本层测试中。
"""

from __future__ import annotations

import pytest
from rich.console import Console

from aurora.agent.conversation import SessionReply
from aurora.cli.commands.serve import _render_reply
from aurora.cli.main import build_parser


def test_console_uses_agent_reply_label():
    console = Console(record=True, force_terminal=False)
    _render_reply(console, SessionReply(text="这是正文"))
    output = console.export_text()
    assert "agent>> 这是正文" in output
    assert "aurora>" not in output


def test_runtime_command_is_registered():
    args = build_parser().parse_args(["runtime"])
    assert args.command == "runtime"
    assert args.port is None


def test_runtime_accepts_stdio_flag_exclusively():
    """--stdio 是 stdio 传输的显式写法，供外部包装器（如打包 sidecar）调用。"""
    args = build_parser().parse_args(["runtime", "--stdio"])
    assert args.stdio is True
    assert args.port is None

    with pytest.raises(SystemExit):
        build_parser().parse_args(["runtime", "--stdio", "--port", "8765"])


def test_serve_command_is_registered():
    args = build_parser().parse_args(["serve", "--mode", "read-only", "--approve", "never"])
    assert args.command == "serve"
    assert args.sandbox_dir == "."
    assert args.mode == "read-only"
    assert args.approve == "never"
