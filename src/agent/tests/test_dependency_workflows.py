"""验证依赖调度通过持久会话、跨进程恢复与真实委派入口工作。"""

import threading
from types import SimpleNamespace

import pytest
from test_persistence import runtime_at
from test_recursive_delegation import tree_runtime
from test_retries import history


def write_step(key, dependencies):
    """创建有显式前置关系的文件写入。"""
    return dict(
        id=key,
        description=key,
        effort="low",
        tool="write_file",
        args={"path": key + ".txt", "content": key},
        depends_on=dependencies,
    )


def test_blocked_graph_resumes_independent_approval_with_exact_task_facts(tmp_path):
    runtime, workspace, _, _ = runtime_at(tmp_path)
    plan = [
        write_step("publish", ["package", "audit"]),
        write_step("package", ["prepare"]),
        write_step("prepare", []),
        write_step("audit", []),
    ]
    runtime._planner_factory = lambda model, tools: SimpleNamespace(plan=lambda goal: plan)
    session = runtime.create_session(str(workspace))
    events = []
    runtime.events.subscribe(lambda event, data: events.append((event, data)))
    try:
        paused = session.start("准备、发布和独立检查")
        run_id = paused.run_id
        rows = {row["taskKey"]: row for row in history(runtime, run_id)["tasks"]}
        ids = {key: row["id"] for key, row in rows.items()}
        assert paused.interruptions[0].value["taskId"] == ids["prepare"]
        assert [rows[key]["status"] for key in ("publish", "package", "prepare", "audit")] == [
            "queued",
            "queued",
            "waiting",
            "queued",
        ]
        paused = session.resume(run_id, False, paused.interruptions[0].id)
        assert paused.interruptions[0].value["taskId"] == ids["audit"]
        stored = history(runtime, run_id)
        rows = {row["id"]: row for row in stored["tasks"]}
        assert rows[ids["package"]]["blockedBy"] == [ids["prepare"]]
        assert rows[ids["publish"]]["blockedBy"] == [ids["package"]]
        assert rows[ids["publish"]]["dependsOn"] == [ids["package"], ids["audit"]]
        assert {p["task_id"] for e, p in events if e == "task.started"} == {
            ids["prepare"],
            ids["audit"],
        }
        assert {p["task_id"] for e, p in events if e == "task.blocked"} == {
            ids["package"],
            ids["publish"],
        }
        assert not stored["toolCalls"]
        assert runtime.records.load_checkpoint(run_id)
    finally:
        runtime.close()
    reopened, _, plans, _ = runtime_at(tmp_path)
    try:
        same = reopened.get_session(session.id)
        (approval,) = same._result(run_id).interruptions
        assert approval.value["taskId"] == ids["audit"]
        with pytest.raises(RuntimeError, match="失败"):
            same.resume(run_id, True, approval.id)
        actual = history(reopened, run_id)
        assert actual["status"] == "failed"
        assert [call["taskKey"] for call in actual["toolCalls"]] == ["audit"]
        assert (workspace / "audit.txt").read_text(encoding="utf-8") == "audit"
        assert not (workspace / "publish.txt").exists() and plans == []
    finally:
        reopened.close()


def test_read_only_delegated_siblings_really_overlap_and_join_before_write(tmp_path, monkeypatch):
    runtime, workspace, agents, _, _ = tree_runtime(tmp_path, monkeypatch, levels=2)
    root, a, b = agents
    for role in (a, b):
        runtime.records.save("agent", {"allowed_tools": ["read_file"]}, role["id"])
    runtime.records.save("agent", {"allowed_tools": ["call_agent", "write_file"]}, root["id"])
    (workspace / "README.md").write_text("shared input", encoding="utf-8")
    barrier = threading.Barrier(2, timeout=7)
    entered = []

    def plan(role):
        if role == root["id"]:
            return [
                write_step("join", ["first", "second"]),
                *[
                    dict(
                        id=key,
                        description=key,
                        effort="low",
                        tool="call_agent",
                        args={"agent_id": child["id"], "task": "读取说明"},
                        depends_on=[],
                    )
                    for key, child in (("first", a), ("second", b))
                ],
            ]
        entered.append(role)
        barrier.wait()
        return [
            dict(
                id="read",
                description="读取",
                effort="low",
                tool="read_file",
                args={"path": "README.md"},
            )
        ]

    runtime._planner_factory = lambda model, tools: SimpleNamespace(
        plan=lambda _: plan(model.agent_id)
    )
    try:
        session = runtime.create_session(str(workspace), approval_mode="always")
        result = session.start("两位只读角色同时检查，再汇总")
        assert result.status == "completed"
        data = history(runtime, result.run_id)
        assert set(entered) == {a["id"], b["id"]}
        assert len({task["agentRunId"] for task in data["tasks"] if task["taskKey"] == "read"}) == 2
        assert [call["toolName"] for call in data["toolCalls"]] == [
            "read_file",
            "read_file",
            "write_file",
        ]
        assert (workspace / "join.txt").exists()
    finally:
        runtime.close()


def test_parallel_role_proof_accounts_for_reachable_writers_and_runtime_extensions(
    tmp_path, monkeypatch
):
    from aurora.agent.orchestration.task_execution import read_only_delegates

    runtime, workspace, agents, _, _ = tree_runtime(tmp_path, monkeypatch, levels=3)
    _, reader, bridge, writer = agents
    session = runtime.create_session(str(workspace))
    try:
        for role, allowed in [(reader, ["read_file"]), (bridge, ["call_agent"]), (writer, ["*"])]:
            runtime.records.save("agent", {"allowed_tools": allowed}, role["id"])
        state = {"capabilities": runtime.capabilities.freeze(), "snapshot": session._snapshot()}
        assert read_only_delegates(session, state) == {reader["id"]}
        runtime.records.save("agent", {"allowed_tools": ["read_file"]}, writer["id"])
        state["snapshot"] = session._snapshot()
        assert {reader["id"], bridge["id"], writer["id"]} <= read_only_delegates(session, state)
        runtime._mcp_tools["connected"] = {}
        assert not read_only_delegates(session, state)
    finally:
        runtime.close()
