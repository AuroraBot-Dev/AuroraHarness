"""验证运行取消、审批失效、进程回收与传输响应。"""

from __future__ import annotations

import asyncio
import json
import os
import queue
import socket
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress

import pytest
from langchain_core.messages import AIMessage
from test_agent_registry import setup_registry
from test_persistence import Model, runtime_at, visual_session
from websockets.asyncio.client import connect

from aurora.agent.core import Effort
from aurora.agent.sandbox.executor import run_process
from aurora.agent.transport import RuntimeApi, serve_ndjson, serve_websocket
from aurora.common.cancellation import cancellation_scope


def wait_until(predicate, timeout=10):
    """在限定时间内等待可观察的执行条件。"""
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            pytest.fail("等待执行条件超时")
        time.sleep(0.01)


def test_cancel_waiting_expires_approval_and_is_idempotent(tmp_path):
    runtime, workspace, _, _ = runtime_at(tmp_path)
    try:
        session = runtime.create_session(str(workspace), approval_mode="interactive")
        paused = session.start("创建文件")
        assert paused.status == "waiting"
        accepted, cancelled = runtime.cancel_run(session.id, paused.run_id)
        assert accepted and cancelled.status == "cancelled"
        assert not cancelled.interruptions
        assert cancelled.state["tasks"][0]["status"] == "cancelled"
        assert not (workspace / "index.html").exists()
        assert runtime.records.db.one("SELECT status FROM interactions")["status"] == "expired"
        before = runtime.records.db.one("SELECT COUNT(*) AS n FROM messages")["n"]
        assert runtime.cancel_run(session.id, paused.run_id) == (True, None)
        assert runtime.records.db.one("SELECT COUNT(*) AS n FROM messages")["n"] == before
        assert runtime.records.db.one("SELECT status FROM agent_runs")["status"] == "cancelled"
        with pytest.raises(ValueError):
            session.resume(paused.run_id, {"approved": True}, paused.interruptions[0].id)
        assert session.start("新任务", mode="say").status == "completed"
    finally:
        runtime.close()


def test_cancel_validates_ownership_and_preserves_completed_run(tmp_path):
    runtime, workspace, _, _ = runtime_at(tmp_path)
    try:
        session = runtime.create_session(str(workspace))
        other = runtime.create_session(str(workspace))
        completed = session.start("你好", mode="say")
        api = RuntimeApi(runtime)

        def cancel(**params):
            return api.handle({"id": "cancel", "method": "run.cancel", "params": params})[0]

        assert cancel(session_id=session.id, run_id=completed.run_id)["result"] == {
            "accepted": False
        }
        assert cancel(session_id="missing", run_id=completed.run_id)["error"]["code"] == (
            "SESSION_NOT_FOUND"
        )
        assert cancel(session_id=other.id, run_id=completed.run_id)["error"]["code"] == (
            "RUN_NOT_FOUND"
        )
        assert "error" in cancel(session_id=session.id, sessionId=other.id, run_id=completed.run_id)
        assert runtime.records.get("runs", completed.run_id)["status"] == "completed"
    finally:
        runtime.close()


def test_cancel_model_waits_for_safe_point_and_releases_workspace(tmp_path):
    runtime, workspace, _, _ = runtime_at(tmp_path)
    entered, release = threading.Event(), threading.Event()

    class BlockingModel:
        """模拟尚未返回的同步模型请求。"""

        def invoke(self, messages):
            entered.set()
            assert release.wait(10)
            return AIMessage(content="迟到的回答")

    runtime._configured_llm_factory = lambda config: BlockingModel()
    try:
        session = runtime.create_session(str(workspace))
        with ThreadPoolExecutor(max_workers=1) as workers:
            future = workers.submit(session.start, "你好", mode="say")
            try:
                assert entered.wait(10)
                run_id = runtime.records.db.one("SELECT id FROM runs")["id"]
                assert runtime.cancel_run(session.id, run_id) == (True, None)
                assert not future.done()
                assert runtime.records.get("runs", run_id)["status"] == "running"
            finally:
                release.set()
            assert future.result(timeout=10).status == "cancelled"
        messages = runtime.records.db.all("SELECT content_json FROM messages")
        assert all("迟到的回答" not in row["content_json"] for row in messages)
        assert session.start("下一轮", mode="say").status == "completed"
    finally:
        release.set()
        runtime.close()


@pytest.mark.parametrize("parallel", [False, True])
def test_cancel_kills_process_trees_and_suppresses_later_tasks(tmp_path, parallel):
    runtime, workspace, _, _ = runtime_at(tmp_path)
    count = 2 if parallel else 1
    child = (
        "import pathlib,time; "
        "p=pathlib.Path('.'); "
        "\nwhile not (p/'release').exists(): time.sleep(0.01)"
        "\n(p/'leaked').write_text('unexpected')"
    )

    class ProcessPlanner:
        """先执行长进程，再安排一个不应启动的写入。"""

        def plan(self, goal):
            tasks = [
                {
                    "id": str(index),
                    "description": "等待取消",
                    "effort": Effort.LOW,
                    "tool": "run_python",
                    "args": {
                        "code": (
                            "import pathlib,subprocess,sys,time\n"
                            f"subprocess.Popen([sys.executable, '-c', {child!r}])\n"
                            f"pathlib.Path('ready{index}').write_text('ready')\n"
                            "time.sleep(20)"
                        ),
                        "timeout": 25,
                    },
                }
                for index in range(count)
            ]
            if not parallel:
                tasks.append(
                    {
                        "id": "later",
                        "description": "后续写入",
                        "effort": Effort.LOW,
                        "tool": "write_file",
                        "args": {"path": "later.txt", "content": "unexpected"},
                    }
                )
            return tasks

    runtime._planner_factory = lambda llm, tools: ProcessPlanner()
    if parallel:
        agent = runtime.records.list_configs("agent")[0]
        runtime.records.save("agent", {"allowed_tools": ["run_python"]}, agent["id"])
    try:
        session = runtime.create_session(
            str(workspace), approval_mode="always" if parallel else "interactive"
        )
        paused = session.start("执行") if not parallel else None
        with ThreadPoolExecutor(max_workers=1) as workers:
            future = (
                workers.submit(session.start, "执行")
                if parallel
                else workers.submit(session.resume, paused.run_id, {"approved": True})
            )
            try:
                wait_until(lambda: all((workspace / f"ready{i}").exists() for i in range(count)))
                run_id = runtime.records.db.one("SELECT id FROM runs")["id"]
                assert runtime.cancel_run(session.id, run_id) == (True, None)
                assert future.result(timeout=10).status == "cancelled"
            finally:
                if not future.done():
                    row = runtime.records.db.one("SELECT id FROM runs")
                    runtime.cancel_run(session.id, row["id"])
                (workspace / "release").touch()
            time.sleep(0.15)
        assert not (workspace / "leaked").exists()
        assert not (workspace / "later.txt").exists()
        assert all(
            row["status"] == "cancelled"
            for row in runtime.records.db.all("SELECT status FROM tasks")
        )
    finally:
        (workspace / "release").touch()
        runtime.close()


def test_cancel_delegation_preserves_completed_work(tmp_path, monkeypatch):
    runtime, workspace, _, _ = setup_registry(tmp_path, monkeypatch)
    try:
        session = runtime.create_session(str(workspace), approval_mode="interactive")
        paused = session.start("委派工作")
        paused = session.resume(paused.run_id, {"approved": True})
        assert (workspace / "1.txt").read_text(encoding="utf-8") == "1"
        assert paused.status == "waiting"
        accepted, cancelled = runtime.cancel_run(session.id, paused.run_id)
        assert accepted and cancelled.status == "cancelled"
        assert not (workspace / "2.txt").exists()
        assert (workspace / "1.txt").read_text(encoding="utf-8") == "1"
        statuses = [row["status"] for row in runtime.records.db.all("SELECT status FROM tasks")]
        assert "completed" in statuses and "cancelled" in statuses
        assert not set(statuses) & {"queued", "running", "waiting"}
        assert not {
            row["status"] for row in runtime.records.db.all("SELECT status FROM agent_runs")
        } & {"running", "waiting"}
    finally:
        runtime.close()


@pytest.mark.skipif(os.name != "posix", reason="POSIX 进程组继承管道场景")
def test_cancel_drains_descendant_pipes_after_parent_exit(tmp_path):
    signal = threading.Event()
    child = (
        "import pathlib,time\n"
        "pathlib.Path('ready').touch()\n"
        "while not pathlib.Path('release').exists(): time.sleep(0.01)\n"
        "pathlib.Path('leaked').touch()"
    )

    def execute():
        with cancellation_scope(signal):
            return run_process(
                [
                    sys.executable,
                    "-c",
                    f"import subprocess,sys; subprocess.Popen([sys.executable, '-c', {child!r}])",
                ],
                cwd=tmp_path,
                timeout=10,
            )

    with ThreadPoolExecutor(max_workers=1) as workers:
        future = workers.submit(execute)
        try:
            wait_until(lambda: (tmp_path / "ready").exists())
            signal.set()
            assert not future.result(timeout=3).timed_out
        finally:
            signal.set()
            (tmp_path / "release").touch()
    time.sleep(0.1)
    assert not (tmp_path / "leaked").exists()


def test_cancel_capture_does_not_record_review_failure(tmp_path):
    entered, release = threading.Event(), threading.Event()

    class Capture:
        """在截图返回前等待外部信号。"""

        def capture(self, config, sandbox):
            entered.set()
            assert release.wait(10)
            raise ValueError("截图关闭")

    runtime, workspace, _, _ = runtime_at(tmp_path, capture=Capture())
    try:
        session = visual_session(runtime, workspace)
        with ThreadPoolExecutor(max_workers=1) as workers:
            future = workers.submit(session.start, "构建页面")
            try:
                assert entered.wait(10)
                run_id = runtime.records.db.one("SELECT id FROM runs")["id"]
                runtime.cancel_run(session.id, run_id)
            finally:
                release.set()
            assert future.result(timeout=10).status == "cancelled"
        assert runtime.records.db.one("SELECT COUNT(*) AS n FROM reviews")["n"] == 0
    finally:
        release.set()
        runtime.close()


def test_cancel_review_discards_late_verdict(tmp_path):
    runtime, workspace, _, calls = runtime_at(tmp_path)
    entered, release = threading.Event(), threading.Event()

    class BlockingReview(Model):
        """在视觉模型返回前等待取消请求。"""

        def invoke(self, messages):
            if self.review:
                entered.set()
                assert release.wait(10)
            return super().invoke(messages)

    runtime._configured_llm_factory = lambda config: BlockingReview(["passed"], calls)
    try:
        session = visual_session(runtime, workspace)
        with ThreadPoolExecutor(max_workers=1) as workers:
            future = workers.submit(session.start, "构建页面")
            try:
                assert entered.wait(10)
                run_id = runtime.records.db.one("SELECT id FROM runs")["id"]
                runtime.cancel_run(session.id, run_id)
            finally:
                release.set()
            assert future.result(timeout=10).status == "cancelled"
        assert runtime.records.db.one("SELECT COUNT(*) AS n FROM reviews")["n"] == 0
        assert (workspace / "index.html").exists()
    finally:
        release.set()
        runtime.close()


def test_cancel_after_completion_does_not_change_terminal_event(tmp_path):
    runtime, workspace, _, _ = runtime_at(tmp_path)
    try:
        session = runtime.create_session(str(workspace))
        accepted = []

        def progress(run_id, node, update):
            if node == "summarize":
                accepted.append(runtime.cancel_run(session.id, run_id)[0])

        result = session.start("你好", progress, mode="say")
        assert result.status == "completed"
        assert accepted == [False]
        assert (
            runtime.records.db.one(
                "SELECT COUNT(*) AS n FROM run_events WHERE type='run.cancelled'"
            )["n"]
            == 0
        )
    finally:
        runtime.close()


def test_stdio_cancel_bypasses_saturated_run_workers(tmp_path):
    runtime, workspace, _, _ = runtime_at(tmp_path)
    release = threading.Event()
    entered = queue.Queue()
    incoming, outgoing = queue.Queue(), queue.Queue()

    class BlockingModel:
        """占用工作线程直到释放。"""

        def invoke(self, messages):
            entered.put(True)
            assert release.wait(15)
            return AIMessage(content="完成")

    class Input:
        """逐行提供测试协议请求。"""

        def __iter__(self):
            return iter(incoming.get, None)

    class Output:
        """收集完整的协议帧。"""

        def write(self, line):
            outgoing.put(json.loads(line))

        def flush(self):
            pass

    def send(request_id, method, params):
        incoming.put(
            json.dumps(
                dict(protocol_version=1, request_id=request_id, method=method, params=params)
            )
        )

    runtime._configured_llm_factory = lambda config: BlockingModel()
    try:
        sessions = []
        for index in range(4):
            root = workspace / str(index)
            root.mkdir()
            sessions.append(runtime.create_session(str(root)))
        with ThreadPoolExecutor(max_workers=1) as workers:
            serving = workers.submit(serve_ndjson, RuntimeApi(runtime), Input(), Output())
            try:
                for index, session in enumerate(sessions):
                    send(
                        str(index), "run.start", dict(sessionId=session.id, goal="等待", mode="say")
                    )
                for _ in sessions:
                    entered.get(timeout=10)
                row = runtime.records.db.one("SELECT id,session_id FROM runs LIMIT 1")
                send("stop", "run.cancel", dict(session_id=row["session_id"], run_id=row["id"]))
                deadline = time.monotonic() + 3
                while True:
                    frame = outgoing.get(timeout=max(0.01, deadline - time.monotonic()))
                    if frame.get("request_id") == "stop":
                        assert frame["result"] == {"accepted": True}
                        break
                assert not release.is_set()
            finally:
                release.set()
                incoming.put(None)
            serving.result(timeout=10)
    finally:
        release.set()
        runtime.close()


def test_websocket_cancel_bypasses_saturated_run_workers(tmp_path):
    runtime, workspace, _, _ = runtime_at(tmp_path)
    entered, release = queue.Queue(), threading.Event()

    class BlockingModel:
        """占用 WebSocket 长请求槽位直到取消确认。"""

        def invoke(self, messages):
            entered.put(True)
            assert release.wait(15)
            return AIMessage(content="完成")

    async def scenario():
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        serving = asyncio.create_task(serve_websocket(RuntimeApi(runtime), port=port))
        try:
            for _ in range(100):
                try:
                    websocket = await connect(f"ws://127.0.0.1:{port}/ws")
                    break
                except OSError:
                    await asyncio.sleep(0.01)
            else:
                pytest.fail("WebSocket 服务未启动")
            async with websocket:
                try:
                    for index in range(4):
                        root = workspace / str(index)
                        root.mkdir()
                        session = runtime.create_session(str(root))
                        await websocket.send(
                            json.dumps(
                                dict(
                                    protocol_version=1,
                                    request_id=str(index),
                                    method="run.start",
                                    params=dict(sessionId=session.id, goal="等待", mode="say"),
                                )
                            )
                        )
                    for _ in range(4):
                        await asyncio.to_thread(entered.get, True, 10)
                    row = runtime.records.db.one("SELECT id,session_id FROM runs LIMIT 1")
                    await websocket.send(
                        json.dumps(
                            dict(
                                protocol_version=1,
                                request_id="stop",
                                method="run.cancel",
                                params=dict(session_id=row["session_id"], run_id=row["id"]),
                            )
                        )
                    )
                    async with asyncio.timeout(3):
                        while True:
                            frame = json.loads(await websocket.recv())
                            if frame.get("request_id") == "stop":
                                assert frame["result"] == {"accepted": True}
                                break
                    assert not release.is_set()
                finally:
                    release.set()
        finally:
            release.set()
            serving.cancel()
            with suppress(asyncio.CancelledError):
                await serving

    runtime._configured_llm_factory = lambda config: BlockingModel()
    try:
        asyncio.run(scenario())
    finally:
        release.set()
        runtime.close()
