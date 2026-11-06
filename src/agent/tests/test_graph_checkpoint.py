"""覆盖暂停图的独立重建、已完成并行结果与递归审批桥。"""

import json
from types import SimpleNamespace

import pytest
from langgraph.types import Command
from test_graph_workflow import OnceClarifier, RecordingPlanner, make_tools
from test_recursive_delegation import history, tree_runtime

from aurora.agent.core import Effort, build_delegation_graph
from aurora.agent.orchestration.graph_checkpoint import (
    _decode,
    _encode,
    _snapshot,
    checkpoint_saver,
    export_graphs,
    restore_graphs,
)
from aurora.agent.safety import build_gate
from aurora.agent.tools.base import RiskLevel, Tool


def test_concurrent_completed_sibling_is_not_replayed_after_json_roundtrip():
    effects = []
    planner = SimpleNamespace(
        plan=lambda goal: [
            dict(id=name, description=name, tool=name, effort=Effort.LOW, args={})
            for name in ("read", "write")
        ]
    )
    tools = {
        key: Tool(key, key, lambda key=key: effects.append(key) or key, risk=risk)
        for key, risk in (("read", RiskLevel.READ), ("write", RiskLevel.WRITE))
    }

    def graph(saved=None):
        return build_delegation_graph(
            planner, tools, build_gate("interrupt"), checkpointer=checkpoint_saver(saved)
        )

    config = {"configurable": {"thread_id": "root"}}
    original = graph()
    paused = original.invoke({"goal": "并行"}, config)
    assert effects == ["read"]
    checkpoint = json.loads(json.dumps(_snapshot(original, "root")))
    restarted = graph({"id": "root", "cursor": checkpoint})
    report = restarted.invoke(
        Command(resume={paused["__interrupt__"][0].id: {"approved": True}}), config
    )["report"]
    assert effects == ["read", "write"]
    assert "read ✓" in report and "write ✓" in report


def test_clarification_restores_planner_history_and_replans_once():
    planner = RecordingPlanner()

    def graph(saved=None):
        return build_delegation_graph(
            planner, make_tools(), clarifier=OnceClarifier(), checkpointer=checkpoint_saver(saved)
        )

    config = {"configurable": {"thread_id": "root"}}
    original = graph()
    request = original.invoke({"goal": "构建应用"}, config)["__interrupt__"][0]
    restored = graph({"id": "root", "cursor": json.loads(json.dumps(_snapshot(original, "root")))})
    result = restored.invoke(Command(resume={request.id: "macOS"}), config)
    assert "macOS" in result["report"] and len(planner.goals) == 2


def test_graph_marker_collision_roundtrips_and_unknown_types_are_rejected():
    payload = {
        "$aurora.graph": ["effort", "low"],
        "nested": {"$aurora.graph": ["execute", {"current_task": {}}]},
    }
    assert _decode(json.loads(json.dumps(_encode(payload)))) == payload
    with pytest.raises(ValueError):
        _decode({"$aurora.graph": ["module", "subprocess.Popen", ["id"]]})
    with pytest.raises(ValueError):
        _encode(object())


def test_recursive_pending_and_resolved_bridges_survive_graph_rebuild(tmp_path, monkeypatch):
    runtime, workspace, _, _, _ = tree_runtime(tmp_path, monkeypatch)
    try:
        session = runtime.create_session(str(workspace))
        paused = session.start("重建多层委派")
        for index in range(2):
            run_id = paused.run_id
            state = session._active[run_id]
            previous = history(runtime, run_id)
            packed = json.loads(json.dumps(export_graphs(state)))
            for key in (
                "graph",
                "children",
                "task_ids",
                "code_agent_run_id",
                "graph_input",
                "planner",
            ):
                state.pop(key, None)
            restore_graphs(session, run_id, state, packed)
            paused = session.resume(run_id, {"approved": True}, paused.interruptions[0].id)
            after = history(runtime, run_id)
            assert {a["id"] for a in previous["agentRuns"]} == {a["id"] for a in after["agentRuns"]}
            assert {t["id"] for t in previous["tasks"]} == {t["id"] for t in after["tasks"]}
            assert len(after["toolCalls"]) == index + 1
        assert paused.status == "completed"
        assert (workspace / "one.txt").exists() and (workspace / "two.txt").exists()
    finally:
        runtime.close()


@pytest.mark.parametrize("damage", ["args", "child", "interrupt", "engine"])
def test_damaged_graph_does_not_hydrate_or_execute(tmp_path, monkeypatch, damage):
    runtime, workspace, _, _, _ = tree_runtime(tmp_path, monkeypatch)
    try:
        session = runtime.create_session(str(workspace))
        paused = session.start("验证保存的委派树")
        state = session._active[paused.run_id]
        saved = json.loads(json.dumps(export_graphs(state)))
        if damage == "args":
            tasks = saved["root"]["cursor"]["checkpoint"]["channel_values"]["tasks"]
            tasks[0]["args"]["task"] = "修改后的目标"
        elif damage == "child":
            saved["children"].pop(next(iter(saved["children"])))
        elif damage == "interrupt":
            state = dict(state, interrupt_ids={})
        else:
            saved["engine"] = "unsupported"
        with pytest.raises(ValueError):
            restore_graphs(session, paused.run_id, state, saved)
        assert "graph_restart" not in state
        assert not (workspace / "one.txt").exists()
    finally:
        runtime.close()


def test_reopened_runtime_finishes_deepest_pending_write_once(tmp_path, monkeypatch):
    from test_approvals import resolve
    from test_persistence import runtime_at
    from test_workflow_checkpoints import pending_input

    from aurora.agent.transport import RuntimeApi

    original, workspace, _, _, _ = tree_runtime(tmp_path, monkeypatch)
    try:
        first_session = original.create_session(str(workspace))
        first = first_session.start("递归委派，等待第二次写入")
        paused = first_session.resume(first.run_id, {"approved": True}, first.interruptions[0].id)
        assert paused.status == "waiting"
        old = paused.interruptions[0].value
        mtime = (workspace / "one.txt").stat().st_mtime_ns
        before = history(original, paused.run_id)
        assert len(before["agentRuns"]) == 4
        assert len(before["toolCalls"]) == 1
        assert not (workspace / "two.txt").exists()
    finally:
        original.close()
    reopened, _, plans, _ = runtime_at(tmp_path)
    try:
        api = RuntimeApi(reopened)
        session = reopened.get_session(first_session.id)
        new = pending_input(reopened, session.id, paused.run_id)
        assert new["approval_id"] != old["approval_id"]
        assert new["interruptId"] != old["interruptId"]
        assert new["expires_at"] == old["expires_at"]
        assert new["taskId"] == old["taskId"] and new["agentRunId"] == old["agentRunId"]
        assert (
            resolve(api, session, paused, old["approval_id"])[0]["error"]["code"]
            == "APPROVAL_EXPIRED"
        )
        assert not (workspace / "two.txt").exists()
        frames = resolve(api, session, paused, new["approval_id"])
        assert frames[-1]["event"] == "run.completed"
        after = history(reopened, paused.run_id)
        assert {role["id"] for role in before["agentRuns"]} == {
            role["id"] for role in after["agentRuns"]
        }
        assert {task["id"] for task in before["tasks"]} == {task["id"] for task in after["tasks"]}
        assert [call["taskKey"] for call in after["toolCalls"]] == ["one", "two"]
        assert (workspace / "one.txt").stat().st_mtime_ns == mtime
        assert (workspace / "two.txt").read_text(encoding="utf-8") == "two"
        assert not plans
    finally:
        reopened.close()


def test_workspace_change_after_fresh_approval_ack_preserves_user_write(tmp_path):
    from test_persistence import runtime_at
    from test_workflow_checkpoints import pending_input

    original, workspace, _, _ = runtime_at(tmp_path)
    try:
        first_session = original.create_session(str(workspace))
        paused = first_session.start("写入页面")
    finally:
        original.close()
    reopened, _, _, _ = runtime_at(tmp_path)
    try:
        session = reopened.get_session(first_session.id)
        fresh = pending_input(reopened, session.id, paused.run_id)
        target = workspace / "index.html"
        result = session.resume(
            paused.run_id,
            {"approved": True},
            approval_id=fresh["approval_id"],
            on_accepted=lambda: target.write_text("用户刚保存的内容", encoding="utf-8"),
        )
        assert result.status == "interrupted"
        assert result.state["error_code"] == "CHECKPOINT_STALE"
        assert target.read_text(encoding="utf-8") == "用户刚保存的内容"
        assert history(reopened, paused.run_id)["toolCalls"] == []
    finally:
        reopened.close()
