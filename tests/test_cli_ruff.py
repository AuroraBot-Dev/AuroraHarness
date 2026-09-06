"""Ruff CLI 子命令测试。"""

from __future__ import annotations

from unittest.mock import Mock

from aurora.cli.commands import ruff
from aurora.cli.main import build_parser


def test_ruff_command_defaults_to_formatting_current_directory(monkeypatch):
    """默认执行当前目录的 Ruff formatter。"""
    completed = Mock(returncode=0)
    monkeypatch.setattr(ruff.shutil, "which", lambda name: "/tools/ruff")
    run = Mock(return_value=completed)
    monkeypatch.setattr(ruff.subprocess, "run", run)

    args = build_parser().parse_args(["ruff"])

    assert args.handler(args) == 0
    run.assert_called_once_with(["/tools/ruff", "format", "."], check=False)


def test_ruff_command_supports_check_and_paths(monkeypatch):
    """检查模式和显式路径会传递给 Ruff。"""
    monkeypatch.setattr(ruff.shutil, "which", lambda name: "/tools/ruff")
    run = Mock(return_value=Mock(returncode=1))
    monkeypatch.setattr(ruff.subprocess, "run", run)

    args = build_parser().parse_args(["ruff", "--check", "src", "tests"])

    assert args.handler(args) == 1
    run.assert_called_once_with(
        ["/tools/ruff", "format", "--check", "src", "tests"],
        check=False,
    )


def test_ruff_command_reports_missing_executable(monkeypatch, capsys):
    """缺少 Ruff 时返回标准的 command-not-found 退出码。"""
    monkeypatch.setattr(ruff.shutil, "which", lambda name: None)
    args = build_parser().parse_args(["ruff"])

    assert args.handler(args) == 127
    assert "uv sync --group dev" in capsys.readouterr().out
