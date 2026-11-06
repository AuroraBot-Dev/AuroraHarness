"""验证重试来源、局部范围、当前权限与不可变历史。"""

from __future__ import annotations

import asyncio
import json
import queue
import socket
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress

import pytest
from langchain_core.messages import AIMessage
from test_agent_registry import setup_registry
from test_cancellation import wait_until
from test_persistence import PNG, Capture, runtime_at, visual_session
from websockets.asyncio.client import connect

from aurora.agent.capability import Capability, CapabilityPolicy, CapabilityScope
from aurora.agent.core import Effort
from aurora.agent.transport import RuntimeApi, serve_ndjson, serve_websocket
from aurora.common.cancellation import OperationCancelled


class Planner:
    """先执行一次不可重复追加，再执行可由工作区修复的任务。"""

    def __init__(self, long_wait=False):
        self.long_wait = long_wait

    def plan(self, goal):
        return [
            dict(
                id="done",
                description="已完成的追加",
                tool="run_python",
                effort=Effort.LOW,
                args={
                    "code": (
                        "from pathlib import Path; p=Path('counter'); "
                        "p.write_text(p.read_text(encoding='utf-8')+'x' "
                        "if p.exists() else 'x',encoding='utf-8')"
                    )
                },
            ),
            dict(
                id="failed",
                description="等待工作区修复",
                tool="run_python",
                effort=Effort.LOW,
                args={
                    "code": (
                        "from pathlib import Path; assert Path('fixed').exists(), '尚未修复'; "
                        + (
                            "import time; Path('ready').touch(); time.sleep(20); "
                            if self.long_wait
                            else ""
                        )
                        + "Path('result').write_text('ok',encoding='utf-8')"
                    )
                },
            ),
            dict(
                id="later",
                description="未执行的后续任务",
                tool="write_file",
                effort=Effort.LOW,
                args={"path": "later", "content": "ok"},
            ),
        ]


def failed_run(tmp_path, *, long_wait=False):
    """创建真实进程失败且含已完成兄弟任务的运行。"""
    runtime, workspace, _, _ = runtime_at(tmp_path)
    runtime._planner_factory = lambda llm, tools: Planner(long_wait)
    session = runtime.create_session(str(workspace), approval_mode="always")
    with pytest.raises(RuntimeError):
        session.start("完成三个任务")
    run = runtime.records.db.one("SELECT id FROM runs")
    return runtime, workspace, session, run["id"]


def history(runtime, run_id):
    """读取来源运行的全部历史以检查不可变性。"""
    return RuntimeApi(runtime)._persistence.dispatch("run.get", {"runId": run_id})


def test_full_retry_retains_completed_work_and_records_new_origin(tmp_path):
    runtime, workspace, session, original = failed_run(tmp_path)
    try:
        before = history(runtime, original)
        assert (workspace / "counter").read_text(encoding="utf-8") == "x"
        (workspace / "fixed").touch()
        update = session.retry(original)
        assert update.status == "completed" and update.run_id != original
        assert update.retry_of_run_id == original and update.retry_task_id is None
        assert (workspace / "counter").read_text(encoding="utf-8") == "x"
        assert (workspace / "later").exists()
        assert history(runtime, original) == before
        current = history(runtime, update.run_id)
        assert len(current["tasks"]) == 2
        assert all(task["retryOfTaskId"] for task in current["tasks"])
        assert current["retryOfRunId"] == original
    finally:
        runtime.close()


def test_single_task_retry_does_not_execute_queued_sibling(tmp_path):
    runtime, workspace, session, original = failed_run(tmp_path)
    try:
        task = runtime.records.db.one("SELECT id FROM tasks WHERE task_key='failed'")
        (workspace / "fixed").touch()
        result = session.retry(original, task["id"])
        assert result.status == "completed" and result.retry_task_id == task["id"]
        assert (workspace / "counter").read_text(encoding="utf-8") == "x"
        assert not (workspace / "later").exists()
        assert len(history(runtime, result.run_id)["tasks"]) == 1
    finally:
        runtime.close()


@pytest.mark.parametrize("select_child", [False, True])
def test_retry_delegated_scope_keeps_completed_child_and_parent_history(
    tmp_path, monkeypatch, select_child
):
    runtime, workspace, _, _ = setup_registry(tmp_path, monkeypatch)
    try:
        session = runtime.create_session(str(workspace))
        paused = session.start("委派两个写入")
        paused = session.resume(paused.run_id, {"approved": True})
        runtime.cancel_run(session.id, paused.run_id)
        (workspace / "1.txt").write_text("保留用户修改", encoding="utf-8")
        before = history(runtime, paused.run_id)
        parent = next(task for task in before["tasks"] if task["tool"] == "call_agent")
        target = (
            next(task for task in before["tasks"] if task["parentId"] and task["taskKey"] == "2")
            if select_child
            else parent
        )
        retried = session.retry(paused.run_id, target["id"])
        assert retried.status == "waiting"
        assert (workspace / "1.txt").read_text(encoding="utf-8") == "保留用户修改"
        retried = session.resume(retried.run_id, {"approved": True})
        assert retried.status == "completed"
        assert (workspace / "2.txt").exists()
        assert (workspace / "1.txt").read_text(encoding="utf-8") == "保留用户修改"
        assert history(runtime, paused.run_id) == before
    finally:
        runtime.close()


def test_retry_rechecks_current_approval_policy_and_expires_old_gate(tmp_path):
    runtime, workspace, session, original = failed_run(tmp_path)
    try:
        (workspace / "fixed").touch()
        runtime.records.update("sessions", session.id, approval_mode="interactive")
        retried = session.retry(original)
        assert retried.status == "waiting"
        assert not (workspace / "result").exists()
        assert (
            runtime.records.get("runs", retried.run_id)["config_snapshot"]["approval_mode"]
            == "interactive"
        )
        retried = session.resume(retried.run_id, {"approved": True})
        assert retried.status == "waiting"
        retried = session.resume(retried.run_id, {"approved": True})
        assert retried.status == "completed"
    finally:
        runtime.close()


def test_retry_after_restart_uses_durable_plan_and_session_deletion_cascades(tmp_path):
    runtime, workspace, session, original = failed_run(tmp_path)
    session_id = session.id
    runtime.close()
    restarted, _, _, _ = runtime_at(tmp_path)
    try:
        (workspace / "fixed").touch()
        result = restarted.get_session(session_id).retry(original)
        assert result.status == "completed"
        assert (workspace / "counter").read_text(encoding="utf-8") == "x"
        restarted.close_session(session_id)
        restarted.records.delete_session(session_id)
        assert not restarted.records.db.all("SELECT id FROM runs")
        assert not restarted.records.db.all("SELECT id FROM tasks")
    finally:
        restarted.close()


def test_retry_interrupted_delegation_uses_new_approval_after_restart(tmp_path, monkeypatch):
    runtime, workspace, _, _ = setup_registry(tmp_path, monkeypatch)
    session = runtime.create_session(str(workspace))
    paused = session.start("委派两个写入")
    paused = session.resume(paused.run_id, {"approved": True})
    session_id = session.id
    runtime.approvals.close()
    runtime.records.close()
    session.repository.close()
    (workspace / "1.txt").write_text("保留用户修改", encoding="utf-8")
    restarted, _, _, _ = runtime_at(tmp_path)
    try:
        before = history(restarted, paused.run_id)
        assert before["status"] == "interrupted"
        current = restarted.get_session(session_id)
        retried = current.retry(paused.run_id)
        assert retried.status == "waiting"
        assert retried.interruptions[0].id != paused.interruptions[0].id
        assert retried.interruptions[0].value["args"]["path"] == "2.txt"
        assert current.resume(retried.run_id, {"approved": True}).status == "completed"
        assert (workspace / "1.txt").read_text(encoding="utf-8") == "保留用户修改"
        assert (workspace / "2.txt").read_text(encoding="utf-8") == "2"
        assert history(restarted, paused.run_id) == before
    finally:
        restarted.close()


def test_retry_does_not_restore_revoked_tools(tmp_path):
    runtime, workspace, session, original = failed_run(tmp_path)
    try:
        before = history(runtime, original)
        agent = runtime.records.list_configs("agent")[0]
        runtime.records.save("agent", {"allowed_tools": ["read_file"]}, agent["id"])
        (workspace / "fixed").touch()
        with pytest.raises(RuntimeError):
            session.retry(original)
        assert not (workspace / "result").exists()
        assert not (workspace / "later").exists()
        assert history(runtime, original) == before
        assert (workspace / "counter").read_text(encoding="utf-8") == "x"
    finally:
        runtime.close()


def test_retry_rebuilds_sandbox_after_policy_is_restricted(tmp_path):
    runtime, workspace, _, _ = runtime_at(tmp_path)
    try:
        session = runtime.create_session(str(workspace))
        paused = session.start("写页面")
        runtime.cancel_run(session.id, paused.run_id)
        runtime.records.update(
            "sessions", session.id, sandbox_mode="read-only", approval_mode="always"
        )
        with pytest.raises(RuntimeError):
            session.retry(paused.run_id)
        assert session.sandbox.mode == "read-only"
        assert not (workspace / "index.html").exists()
        assert "只读" in runtime.records.db.all("SELECT output FROM tasks")[-1]["output"]
    finally:
        runtime.close()


def test_retry_keeps_plan_order_when_timestamps_tie_and_tools_change(tmp_path):
    runtime, workspace, _, _ = runtime_at(tmp_path)

    class OrderedPlanner(Planner):
        """使用与字典序相反的任务键及必须串行的后续检查。"""

        def plan(self, goal):
            tasks = super().plan(goal)
            tasks[1]["id"] = "z-first"
            tasks[1]["args"]["code"] = tasks[1]["args"]["code"].replace(
                "Path('result')", "import time; time.sleep(0.3); Path('result')"
            )
            tasks[2] = dict(
                id="a-second",
                description="检查前序结果",
                tool="run_python",
                effort=Effort.LOW,
                args={
                    "code": (
                        "from pathlib import Path; assert Path('result').exists(); "
                        "Path('later').touch()"
                    )
                },
            )
            return tasks

    runtime._planner_factory = lambda llm, tools: OrderedPlanner()
    try:
        session = runtime.create_session(str(workspace), approval_mode="always")
        with pytest.raises(RuntimeError):
            session.start("按序执行")
        original = runtime.records.db.one("SELECT id FROM runs")["id"]
        with runtime.records.db.lock:
            runtime.records.db.connection.execute(
                "UPDATE tasks SET created_at='2026-01-01T00:00:00+00:00'"
            )
        agent = runtime.records.list_configs("agent")[0]
        runtime.records.save("agent", {"allowed_tools": ["run_python", "write_file"]}, agent["id"])
        (workspace / "fixed").touch()
        assert session.retry(original).status == "completed"
        assert (workspace / "later").exists()
        assert (workspace / "counter").read_text(encoding="utf-8") == "x"
    finally:
        runtime.close()


def test_retry_with_no_saved_plan_replans_original_goal(tmp_path):
    runtime, workspace, goals, _ = runtime_at(tmp_path)
    failing = [True]
    original_factory = runtime._planner_factory

    class FailedPlanner:
        """首次规划失败，后续使用正常规划器。"""

        def plan(self, goal):
            raise ValueError("规划服务不可用")

    runtime._planner_factory = lambda llm, tools: (
        FailedPlanner() if failing[0] else original_factory(llm, tools)
    )
    try:
        session = runtime.create_session(str(workspace), approval_mode="always")
        with pytest.raises(RuntimeError):
            session.start("原始目标")
        original = runtime.records.db.one("SELECT id FROM runs")["id"]
        failing[0] = False
        assert session.retry(original).status == "completed"
        assert "原始目标" in goals[-1]
        assert (workspace / "index.html").exists()
    finally:
        runtime.close()


def test_retry_acknowledges_new_run_before_execution_and_can_cancel(tmp_path):
    runtime, workspace, session, original = failed_run(tmp_path, long_wait=True)
    try:
        task = runtime.records.db.one("SELECT id FROM tasks WHERE task_key='failed'")["id"]
        (workspace / "fixed").touch()
        frames = queue.Queue()
        request = dict(
            protocol_version=1,
            request_id="retry",
            method="run.retry",
            params=dict(session_id=session.id, run_id=original, task_id=task),
        )
        with ThreadPoolExecutor(max_workers=1) as workers:
            future = workers.submit(RuntimeApi(runtime).process_wire, request, frames.put)
            new_id = None
            try:
                frame = frames.get(timeout=5)
                assert frame["request_id"] == "retry" and frame["ok"]
                new_id = frame["result"]["run"]["id"]
                assert new_id != original
                assert frame["result"]["run"]["retry_task_id"] == task
                wait_until(lambda: (workspace / "ready").exists())
                assert not future.done()
                runtime.cancel_run(session.id, new_id)
                future.result(timeout=5)
            finally:
                if new_id:
                    runtime.cancel_run(session.id, new_id)
            remaining = []
            while not frames.empty():
                remaining.append(frames.get_nowait())
            assert not any(frame.get("request_id") == "retry" for frame in remaining)
        assert runtime.records.get("runs", new_id)["status"] == "cancelled"
        assert not (workspace / "result").exists()
    finally:
        runtime.close()


def test_retry_completed_code_restarts_visual_validation_without_writes(tmp_path):
    class CancelCapture(Capture):
        """首次截图模拟用户取消。"""

        def capture(self, config, sandbox):
            if self.calls == 0:
                self.calls += 1
                raise OperationCancelled()
            return super().capture(config, sandbox)

    runtime, workspace, goals, _ = runtime_at(tmp_path, ["passed"], CancelCapture())
    try:
        session = visual_session(runtime, workspace)
        cancelled = session.start("设计页面")
        assert cancelled.status == "cancelled"
        (workspace / "index.html").write_text("保留当前页面", encoding="utf-8")
        result = session.retry(cancelled.run_id)
        assert result.status == "completed"
        assert len(goals) == 1
        assert (workspace / "index.html").read_text(encoding="utf-8") == "保留当前页面"
        assert len(history(runtime, result.run_id)["reviews"]) == 1
    finally:
        runtime.close()


def test_retry_cancelled_plan_mode_replans_instead_of_returning_an_empty_plan(tmp_path):
    runtime, workspace, goals, _ = runtime_at(tmp_path)
    try:
        session = visual_session(runtime, workspace)

        def cancel_plan(run_id, node, update):
            if node == "stage.completed":
                runtime.cancel_run(session.id, run_id)

        cancelled = session.start("规划页面", cancel_plan, mode="plan")
        assert cancelled.status == "cancelled"
        retried = session.retry(cancelled.run_id)
        assert retried.status == "completed"
        assert len(retried.state["tasks"]) == 1
        assert len(goals) == 2
        assert not (workspace / "index.html").exists()
    finally:
        runtime.close()


def test_retry_child_response_preserves_scope_and_never_uses_parent_context(tmp_path, monkeypatch):
    runtime, workspace, child, _ = setup_registry(tmp_path, monkeypatch)
    messages, failing = [], [True]
    parent = runtime.records.list_configs("agent")[0]
    runtime.records.save("agent", {"instructions": "仅限父角色的私有指令"}, parent["id"])
    runtime.capabilities.register(
        CapabilityPolicy(
            "child-scope",
            lambda context: Capability(context="子树特化上下文"),
            CapabilityScope("subtree", workspace, ("parent",)),
        )
    )

    class Model:
        """记录子角色请求，第二个回答第一次失败。"""

        def invoke(self, value):
            messages.append(value)
            if failing[0] and value[-1].content == "second":
                raise ValueError("子模型暂时失败")
            return AIMessage(content="子角色结果")

    class ResponsePlanner:
        """先委派，再生成两个可独立重试的回答任务。"""

        def __init__(self, tools):
            self.tools = tools

        def plan(self, goal):
            if "call_agent" in self.tools:
                return [
                    dict(
                        id="parent",
                        description="委派分析",
                        tool="call_agent",
                        effort=Effort.LOW,
                        args={"agent_id": child["id"], "task": "子角色的任务"},
                    )
                ]
            return [
                dict(
                    id=value,
                    description=value,
                    tool="agent_respond",
                    effort=Effort.LOW,
                    args={"prompt": value},
                )
                for value in ("first", "second")
            ]

    runtime._configured_llm_factory = lambda config: Model()
    runtime._planner_factory = lambda llm, tools: ResponsePlanner(tools)
    try:
        session = runtime.create_session(str(workspace), approval_mode="always")
        with pytest.raises(RuntimeError):
            session.start("父角色目标")
        original = runtime.records.db.one("SELECT id FROM runs")["id"]
        failed = next(
            task for task in history(runtime, original)["tasks"] if task["taskKey"] == "second"
        )
        with pytest.raises(RuntimeError):
            session.retry(original, failed["id"])
        first_retry = runtime.records.db.one(
            "SELECT id FROM runs WHERE retry_of_run_id=?", (original,)
        )["id"]
        failed_again = history(runtime, first_retry)["tasks"][0]
        runtime.records.save("agent", {"callable": False}, child["id"])
        with pytest.raises(ValueError, match="委派权限"):
            session.retry(first_retry, failed_again["id"])
        runtime.records.save("agent", {"callable": True}, child["id"])
        failing[0] = False
        result = session.retry(first_retry, failed_again["id"])
        assert result.status == "completed"
        assert result.retry_of_run_id == first_retry
        assert len(messages) == 4
        assert "仅限父角色的私有指令" not in str(messages[-1])
        assert "子树特化上下文" in str(messages[-1])
        assert messages[-1][-1].content == "second"
    finally:
        runtime.close()


def test_explicit_completed_task_retry_only_repeats_that_task(tmp_path):
    runtime, workspace, session, original = failed_run(tmp_path)
    try:
        task = runtime.records.db.one("SELECT id FROM tasks WHERE task_key='done'")["id"]
        assert session.retry(original, task).status == "completed"
        assert (workspace / "counter").read_text(encoding="utf-8") == "xx"
        assert not (workspace / "later").exists()
    finally:
        runtime.close()


def test_invalid_retry_targets_do_not_create_runs(tmp_path):
    runtime, workspace, session, original = failed_run(tmp_path)
    try:
        api = RuntimeApi(runtime)
        other = runtime.create_session(str(workspace))

        def request(**params):
            return api.handle({"id": "retry", "method": "run.retry", "params": params})[0]

        assert request(session_id=other.id, run_id=original)["error"]["code"] == "RUN_NOT_FOUND"
        assert (
            request(session_id=session.id, run_id=original, task_id="missing")["error"]["code"]
            == "TASK_NOT_FOUND"
        )
        assert request(session_id=session.id, run_id=original, task_id=1)["error"]
        assert len(runtime.records.db.all("SELECT id FROM runs")) == 1
        completed = session.start("你好", mode="say")
        assert (
            request(session_id=session.id, run_id=completed.run_id)["error"]["code"]
            == "RUN_NOT_RETRYABLE"
        )
        runtime.records.update("sessions", session.id, approval_mode="interactive")
        paused = session.start("再次执行")
        assert (
            request(session_id=session.id, run_id=paused.run_id)["error"]["code"]
            == "RUN_NOT_RETRYABLE"
        )
        assert (
            request(session_id=session.id, run_id=original)["error"]["code"] == "RUN_ALREADY_ACTIVE"
        )
        assert len(runtime.records.db.all("SELECT id FROM runs")) == 3
    finally:
        runtime.close()


@pytest.mark.parametrize("change", ["none", "text-model", "tampered-image"])
def test_retry_image_task_copies_valid_evidence_and_checks_current_model(
    tmp_path, monkeypatch, change
):
    runtime, workspace, child, _ = setup_registry(tmp_path, monkeypatch)
    model = runtime.records.list_configs("model")[0]
    runtime.records.save(
        "model", {"model_type": "multimodal", "input_types": ["text", "image"]}, model["id"]
    )
    failing, messages = [True], []

    class Model:
        """先失败，随后记录重试时真正收到的图像。"""

        def invoke(self, value):
            messages.append(value)
            if failing[0]:
                raise ValueError("图像模型暂时不可用")
            return AIMessage(content="图像分析已完成")

    class ImagePlanner:
        """从本轮产物创建带图片的委派。"""

        def __init__(self, tools):
            self.tools = tools

        def plan(self, goal):
            if "call_agent" in self.tools:
                run_id = runtime.records.db.one("SELECT id FROM runs WHERE status='running'")["id"]
                artifact = runtime.records.artifact(
                    run_id, None, "screenshot", PNG, "image/png", {}
                )
                return [
                    dict(
                        id="parent",
                        description="图片分析",
                        tool="call_agent",
                        effort=Effort.LOW,
                        args={
                            "agent_id": child["id"],
                            "task": "分析图片",
                            "artifact_ids": [artifact["id"]],
                        },
                    )
                ]
            return [
                dict(
                    id="image",
                    description="理解图片",
                    tool="agent_respond",
                    effort=Effort.LOW,
                    args={"prompt": "描述图片"},
                )
            ]

    runtime._planner_factory = lambda llm, tools: ImagePlanner(tools)
    runtime._configured_llm_factory = lambda config: Model()
    try:
        session = runtime.create_session(str(workspace), approval_mode="always")
        with pytest.raises(RuntimeError):
            session.start("分析页面图片")
        original = runtime.records.db.one("SELECT id FROM runs")["id"]
        detail = history(runtime, original)
        target = next(task for task in detail["tasks"] if task["tool"] == "agent_respond")
        source_image = detail["artifacts"][0]
        failing[0] = False
        if change == "text-model":
            runtime.records.save(
                "model", {"model_type": "text", "input_types": ["text"]}, model["id"]
            )
        if change == "tampered-image":
            from pathlib import Path

            Path(source_image["path"]).write_bytes(b"tampered")
        if change != "none":
            with pytest.raises(RuntimeError):
                session.retry(original, target["id"])
            assert len(messages) == 1
        else:
            result = session.retry(original, target["id"])
            assert result.status == "completed"
            copied = history(runtime, result.run_id)["artifacts"][0]
            assert copied["id"] != source_image["id"]
            assert copied["metadata"]["retry_of_artifact_id"] == source_image["id"]
            assert copied["sha256"] == source_image["sha256"]
            assert messages[-1][1].content[1]["type"] == "image_url"
        assert history(runtime, original) == detail
    finally:
        runtime.close()


def test_retry_can_cancel_in_creation_callback_before_tools_begin(tmp_path):
    runtime, workspace, session, original = failed_run(tmp_path)
    try:
        before = history(runtime, original)
        result = session.retry(
            original, on_created=lambda run_id: runtime.cancel_run(session.id, run_id)
        )
        assert result.status == "cancelled"
        assert not history(runtime, result.run_id)["tasks"]
        assert history(runtime, original) == before
        assert (workspace / "counter").read_text(encoding="utf-8") == "x"
    finally:
        runtime.close()


def test_retry_creation_callback_failure_does_not_leave_an_active_run(tmp_path):
    runtime, workspace, session, original = failed_run(tmp_path)
    try:

        def unavailable(run_id):
            raise ValueError("接收器故障")

        with pytest.raises(RuntimeError, match="接收器"):
            session.retry(original, on_created=unavailable)
        assert not runtime.records.db.all(
            "SELECT id FROM runs WHERE status IN ('queued','running','waiting')"
        )
        assert not (workspace / "result").exists()
    finally:
        runtime.close()


@pytest.mark.parametrize("transport", ["stdio", "websocket"])
def test_retry_creation_response_and_cancel_share_both_transports(tmp_path, transport):
    runtime, workspace, session, original = failed_run(tmp_path, long_wait=True)
    task_id = runtime.records.db.one("SELECT id FROM tasks WHERE task_key='failed'")["id"]
    (workspace / "fixed").touch()
    new_ids = []

    async def exchange(send, receive):
        await send(
            dict(
                protocol_version=1,
                request_id="retry",
                method="run.retry",
                params=dict(session_id=session.id, run_id=original, task_id=task_id),
            )
        )
        frame = await receive()
        assert frame["request_id"] == "retry" and frame["ok"]
        new_ids.append(frame["result"]["run"]["id"])
        await send(
            dict(
                protocol_version=1,
                request_id="stop",
                method="run.cancel",
                params=dict(session_id=session.id, run_id=new_ids[0]),
            )
        )
        received = []
        async with asyncio.timeout(10):
            while not any(value.get("type") == "run.cancelled" for value in received) or not any(
                value.get("request_id") == "stop" for value in received
            ):
                received.append(await receive())
        assert not any(value.get("request_id") == "retry" for value in received)
        assert next(value for value in received if value.get("request_id") == "stop")["result"][
            "accepted"
        ]

    async def websocket():
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        server = asyncio.create_task(serve_websocket(RuntimeApi(runtime), port=port))
        try:
            for _ in range(100):
                try:
                    ws = await connect(f"ws://127.0.0.1:{port}/ws")
                    break
                except OSError:
                    await asyncio.sleep(0.01)
            else:
                pytest.fail("WebSocket 服务未启动")
            async with ws:

                async def send(value):
                    await ws.send(json.dumps(value))

                async def receive():
                    return json.loads(await asyncio.wait_for(ws.recv(), 10))

                await exchange(send, receive)
        finally:
            for run_id in new_ids:
                runtime.cancel_run(session.id, run_id)
            server.cancel()
            with suppress(asyncio.CancelledError):
                await server

    try:
        if transport == "websocket":
            asyncio.run(websocket())
        else:
            incoming, outgoing = queue.Queue(), queue.Queue()

            class Input:
                """保持 stdio 输入打开直到测试关闭。"""

                def __iter__(self):
                    return iter(incoming.get, None)

            class Output:
                """收集服务器完整输出帧。"""

                def write(self, value):
                    outgoing.put(json.loads(value))

                def flush(self):
                    pass

            async def send(value):
                incoming.put(json.dumps(value))

            async def receive():
                return await asyncio.to_thread(outgoing.get, True, 10)

            with ThreadPoolExecutor(max_workers=1) as workers:
                serving = workers.submit(serve_ndjson, RuntimeApi(runtime), Input(), Output())
                try:
                    asyncio.run(exchange(send, receive))
                finally:
                    for run_id in new_ids:
                        runtime.cancel_run(session.id, run_id)
                    incoming.put(None)
                serving.result(timeout=10)
        assert runtime.records.get("runs", new_ids[0])["status"] == "cancelled"
        assert not (workspace / "result").exists()
    finally:
        runtime.close()
