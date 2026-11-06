"""通过真实会话验证工作树占用与并行委派恢复。"""

import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from test_persistence import runtime_at
from test_recursive_delegation import tree_runtime
from test_retries import history

from aurora.agent.core import ClarificationDecision


def write_plan(path, content):
    """写入指定路径，用文件内容辨别哪个会话运行过。"""
    return [
        {
            "id": "write",
            "description": "保存结果",
            "tool": "write_file",
            "effort": "low",
            "args": {"path": path, "content": content},
            "depends_on": [],
        }
    ]


@pytest.mark.parametrize(
    ("nested_repo", "child_first"), [(False, False), (True, False), (True, True)]
)
def test_overlapping_worktrees_reject_second_session_and_release_after_cancel(
    tmp_path, nested_repo, child_first
):
    runtime, workspace, _, _ = runtime_at(tmp_path)
    nested = workspace / "nested"
    nested.mkdir()
    if nested_repo:
        subprocess.run(["git", "init", str(nested)], check=True, capture_output=True)
        (workspace / ".gitignore").write_text("nested/\n", encoding="utf-8")
    parent = runtime.create_session(str(workspace), approval_mode="always")
    child = runtime.create_session(str(nested), approval_mode="always")
    owner, contender = (child, parent) if child_first else (parent, child)
    entered, release = threading.Event(), threading.Event()

    def plan(goal):
        if "owner" in goal:
            entered.set()
            assert release.wait(10)
        path = "nested/result.txt" if ("owner" in goal) != child_first else "result.txt"
        return write_plan(path, "owner" if "owner" in goal else "contender")

    runtime._planner_factory = lambda model, tools: SimpleNamespace(plan=plan)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            started = pool.submit(owner.start, "owner")
            try:
                assert entered.wait(10)
                with pytest.raises(RuntimeError, match="工作区正在被其他会话使用"):
                    contender.start("contender")
                assert not (nested / "result.txt").exists()
                (run_id,) = owner._active
                assert owner.cancel(run_id)[0]
            finally:
                release.set()
            assert started.result(timeout=10).status == "cancelled"
        assert contender.start("contender").status == "completed"
        assert (nested / "result.txt").read_text(encoding="utf-8") == "contender"
    finally:
        release.set()
        runtime.close()


def test_disjoint_worktrees_can_execute_at_the_same_time(tmp_path):
    runtime, workspace, _, _ = runtime_at(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    pair = threading.Barrier(2, timeout=5)
    left = runtime.create_session(str(workspace), approval_mode="always")
    right = runtime.create_session(str(other), approval_mode="always")

    def plan(goal):
        pair.wait()
        return write_plan("result.txt", "left" if "left" in goal else "right")

    runtime._planner_factory = lambda model, tools: SimpleNamespace(plan=plan)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = [
                pool.submit(session.start, goal)
                for session, goal in [(left, "left"), (right, "right")]
            ]
            assert all(job.result(timeout=10).status == "completed" for job in jobs)
        assert (workspace / "result.txt").read_text(encoding="utf-8") == "left"
        assert (other / "result.txt").read_text(encoding="utf-8") == "right"
    finally:
        runtime.close()


def test_parallel_delegates_resume_only_the_unanswered_clarification(tmp_path, monkeypatch):
    runtime, workspace, agents, _, _ = tree_runtime(tmp_path, monkeypatch, levels=2)
    root, a, b = agents
    for role in (a, b):
        runtime.records.save("agent", {"allowed_tools": ["read_file"]}, role["id"])
    (workspace / "README.md").write_text("资料", encoding="utf-8")
    model_factory = runtime._configured_llm_factory

    def plan(role):
        if role != root["id"]:
            return [
                dict(
                    id="read",
                    description="读取",
                    effort="low",
                    tool="read_file",
                    args={"path": "README.md"},
                )
            ]
        return [
            dict(
                id=key,
                description=key,
                effort="low",
                tool="call_agent",
                args={"agent_id": role["id"], "task": "调查"},
                depends_on=[],
            )
            for key, role in (("a", a), ("b", b))
        ]

    def configure(rt):
        rt._configured_llm_factory = model_factory
        rt._planner_factory = lambda model, tools: SimpleNamespace(
            plan=lambda _: plan(model.agent_id)
        )
        rt._clarifier_factory = lambda model: SimpleNamespace(
            assess=lambda goal, tasks, answers: ClarificationDecision(
                model.agent_id != root["id"] and not answers, "选取目标？"
            )
        )

    configure(runtime)
    session = runtime.create_session(str(workspace))
    try:
        paused = session.start("并行调查")
        run_id = paused.run_id
        assert len(paused.interruptions) == 2
        first, second = paused.interruptions
        paused = session.resume(run_id, "第一个", first.id)
        assert [i.id for i in paused.interruptions] == [second.id]
        assert len(history(runtime, run_id)["toolCalls"]) == 1
    finally:
        runtime.close()
    restored, _, _, _ = runtime_at(tmp_path)
    configure(restored)
    try:
        reopened = restored.get_session(session.id)
        (fresh,) = reopened._result(run_id).interruptions
        assert fresh.id != second.id and fresh.value["agentRunId"] == second.value["agentRunId"]
        assert reopened.resume(run_id, "第二个", fresh.id).status == "completed"
        calls = history(restored, run_id)["toolCalls"]
        assert {call["agentRunId"] for call in calls} == {
            i.value["agentRunId"] for i in (first, second)
        }
        assert len(calls) == 2
    finally:
        restored.close()


@pytest.mark.parametrize("action", ["resume", "cancel", "timeout"])
def test_foreign_write_during_wait_never_enters_rollback_diff(tmp_path, action):
    from test_approvals import Clock, resolve

    from aurora.agent.transport import RuntimeApi

    clock = Clock()
    runtime, workspace, _, _ = runtime_at(tmp_path, approval_clock=clock, approval_timeout=60)
    owner = runtime.create_session(str(workspace))
    holder = runtime.create_session(str(workspace), approval_mode="always")

    def plan(goal):
        if "holder" in goal:
            return write_plan("holder.txt", "holder")
        (first,) = write_plan("first.txt", "first")
        second = dict(
            first, id="next", args={"path": "next.txt", "content": "next"}, depends_on=["write"]
        )
        return [first, second]

    runtime._planner_factory = lambda model, tools: SimpleNamespace(plan=plan)
    entered, release = threading.Event(), threading.Event()

    def during_write(event, payload):
        if event == "tool.completed" and payload["session_id"] == holder.id:
            entered.set()
            release.wait(10)

    unsubscribe = runtime.events.subscribe(during_write)
    try:
        first = owner.start("owner")
        waiting = owner.resume(first.run_id, True, first.interruptions[0].id)
        pending = waiting.interruptions[0]
        assert (workspace / "first.txt").exists()
        with ThreadPoolExecutor(max_workers=1) as pool:
            job = pool.submit(holder.start, "holder")
            try:
                assert entered.wait(10)
                assert (workspace / "holder.txt").read_text(encoding="utf-8") == "holder"
                if action == "resume":
                    frames = resolve(RuntimeApi(runtime), owner, waiting)
                    assert frames[0]["error"]["code"] == "RUN_BUSY"
                    (same,) = owner._result(first.run_id).interruptions
                    assert (
                        same.id == pending.id
                        and same.value["expires_at"] == pending.value["expires_at"]
                    )
                elif action == "cancel":
                    assert owner.cancel(first.run_id)[0]
                else:
                    clock.advance(61)
                    owner.expire_approvals(first.run_id)
            finally:
                release.set()
            assert job.result(timeout=10).status == "completed"
        if action == "resume":
            assert (
                resolve(RuntimeApi(runtime), owner, waiting)[0]["error"]["code"]
                == "CHECKPOINT_STALE"
            )
        expected_status = {"cancel": "cancelled", "timeout": "failed", "resume": "interrupted"}[
            action
        ]
        assert owner._result(first.run_id).status == expected_status
        status = owner.git_status("run", first.run_id)
        assert status["canRollback"] is False
        assert [item["path"] for item in status["files"]] == ["first.txt"]
        assert not (workspace / "next.txt").exists()
        assert (workspace / "holder.txt").read_text(encoding="utf-8") == "holder"
    finally:
        release.set()
        unsubscribe()
        runtime.close()


def test_public_rollback_waits_until_overlapping_run_releases_its_worktree(tmp_path):
    from aurora.agent.transport import RuntimeApi

    runtime, workspace, _, _ = runtime_at(tmp_path)
    owner = runtime.create_session(str(workspace), approval_mode="always")
    observer = runtime.create_session(str(workspace), approval_mode="always")
    entered, release = threading.Event(), threading.Event()

    def plan(goal):
        if "reading" in goal:
            entered.set()
            assert release.wait(10)
            return [
                dict(
                    id="read",
                    description="读取",
                    tool="read_file",
                    effort="low",
                    args={"path": "owner.txt"},
                )
            ]
        return write_plan("owner.txt", "owner")

    runtime._planner_factory = lambda model, tools: SimpleNamespace(plan=plan)
    try:
        completed = owner.start("保存")
        request = {
            "id": "rollback",
            "method": "git.rollback",
            "params": {"sessionId": owner.id, "runId": completed.run_id},
        }
        api = RuntimeApi(runtime)
        with ThreadPoolExecutor(max_workers=1) as pool:
            reading = pool.submit(observer.start, "reading")
            try:
                assert entered.wait(10)
                frames = api.handle(request)
                assert frames[0]["error"]["code"] == "RUN_BUSY"
                assert (workspace / "owner.txt").read_text(encoding="utf-8") == "owner"
            finally:
                release.set()
            assert reading.result(timeout=10).status == "completed"
        assert api.handle(request)[0]["result"]["reverted"] is True
        assert not (workspace / "owner.txt").exists()
    finally:
        release.set()
        runtime.close()
