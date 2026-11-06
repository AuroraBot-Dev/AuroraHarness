"""用真实 SQLite、Git 基线与进程退出验证工作流重启恢复。"""

import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from test_approvals import Clock, resolve
from test_persistence import Capture, configure_visual, runtime_at, visual_session

from aurora.agent.core import Effort
from aurora.agent.transport import RuntimeApi


def pending_input(runtime, session_id, run_id):
    """通过会话接口取出本轮待处理输入。"""
    result = RuntimeApi(runtime).handle(
        {"id": "read", "method": "session.get", "params": {"sessionId": session_id}}
    )[0]["result"]
    (pending,) = (
        item
        for item in result["approvals"]
        if item["runId"] == run_id and item["status"] == "pending"
    )
    return pending


def release_process_resources(runtime, session):
    """释放进程资源，保留崩溃现场的数据库状态。"""
    runtime.approvals.close()
    session.repository.close()
    runtime.records.close()


def crash_at_preview(root):
    """在独立进程写完文件后退出，跳过 Python 清理钩子。"""
    runtime, workspace, goals, _ = runtime_at(root)
    flow = configure_visual(runtime)
    session = runtime.create_session(str(workspace), workflow_id=flow["id"], approval_mode="always")
    paused = session.start("生成并审查页面")
    assert paused.status == "waiting" and paused.interruptions[0].value["previewRequired"]
    assert len(goals) == 1
    manifest = {
        "session": session.id,
        "run": paused.run_id,
        "diff": session.git_diff("index.html", "run", paused.run_id)["content"],
        "input": paused.interruptions[0].id,
        "modified": (workspace / "index.html").stat().st_mtime_ns,
    }
    (root / "waiting.json").write_text(json.dumps(manifest), encoding="utf-8")
    os._exit(0)


def test_process_exit_at_preview_resumes_visual_run_and_git_baseline(tmp_path):
    with TemporaryDirectory(dir=tmp_path) as scratch:
        subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), str(tmp_path)],
            check=True,
            env=os.environ | {"TMPDIR": scratch, "TMP": scratch, "TEMP": scratch},
            timeout=45,
        )
    saved = json.loads((tmp_path / "waiting.json").read_text(encoding="utf-8"))
    runtime, workspace, goals, _ = runtime_at(tmp_path, ["passed"])
    try:
        session = runtime.get_session(saved["session"])
        fresh = pending_input(runtime, session.id, saved["run"])
        assert fresh["previewRequired"] and fresh["interruptId"] != saved["input"]
        result = session.resume(
            saved["run"], {"url": "http://127.0.0.1:3000"}, fresh["interruptId"]
        )
        assert (result.run_id, result.status) == (saved["run"], "completed")
        assert (
            saved["diff"]
            and session.git_diff("index.html", "run", result.run_id)["content"] == saved["diff"]
        )
        assert (workspace / "index.html").stat().st_mtime_ns == saved["modified"]
        assert goals == [] and runtime.capture.calls == 1
        assert runtime.records.db.one("SELECT COUNT(*) AS n FROM tool_calls")["n"] == 1
        assert runtime.records.checkpoint_run_ids() == []
    finally:
        runtime.close()


def test_decision_after_reopen_accepts_previous_review_without_repeating_capture(tmp_path):
    runtime, workspace, _, _ = runtime_at(tmp_path, capture=Capture(fail=True))
    session = visual_session(runtime, workspace)
    paused = session.start("生成并审查页面")
    assert paused.interruptions[0].value["actions"] == ["retry", "accept", "cancel"]
    modified = (workspace / "index.html").stat().st_mtime_ns
    release_process_resources(runtime, session)
    restarted, _, goals, _ = runtime_at(tmp_path)
    try:
        restored = restarted.get_session(session.id)
        fresh = pending_input(restarted, session.id, paused.run_id)
        done = restored.resume(paused.run_id, {"action": "accept"}, fresh["interruptId"])
        assert done.status == "completed" and done.run_id == paused.run_id
        assert "尚未通过" in done.state["report"]
        assert restarted.capture.calls == 0 and goals == []
        assert (workspace / "index.html").stat().st_mtime_ns == modified
    finally:
        restarted.close()


def test_pending_tool_reopens_with_new_authorization_and_original_deadline(tmp_path):
    clock = Clock()
    runtime, workspace, _, _ = runtime_at(tmp_path, approval_clock=clock, approval_timeout=60)
    session = runtime.create_session(str(workspace))
    paused = session.start("创建页面")
    old = paused.interruptions[0].value
    runtime.close()
    clock.advance(15)
    first_reopen, _, first_goals, _ = runtime_at(
        tmp_path, approval_clock=clock, approval_timeout=600
    )
    try:
        middle = pending_input(first_reopen, session.id, paused.run_id)
        assert middle["expires_at"] == old["expires_at"] and first_goals == []
    finally:
        first_reopen.close()
    clock.advance(15)
    restarted, _, goals, _ = runtime_at(tmp_path, approval_clock=clock, approval_timeout=600)
    try:
        fresh = pending_input(restarted, session.id, paused.run_id)
        assert fresh["expires_at"] == old["expires_at"]
        assert len({item["approval_id"] for item in (old, middle, fresh)}) == 3
        assert len({item["interruptId"] for item in (old, middle, fresh)}) == 3
        api = RuntimeApi(restarted)
        for expired in (old, middle):
            assert (
                resolve(api, session, paused, expired["approval_id"])[0]["error"]["code"]
                == "APPROVAL_EXPIRED"
            )
        unbound = api.handle(
            {
                "id": "late",
                "method": "run.resume",
                "params": {
                    "sessionId": session.id,
                    "runId": paused.run_id,
                    "response": {"approved": True},
                },
            }
        )
        assert unbound[0]["error"]["code"] == "CHECKPOINT_INPUT_REQUIRED"
        assert not (workspace / "index.html").exists()
        assert resolve(api, session, paused, fresh["approval_id"])[0]["result"] == {
            "accepted": True
        }
        assert restarted.records.get("runs", paused.run_id)["status"] == "completed"
        assert (workspace / "index.html").read_text(encoding="utf-8") == "<h1>version 1</h1>"
        assert goals == []
        assert len(restarted.records.db.all("SELECT id FROM tool_calls")) == 1
        assert restarted.records.checkpoint_run_ids() == []
    finally:
        restarted.close()


def test_partial_parallel_batch_reopens_pending_sibling_only(tmp_path):
    class TwoWrites:
        def plan(self, _goal):
            return [
                dict(
                    id=name,
                    description=name,
                    effort=Effort.LOW,
                    tool="write_file",
                    args={"path": name, "content": name},
                )
                for name in ("first.txt", "second.txt")
            ]

    runtime, workspace, _, _ = runtime_at(tmp_path)
    runtime._planner_factory = lambda llm, tools: TwoWrites()
    agent = runtime.records.list_configs("agent")[0]
    runtime.records.save("agent", {"allowed_tools": ["write_file"]}, agent["id"])
    session = runtime.create_session(str(workspace))
    issued = session.start("生成两个文件")
    assert len(issued.interruptions) == 2
    first = next(item for item in issued.interruptions if item.value["args"]["path"] == "first.txt")
    paused = session.resume(issued.run_id, {"approved": True}, first.id)
    assert paused.status == "waiting" and len(paused.interruptions) == 1
    modified = (workspace / "first.txt").stat().st_mtime_ns
    release_process_resources(runtime, session)
    restarted, _, goals, _ = runtime_at(tmp_path)
    try:
        last = pending_input(restarted, session.id, issued.run_id)
        assert last["args"]["path"] == "second.txt"
        result = resolve(RuntimeApi(restarted), session, paused, last["approval_id"])
        assert result[-1]["event"] == "run.completed"
        assert goals == [] and (workspace / "first.txt").stat().st_mtime_ns == modified
        assert (workspace / "second.txt").read_text(encoding="utf-8") == "second.txt"
        assert restarted.records.db.all(
            "SELECT task_key,status FROM tool_calls ORDER BY task_key"
        ) == [{"task_key": name, "status": "completed"} for name in ("first.txt", "second.txt")]
    finally:
        restarted.close()


def test_deadline_passed_while_process_was_offline_denies_write(tmp_path):
    clock = Clock()
    runtime, workspace, _, _ = runtime_at(tmp_path, approval_clock=clock, approval_timeout=30)
    session = runtime.create_session(str(workspace))
    paused = session.start("创建页面")
    release_process_resources(runtime, session)
    clock.advance(31)
    restarted, _, goals, _ = runtime_at(tmp_path, approval_clock=clock)
    try:
        assert restarted.records.get("runs", paused.run_id)["status"] == "failed"
        old = restarted.records.get("interactions", paused.interruptions[0].value["approval_id"])
        assert old["status"] == "expired" and old["resolution"] == "timeout"
        assert not restarted.records.db.all("SELECT id FROM interactions WHERE status='pending'")
        assert not (workspace / "index.html").exists() and goals == []
    finally:
        restarted.close()


@pytest.mark.parametrize("changed_before_reopen", [True, False])
def test_workspace_changes_after_pause_protect_user_edits(tmp_path, changed_before_reopen):
    runtime, workspace, _, _ = runtime_at(tmp_path)
    session = runtime.create_session(str(workspace))
    paused = session.start("创建页面")
    release_process_resources(runtime, session)
    file = workspace / "index.html"
    if changed_before_reopen:
        file.write_text("用户编辑", encoding="utf-8")
    restarted, _, goals, _ = runtime_at(tmp_path)
    try:
        if not changed_before_reopen:
            fresh = pending_input(restarted, session.id, paused.run_id)
            file.write_text("用户编辑", encoding="utf-8")
            frames = resolve(RuntimeApi(restarted), session, paused, fresh["approval_id"])
            assert frames[0]["error"]["code"] == "CHECKPOINT_STALE"
        assert restarted.records.get("runs", paused.run_id)["status"] == "interrupted"
        assert file.read_text(encoding="utf-8") == "用户编辑" and goals == []
        assert not restarted.records.db.all("SELECT id FROM tool_calls")
    finally:
        restarted.close()


if __name__ == "__main__":
    crash_at_preview(Path(sys.argv[1]))
