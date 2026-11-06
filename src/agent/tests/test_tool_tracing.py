"""验证真实调用的审批边界、持久化、取消和脱敏。"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from test_agent_registry import setup_registry
from test_cancellation import wait_until
from test_persistence import runtime_at
from test_retries import failed_run, history

from aurora.agent.core import Effort
from aurora.agent.tools.base import Tool


def test_trace_starts_only_after_approval_and_is_committed_before_events(tmp_path):
    runtime, workspace, _, _ = runtime_at(tmp_path)
    observed = []

    def receive(name, payload):
        if name.startswith("tool."):
            persisted = runtime.records.get("tool_calls", payload["tool_call_id"])
            assert persisted["status"] == payload["status"]
            observed.append((name, payload))

    unsubscribe = runtime.events.subscribe(receive)
    try:
        session = runtime.create_session(str(workspace))
        paused = session.start("写入页面")
        assert not history(runtime, paused.run_id)["toolCalls"]
        assert not observed
        result = session.resume(paused.run_id, {"approved": True})
        detail = history(runtime, result.run_id)
        call = detail["toolCalls"][0]
        assert call["status"] == "completed" and call["durationMs"] >= 0
        assert call["startedAt"] <= call["endedAt"]
        assert call["input"]["path"] == "index.html"
        assert call["output"] and not call["error"]
        assert call["taskId"] == detail["tasks"][0]["id"]
        assert call["agentRunId"] == detail["agentRuns"][0]["id"]
        assert [name for name, _ in observed] == ["tool.started", "tool.completed"]
        assert all(payload["tool_call_id"] == call["id"] for _, payload in observed)
        assert [item["type"] for item in detail["events"] if item["type"].startswith("tool.")] == [
            "tool.started",
            "tool.completed",
        ]
    finally:
        unsubscribe()
        runtime.close()


def test_denied_approval_never_creates_tool_call(tmp_path):
    runtime, workspace, _, _ = runtime_at(tmp_path)
    try:
        session = runtime.create_session(str(workspace))
        paused = session.start("拒绝写入")
        with pytest.raises(RuntimeError, match="失败的工具"):
            session.resume(paused.run_id, {"approved": False})
        assert not history(runtime, paused.run_id)["toolCalls"]
        assert not (workspace / "index.html").exists()
    finally:
        runtime.close()


def test_failed_process_records_error_and_retry_keeps_original_spans(tmp_path):
    runtime, workspace, session, source = failed_run(tmp_path)
    try:
        before = history(runtime, source)
        assert [call["status"] for call in before["toolCalls"]] == ["completed", "failed"]
        assert "尚未修复" in before["toolCalls"][1]["error"]
        (workspace / "fixed").touch()
        result = session.retry(source)
        after = history(runtime, result.run_id)
        assert len(after["toolCalls"]) == 2
        assert all(call["status"] == "completed" for call in after["toolCalls"])
        assert {call["id"] for call in after["toolCalls"]}.isdisjoint(
            call["id"] for call in before["toolCalls"]
        )
        assert history(runtime, source) == before
    finally:
        runtime.close()


def test_cancelled_process_finishes_span_and_suppresses_later_calls(tmp_path):
    runtime, workspace, _, _ = runtime_at(tmp_path)

    class Planner:
        """先阻塞进程，再安排一个不会执行的任务。"""

        def plan(self, goal):
            return [
                dict(
                    id="slow",
                    description="等待",
                    effort=Effort.LOW,
                    tool="run_python",
                    args={
                        "code": "from pathlib import Path; import time; "
                        "Path('ready').touch(); time.sleep(20)"
                    },
                ),
                dict(
                    id="later",
                    description="后续",
                    effort=Effort.LOW,
                    tool="write_file",
                    args={"path": "later", "content": "unexpected"},
                ),
            ]

    runtime._planner_factory = lambda llm, tools: Planner()
    try:
        session = runtime.create_session(str(workspace), approval_mode="always")
        with ThreadPoolExecutor(max_workers=1) as workers:
            future = workers.submit(session.start, "运行可取消进程")
            try:
                wait_until(lambda: (workspace / "ready").exists())
                run_id = runtime.records.db.one("SELECT id FROM runs")["id"]
                assert history(runtime, run_id)["toolCalls"][0]["status"] == "running"
                runtime.cancel_run(session.id, run_id)
                result = future.result(timeout=5)
                calls = history(runtime, result.run_id)["toolCalls"]
                assert len(calls) == 1 and calls[0]["status"] == "cancelled"
                assert calls[0]["durationMs"] >= 0 and calls[0]["endedAt"]
                assert not (workspace / "later").exists()
            finally:
                if not future.done():
                    runtime.cancel_run(
                        session.id, runtime.records.db.one("SELECT id FROM runs")["id"]
                    )
    finally:
        runtime.close()


def test_delegated_tools_belong_to_child_role_without_fake_parent_call(tmp_path, monkeypatch):
    runtime, workspace, child, _ = setup_registry(tmp_path, monkeypatch)
    try:
        session = runtime.create_session(str(workspace), approval_mode="always")
        result = session.start("委派写入")
        detail = history(runtime, result.run_id)
        child_run = next(row for row in detail["agentRuns"] if row["agentId"] == child["id"])
        assert len(detail["toolCalls"]) == 2
        assert all(row["agentRunId"] == child_run["id"] for row in detail["toolCalls"])
        assert all(row["toolName"] == "write_file" for row in detail["toolCalls"])
        assert all(row["taskId"] for row in detail["toolCalls"])
    finally:
        runtime.close()


@pytest.mark.parametrize("fails", [False, True])
def test_trace_scrubs_structured_secrets_and_registered_keys_without_changing_tool_input(
    tmp_path, monkeypatch, fails
):
    monkeypatch.setenv("AGENT_API_KEY", "trace-test-known-key")
    runtime, workspace, _, _ = runtime_at(tmp_path)
    received = []

    def run(payload):
        received.append(payload)
        value = json.dumps(
            {"access_token": "output-secret", "nested": {"password": "password-secret"}}
        )
        if fails:
            raise ValueError(value)
        return value

    try:
        session = runtime.create_session(str(workspace), approval_mode="always")
        result = session.start("准备调用关联信息")
        stage = history(runtime, result.run_id)["agentRuns"][0]
        args = {
            "payload": {
                "api_key": "private-input",
                "content": "trace-test-known-key",
                "nested": [{"Authorization": "Bearer secret"}],
            }
        }
        if fails:
            with pytest.raises(ValueError):
                runtime.traces.invoke(
                    result.run_id, stage["id"], "extra", Tool("test", "验证", run), args
                )
        else:
            runtime.traces.invoke(
                result.run_id, stage["id"], "extra", Tool("test", "验证", run), args
            )
        assert received == [args["payload"]]
        detail = history(runtime, result.run_id)
        saved = json.dumps(
            detail["toolCalls"]
            + [row for row in detail["events"] if row["type"].startswith("tool.")]
        )
        assert "[redacted]" in saved
        assert not any(
            secret in saved
            for secret in [
                "private-input",
                "trace-test-known-key",
                "Bearer secret",
                "output-secret",
                "password-secret",
            ]
        )
    finally:
        runtime.close()


def test_restart_marks_unfinished_span_interrupted_without_inventing_duration_and_cascades(
    tmp_path,
):
    runtime, workspace, _, _ = runtime_at(tmp_path)
    session_id = ""
    try:
        session = runtime.create_session(str(workspace), approval_mode="always")
        session_id = session.id
        result = session.start("生成历史")
        call = history(runtime, result.run_id)["toolCalls"][0]
        runtime.records.update(
            "tool_calls", call["id"], status="running", ended_at=None, duration_ms=None
        )
        runtime.close()
        runtime, _, _, _ = runtime_at(tmp_path)
        recovered = history(runtime, result.run_id)["toolCalls"][0]
        assert recovered["status"] == "interrupted" and recovered["durationMs"] is None
        assert recovered["endedAt"]
        runtime.records.delete_session(session_id)
        assert not runtime.records.db.all("SELECT * FROM tool_calls")
    finally:
        runtime.close()
