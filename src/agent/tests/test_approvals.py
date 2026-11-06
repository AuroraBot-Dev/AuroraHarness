"""验证危险操作审批的期限、唯一解决和传输通知。"""

from __future__ import annotations

import asyncio
import json
import queue
import socket
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, suppress
from datetime import UTC, datetime, timedelta

import pytest
from test_agent_registry import setup_registry
from test_cancellation import wait_until
from test_persistence import runtime_at, visual_session
from websockets.asyncio.client import connect

from aurora.agent.core import Effort
from aurora.agent.transport import RuntimeApi, serve_ndjson, serve_websocket


class Clock:
    """提供无需长时间等待的 UTC 审批时钟。"""

    def __init__(self):
        self.value = datetime.now(UTC)

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += timedelta(seconds=seconds)


def resolve(api, session, paused, approval_id=None, approved=True):
    """通过公共审批接口提交规范请求。"""
    return api.handle(
        {
            "id": "resolve",
            "method": "approval.resolve",
            "params": {
                "session_id": session.id,
                "run_id": paused.run_id,
                "approval_id": approval_id or paused.interruptions[0].value["approval_id"],
                "approved": approved,
            },
        }
    )


def test_approval_deadline_is_persisted_and_resolve_is_unique(tmp_path):
    clock = Clock()
    runtime, workspace, _, _ = runtime_at(tmp_path, approval_clock=clock, approval_timeout=60)
    try:
        session = runtime.create_session(str(workspace))
        paused = session.start("写页面")
        request = paused.interruptions[0].value
        assert datetime.fromisoformat(request["expires_at"]) == clock() + timedelta(seconds=60)
        row = runtime.records.db.one("SELECT * FROM interactions")
        assert row["id"] == request["approval_id"]
        api = RuntimeApi(runtime)
        assert resolve(api, session, paused)[0]["result"] == {"accepted": True}
        assert (workspace / "index.html").exists()
        row = runtime.records.db.one("SELECT * FROM interactions")
        assert row["resolution"] == "approved"
        assert row["resolved_at"] == clock().isoformat()
        assert resolve(api, session, paused)[0]["error"]["code"] == "APPROVAL_ALREADY_RESOLVED"
        assert (
            runtime.records.db.one(
                "SELECT COUNT(*) n FROM run_events WHERE type='approval.resolved'"
            )["n"]
            == 1
        )
        history = api._persistence.dispatch("session.get", {"sessionId": session.id})
        assert history["approvals"][0]["status"] == "approved"
    finally:
        runtime.close()


def test_timeout_is_automatic_and_rejects_late_approval(tmp_path):
    runtime, workspace, _, _ = runtime_at(tmp_path, approval_timeout=1)
    events = queue.Queue()

    def observe(name, payload):
        if not name.startswith("task."):
            events.put((name, payload))

    unsubscribe = runtime.events.subscribe(observe)
    try:
        session = runtime.create_session(str(workspace))
        paused = session.start("写页面")
        wait_until(lambda: runtime.records.get("runs", paused.run_id)["status"] == "failed")
        assert not (workspace / "index.html").exists()
        row = runtime.records.db.one("SELECT * FROM interactions")
        assert row["status"] == "expired" and row["resolution"] == "timeout"
        assert json.loads(row["response_json"]) == {"approved": False}
        observed = [events.get(timeout=3), events.get(timeout=3)]
        assert [name for name, _ in observed] == ["approval.resolved", "run.failed"]
        assert observed[0][1]["approved"] is False
        assert (
            resolve(RuntimeApi(runtime), session, paused)[0]["error"]["code"] == "APPROVAL_EXPIRED"
        )
        with pytest.raises(ValueError):
            session.resume(paused.run_id, {"approved": True})
        assert not session.expire_approvals(paused.run_id)
        assert session.start("下一轮", mode="say").status == "completed"
    finally:
        unsubscribe()
        runtime.close()


def test_deadline_check_blocks_approval_even_before_scheduler_runs(tmp_path):
    clock = Clock()
    runtime, workspace, _, _ = runtime_at(tmp_path, approval_clock=clock, approval_timeout=60)
    try:
        session = runtime.create_session(str(workspace))
        paused = session.start("写页面")
        clock.advance(60)
        response = resolve(RuntimeApi(runtime), session, paused)[0]
        assert response["error"]["code"] == "APPROVAL_EXPIRED"
        assert not (workspace / "index.html").exists()
        assert runtime.records.get("runs", paused.run_id)["status"] == "failed"
    finally:
        runtime.close()


def test_deadline_crossed_during_submission_is_checked_at_commit(tmp_path, monkeypatch):
    clock = Clock()
    runtime, workspace, _, _ = runtime_at(tmp_path, approval_clock=clock, approval_timeout=60)
    try:
        session = runtime.create_session(str(workspace))
        paused = session.start("写页面")
        original = runtime.approvals.resolve

        def resolve_at_deadline(row, approved, **kwargs):
            if not kwargs.get("resolution"):
                clock.advance(60)
            return original(row, approved, **kwargs)

        monkeypatch.setattr(runtime.approvals, "resolve", resolve_at_deadline)
        assert (
            resolve(RuntimeApi(runtime), session, paused)[0]["error"]["code"] == "APPROVAL_EXPIRED"
        )
        assert not (workspace / "index.html").exists()
        assert (
            runtime.records.db.one("SELECT resolution FROM interactions")["resolution"] == "timeout"
        )
        assert runtime.records.get("runs", paused.run_id)["status"] == "failed"
    finally:
        runtime.close()


@pytest.mark.parametrize("value", ["false", "true", 0, 1, None, {}, []])
def test_non_boolean_decisions_cannot_authorize_tools(tmp_path, value):
    runtime, workspace, _, _ = runtime_at(tmp_path)
    try:
        session = runtime.create_session(str(workspace))
        paused = session.start("写页面")
        assert "error" in resolve(RuntimeApi(runtime), session, paused, approved=value)[0]
        with pytest.raises(ValueError, match="布尔"):
            session.resume(paused.run_id, {"approved": value})
        assert not (workspace / "index.html").exists()
        assert runtime.records.db.one("SELECT status FROM interactions")["status"] == "pending"
    finally:
        runtime.close()


def test_explicit_rejection_is_durable_and_never_executes(tmp_path):
    runtime, workspace, _, _ = runtime_at(tmp_path)
    try:
        session = runtime.create_session(str(workspace))
        paused = session.start("写页面")
        response = resolve(RuntimeApi(runtime), session, paused, approved=False)
        assert response[0]["result"] == {"accepted": True}
        assert response[-1]["event"] == "run.failed"
        assert (
            runtime.records.db.one("SELECT resolution FROM interactions")["resolution"]
            == "rejected"
        )
        assert not (workspace / "index.html").exists()
    finally:
        runtime.close()


def test_timeout_of_delegated_approval_preserves_completed_sibling(tmp_path, monkeypatch):
    runtime, workspace, _, _ = setup_registry(tmp_path, monkeypatch)
    clock = Clock()
    runtime.approvals.clock = clock
    runtime.approvals.timeout = 60
    try:
        session = runtime.create_session(str(workspace))
        paused = session.start("委派工作")
        paused = session.resume(paused.run_id, {"approved": True})
        assert paused.status == "waiting"
        assert (workspace / "1.txt").read_text(encoding="utf-8") == "1"
        clock.advance(60)
        assert session.expire_approvals(paused.run_id)
        assert not (workspace / "2.txt").exists()
        assert runtime.records.get("runs", paused.run_id)["status"] == "failed"
        rows = runtime.records.db.all("SELECT resolution FROM interactions ORDER BY created_at")
        assert [row["resolution"] for row in rows] == ["approved", "timeout"]
        assert not {
            row["status"] for row in runtime.records.db.all("SELECT status FROM agent_runs")
        } & {"running", "waiting"}
    finally:
        runtime.close()


def test_preview_approval_timeout_does_not_start_browser(tmp_path):
    clock = Clock()
    runtime, workspace, _, _ = runtime_at(tmp_path, approval_clock=clock, approval_timeout=60)
    try:
        session = visual_session(runtime, workspace)
        runtime.records.update("sessions", session.id, approval_mode="interactive")
        paused = session.start("构建页面")
        paused = session.resume(paused.run_id, {"approved": True})
        assert paused.interruptions[0].value["tool"] == "browser_preview"
        clock.advance(60)
        assert session.expire_approvals(paused.run_id)
        assert runtime.capture.calls == 0
        assert (workspace / "index.html").exists()
    finally:
        runtime.close()


def test_stdio_broadcasts_timeout_after_start_response(tmp_path):
    runtime, workspace, _, _ = runtime_at(tmp_path, approval_timeout=1)
    incoming, outgoing = queue.Queue(), queue.Queue()

    class Input:
        """在响应后保持连接打开。"""

        def __iter__(self):
            return iter(incoming.get, None)

    class Output:
        """收集后台广播的完整协议帧。"""

        def write(self, line):
            outgoing.put(json.loads(line))

        def flush(self):
            pass

    try:
        session = runtime.create_session(str(workspace))
        with ThreadPoolExecutor(max_workers=1) as workers:
            serving = workers.submit(serve_ndjson, RuntimeApi(runtime), Input(), Output())
            try:
                incoming.put(
                    json.dumps(
                        dict(
                            protocol_version=1,
                            request_id="start",
                            method="run.start",
                            params=dict(sessionId=session.id, goal="写页面"),
                        )
                    )
                )
                frames = []
                while not any(frame.get("type") == "run.failed" for frame in frames):
                    frames.append(outgoing.get(timeout=10))
                assert any(
                    frame.get("request_id") == "start" and frame["result"]["status"] == "waiting"
                    for frame in frames
                )
                assert any(frame.get("type") == "approval.requested" for frame in frames)
                assert any(
                    frame.get("type") == "approval.resolved"
                    and frame["payload"]["resolution"] == "timeout"
                    for frame in frames
                )
            finally:
                incoming.put(None)
            serving.result(timeout=10)
    finally:
        runtime.close()


def test_expiring_parallel_approval_stops_active_process_and_only_acknowledges_once(tmp_path):
    clock = Clock()
    runtime, workspace, _, _ = runtime_at(tmp_path, approval_clock=clock, approval_timeout=60)
    frames = queue.Queue()

    class Planner:
        """让两个危险操作同时进入独立审批。"""

        def plan(self, goal):
            return [
                dict(
                    id=str(index),
                    description="执行进程",
                    tool="run_python",
                    effort=Effort.LOW,
                    args={
                        "code": (
                            f"import pathlib,time; pathlib.Path('ready{index}').touch(); "
                            "time.sleep(20)"
                        ),
                        "timeout": 25,
                    },
                )
                for index in (1, 2)
            ]

    runtime._planner_factory = lambda llm, tools: Planner()
    agent = runtime.records.list_configs("agent")[0]
    runtime.records.save("agent", {"allowed_tools": ["run_python"]}, agent["id"])
    api = RuntimeApi(runtime)
    unsubscribe = api.subscribe(frames.put)
    restarted = []
    restart_errors = []

    def retry_on_terminal(name, payload):
        if name == "run.failed":
            try:
                restarted.append(session.start("下一轮", mode="say").status)
            except Exception as exc:
                restart_errors.append(str(exc))

    unsubscribe_retry = runtime.events.subscribe(retry_on_terminal)
    try:
        session = runtime.create_session(str(workspace))
        paused = session.start("启动两个进程")
        assert len(paused.interruptions) == 2
        approval = paused.interruptions[0].value["approval_id"]
        request = dict(
            protocol_version=1,
            request_id="approve",
            method="approval.resolve",
            params=dict(
                session_id=session.id, run_id=paused.run_id, approval_id=approval, approved=True
            ),
        )
        with ThreadPoolExecutor(max_workers=1) as workers:
            future = workers.submit(api.process_wire, request, frames.put)
            try:
                wait_until(
                    lambda: (workspace / "ready1").exists() or (workspace / "ready2").exists()
                )
                before_timeout = []
                while not frames.empty():
                    before_timeout.append(frames.get_nowait())
                responses = [
                    frame for frame in before_timeout if frame.get("request_id") == "approve"
                ]
                assert len(responses) == 1 and responses[0]["result"] == {"accepted": True}
                assert not future.done()
                clock.advance(60)
                assert session.expire_approvals(paused.run_id)
                future.result(timeout=5)
            finally:
                runtime.cancel_run(session.id, paused.run_id)
            after_timeout = []
            while not frames.empty():
                after_timeout.append(frames.get_nowait())
            assert not any(frame.get("request_id") == "approve" for frame in after_timeout)
        assert runtime.records.get("runs", paused.run_id)["status"] == "failed"
        resolutions = [
            row["resolution"]
            for row in runtime.records.db.all("SELECT resolution FROM interactions")
        ]
        assert sorted(resolutions) == ["approved", "timeout"]
        assert len(list(workspace.glob("ready*"))) == 1
        assert restarted == ["completed"]
        assert not restart_errors
    finally:
        unsubscribe_retry()
        unsubscribe()
        runtime.close()


def test_websocket_delivers_timeout_without_another_request(tmp_path):
    runtime, workspace, _, _ = runtime_at(tmp_path, approval_timeout=1)
    session = runtime.create_session(str(workspace))

    async def scenario():
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
                await ws.send(
                    json.dumps(
                        dict(
                            protocol_version=1,
                            request_id="start",
                            method="run.start",
                            params=dict(sessionId=session.id, goal="写页面"),
                        )
                    )
                )
                frames = []
                async with asyncio.timeout(10):
                    while not any(frame.get("type") == "run.failed" for frame in frames):
                        frames.append(json.loads(await ws.recv()))
                assert any(
                    frame.get("type") == "approval.resolved"
                    and frame["payload"]["resolution"] == "timeout"
                    for frame in frames
                )
                assert any(frame.get("type") == "approval.requested" for frame in frames)
        finally:
            server.cancel()
            with suppress(asyncio.CancelledError):
                await server

    try:
        asyncio.run(scenario())
    finally:
        runtime.close()


def test_approval_ownership_and_cancelled_gate_cannot_be_replayed(tmp_path):
    runtime, workspace, _, _ = runtime_at(tmp_path)
    try:
        session = runtime.create_session(str(workspace))
        other = runtime.create_session(str(workspace))
        first = session.start("第一轮")
        second = other.start("第二轮")
        api = RuntimeApi(runtime)
        foreign_id = second.interruptions[0].value["approval_id"]
        assert (
            resolve(api, session, first, approval_id=foreign_id)[0]["error"]["code"]
            == "APPROVAL_NOT_FOUND"
        )
        runtime.cancel_run(session.id, first.run_id)
        assert resolve(api, session, first)[0]["error"]["code"] == "APPROVAL_EXPIRED"
        row = runtime.records.db.one(
            "SELECT resolution FROM interactions WHERE run_id=?", (first.run_id,)
        )
        assert row["resolution"] == "cancelled"
        assert not (workspace / "index.html").exists()
    finally:
        runtime.close()


def test_upgrade_and_restart_preserve_history_and_expire_old_approvals(tmp_path):
    runtime, workspace, _, _ = runtime_at(tmp_path)
    session = runtime.create_session(str(workspace))
    paused = session.start("写页面")
    runtime.approvals.close()
    path = runtime.records.db.path
    runtime.records.close()
    session.repository.close()
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript(
            "ALTER TABLE tasks DROP COLUMN depends_on_json;"
            "ALTER TABLE tasks DROP COLUMN blocked_by_json;"
            "DROP TABLE run_checkpoints;"
            "DROP TABLE tool_calls;"
            "DROP INDEX runs_retry_source;"
            "ALTER TABLE runs DROP COLUMN retry_of_run_id;"
            "ALTER TABLE runs DROP COLUMN retry_task_id;"
            "ALTER TABLE tasks DROP COLUMN retry_of_task_id;"
            "DROP INDEX interactions_expiry;"
            "ALTER TABLE interactions DROP COLUMN expires_at;"
            "ALTER TABLE interactions DROP COLUMN resolved_at;"
            "ALTER TABLE interactions DROP COLUMN resolution;"
            "DELETE FROM schema_migrations WHERE version>=3;"
        )
        connection.commit()
    restarted, _, _, _ = runtime_at(tmp_path)
    try:
        row = restarted.records.db.one("SELECT * FROM interactions")
        assert row["status"] == "expired" and row["resolution"] == "runtime_restart"
        assert row["resolved_at"]
        assert row["expires_at"] is None
        assert restarted.records.get("runs", paused.run_id)["status"] == "interrupted"
        assert (
            resolve(RuntimeApi(restarted), session, paused)[0]["error"]["code"]
            == "APPROVAL_EXPIRED"
        )
        assert not (workspace / "index.html").exists()
    finally:
        restarted.close()


def test_cancel_and_timeout_record_only_one_resolution(tmp_path):
    clock = Clock()
    runtime, workspace, _, _ = runtime_at(tmp_path, approval_clock=clock, approval_timeout=60)
    try:
        session = runtime.create_session(str(workspace))
        paused = session.start("写页面")
        runtime.approvals.disarm(paused.run_id)
        clock.advance(60)
        ready = threading.Barrier(2)

        def cancel():
            ready.wait(timeout=5)
            return runtime.cancel_run(session.id, paused.run_id)

        def expire():
            ready.wait(timeout=5)
            return session.expire_approvals(paused.run_id)

        with ThreadPoolExecutor(max_workers=2) as workers:
            cancelled = workers.submit(cancel)
            expired = workers.submit(expire)
            cancelled.result(timeout=10)
            expired.result(timeout=10)
        run = runtime.records.get("runs", paused.run_id)
        row = runtime.records.db.one("SELECT resolution FROM interactions")
        assert row["resolution"] == ("timeout" if run["status"] == "failed" else "cancelled")
        assert (
            runtime.records.db.one(
                "SELECT COUNT(*) n FROM run_events WHERE type='approval.resolved'"
            )["n"]
            == 1
        )
        assert not (workspace / "index.html").exists()
    finally:
        runtime.close()
