"""浏览器截图与预览进程生命周期测试。"""

from __future__ import annotations

import os
import shlex
import socket
import subprocess
import sys
import threading
import time

import pytest

from aurora.agent.preview import BrowserCapture, validate_preview
from aurora.agent.sandbox import Sandbox, UnsafeSubprocessExecutor


def _command(*parts: str) -> str:
    """把命令拼成当前平台命令解释器能执行的字符串。

    预览配置里的 command 是一整条命令串，执行器把原样交给平台的解释器：类 Unix 上是
    ``/bin/sh -c``，Windows 上是 ``powershell -Command``。两者引号规则不同，Windows 上
    既不能沿用 shlex.quote 的单引号（PowerShell 会当成表达式报错），带空格的程序路径还
    必须配合调用运算符 ``&``，所以这里按平台分别拼装。
    """
    if os.name == "nt":
        return "& " + subprocess.list2cmdline(list(parts))
    return shlex.join(parts)


def test_preview_rejects_external_urls_and_path_escape(tmp_path):
    sandbox = Sandbox(tmp_path, executor=UnsafeSubprocessExecutor())
    for config in (
        {"url": "https://example.com"},
        {"url": "http://localhost:3000", "cwd": ".."},
        {"url": "http://localhost:3000", "pages": ["//example.com"]},
        {"url": "http://localhost:3000", "viewports": []},
    ):
        with pytest.raises(ValueError):
            validate_preview(config, sandbox)


def test_preview_command_cancellation_stops_before_timeout(tmp_path):
    sandbox = Sandbox(tmp_path, executor=UnsafeSubprocessExecutor())
    cancelled = threading.Event()
    timer = threading.Timer(0.2, cancelled.set)
    started = time.monotonic()
    timer.start()
    try:
        result = sandbox.run_in(
            ".",
            _command(sys.executable, "-c", "import time; time.sleep(30)"),
            30,
            cancelled,
        )
        assert time.monotonic() - started < 5
        assert result.exit_code != 0
    finally:
        timer.cancel()


def test_real_browser_capture_and_preview_cleanup(tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    # 先确认浏览器可用，再启动预览服务：缺 Chromium 时应当明确跳过，而不是先拉起一个服务、
    # 然后在一次与浏览器无关的等待里失败（macOS 的 CI 机器上就是这样被误报成产品缺陷的）。
    with playwright.sync_playwright() as runtime:
        try:
            runtime.chromium.launch().close()
        except playwright.Error as exc:
            pytest.skip(f"未安装 Chromium（uv run playwright install chromium）：{exc}")

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    (tmp_path / "index.html").write_text("<!doctype html><h1>Aurora preview</h1>", encoding="utf-8")
    config = {
        "url": f"http://127.0.0.1:{port}",
        "command": _command(sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"),
    }
    screenshots, _ = BrowserCapture().capture(
        config, Sandbox(tmp_path, executor=UnsafeSubprocessExecutor())
    )
    assert len(screenshots) == 2
    assert all(data.startswith(b"\x89PNG") for data, _ in screenshots)
    assert [metadata["viewport"]["width"] for _, metadata in screenshots] == [1440, 390]
    with socket.socket() as probe:
        assert probe.connect_ex(("127.0.0.1", port)) != 0
