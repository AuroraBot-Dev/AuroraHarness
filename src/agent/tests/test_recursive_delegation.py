"""验证受限递归的隔离、深层审批、取消、重试与历史。"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage
from test_cancellation import wait_until
from test_persistence import runtime_at
from test_retries import history

from aurora.agent.capability import Capability, CapabilityPolicy, CapabilityScope
from aurora.agent.core import Effort, ExecutionLimits
from aurora.agent.orchestration.runtime import RunExecutionError
from aurora.agent.transport import RuntimeApi


def tree_runtime(tmp_path, monkeypatch, *, limits=None, cycle=False, slow=False, levels=3):
    """创建根角色和三层开放角色，叶子执行真实文件或进程工具。"""
    monkeypatch.setenv("AGENT_API_KEY", "recursive-test-key")
    runtime, workspace, _, _ = runtime_at(tmp_path, execution_limits=limits)
    records = runtime.records
    model = records.list_configs("model")[0]
    records.save("model", {"model_name": "recursive-test"}, model["id"])
    root = records.list_configs("agent")[0]
    records.save(
        "agent", {"allowed_tools": ["call_agent"], "instructions": "root-private"}, root["id"]
    )
    agents = [root]
    names = ("A", "B", "C") if levels == 3 else tuple(f"role-{index}" for index in range(levels))
    for name in names:
        agents.append(
            records.save(
                "agent",
                {
                    "name": name,
                    "instructions": name + "-private",
                    "callable": True,
                    "model_config_id": model["id"],
                    "allowed_tools": ["write_file", "run_python"]
                    if name == names[-1]
                    else ["call_agent"],
                },
            )
        )
    seen, models = {}, []

    class Planner:
        """用重复的局部 ID 构造多层委派，记录每个角色的私有输入。"""

        def __init__(self, agent_id, tools):
            self.agent_id, self.tools = agent_id, tools

        def set_instructions(self, value):
            seen.setdefault(self.agent_id, {})["instructions"] = value

        def plan(self, goal):
            seen.setdefault(self.agent_id, {})["goal"] = goal
            index = next(i for i, agent in enumerate(agents) if agent["id"] == self.agent_id)
            if index < len(agents) - 1:
                target = agents[1] if cycle and index == 2 else agents[index + 1]
                return [
                    dict(
                        id="delegate",
                        description="委派" + target["name"],
                        effort=Effort.LOW,
                        tool="call_agent",
                        args={"agent_id": target["id"], "task": "仅处理" + target["name"] + "范围"},
                    )
                ]
            return [
                dict(
                    id="one",
                    description="首次写入",
                    effort=Effort.LOW,
                    tool="write_file",
                    args={"path": "one.txt", "content": "one"},
                ),
                dict(
                    id="two",
                    description="后续任务",
                    effort=Effort.LOW,
                    tool="run_python" if slow else "write_file",
                    args={
                        "code": "from pathlib import Path; import time; Path('ready').touch(); "
                        "time.sleep(20); Path('late').touch()"
                    }
                    if slow
                    else {"path": "two.txt", "content": "two"},
                ),
            ]

    def create_model(config):
        """记录客户端创建顺序，边界拒绝前不创建更深层模型。"""
        agent_id = config["agent"]["id"]
        models.append(agent_id)
        return SimpleNamespace(agent_id=agent_id, invoke=lambda messages: AIMessage(content="ok"))

    runtime._configured_llm_factory = create_model
    runtime._planner_factory = lambda model, tools: Planner(model.agent_id, tools)
    return runtime, workspace, agents, seen, models


def test_nested_approvals_resume_deepest_role_and_publish_complete_tree(tmp_path, monkeypatch):
    runtime, workspace, agents, seen, _ = tree_runtime(tmp_path, monkeypatch)
    events = []
    unsubscribe = runtime.events.subscribe(lambda name, payload: events.append((name, payload)))
    try:
        session = runtime.create_session(str(workspace))
        paused = session.start("构造四层角色")
        assert paused.status == "waiting"
        first = paused.interruptions[0].value
        assert first["agentId"] == agents[3]["id"]
        before = history(runtime, paused.run_id)
        deepest = next(row for row in before["agentRuns"] if row["agentId"] == agents[3]["id"])
        assert first["agentRunId"] == deepest["id"]
        assert first["taskId"] == next(
            row["id"]
            for row in before["tasks"]
            if row["agentRunId"] == deepest["id"] and row["taskKey"] == "one"
        )
        assert (
            next(row["status"] for row in before["tasks"] if row["id"] == first["taskId"])
            == "waiting"
        )
        assert (
            runtime.records.db.one("SELECT agent_run_id FROM interactions")["agent_run_id"]
            == deepest["id"]
        )
        assert len(before["tasks"]) == 5 and not before["toolCalls"]
        paused = session.resume(paused.run_id, {"approved": True})
        assert paused.status == "waiting" and (workspace / "one.txt").exists()
        assert not (workspace / "two.txt").exists()
        assert first["taskId"] != paused.interruptions[0].value["taskId"]
        assert (
            first["delegationInterruptId"] != paused.interruptions[0].value["delegationInterruptId"]
        )
        result = session.resume(paused.run_id, {"approved": True})
        assert result.status == "completed" and (workspace / "two.txt").exists()
        detail = history(runtime, result.run_id)
        assert all(row["status"] == "completed" for row in detail["agentRuns"])
        assert all(row["status"] == "completed" for row in detail["tasks"])
        assert len(detail["toolCalls"]) == 2
        for row in detail["agentRuns"]:
            if row["parentTaskId"]:
                assert row["input"]["delegation_depth"] == len(row["input"]["role_path"])
        for index, agent in enumerate(agents[1:], 1):
            assert "root-private" not in seen[agent["id"]]["instructions"]
            assert "root-private" not in seen[agent["id"]]["goal"]
            assert agent["name"] + "-private" in seen[agent["id"]]["instructions"]
        created = [payload for name, payload in events if name == "task.created"]
        assert len(created) == 4
        assert all(payload["task"]["parent_id"] for payload in created)
        assert len({payload["task_id"] for payload in created}) == 4
        assert len([name for name, _ in events if name == "tool.completed"]) == 2
    finally:
        unsubscribe()
        runtime.close()


@pytest.mark.parametrize(
    "limits,code,models_count",
    [
        (ExecutionLimits(max_depth=2), "DELEGATION_DEPTH_LIMIT", 3),
        (ExecutionLimits(max_total_tasks=4), "RUN_TASK_LIMIT", 4),
        (ExecutionLimits(max_plan_tasks=1), "PLAN_TASK_LIMIT", 4),
    ],
)
def test_recursive_limits_reject_before_leaf_side_effects(
    tmp_path, monkeypatch, limits, code, models_count
):
    runtime, workspace, _, _, models = tree_runtime(tmp_path, monkeypatch, limits=limits)
    try:
        session = runtime.create_session(str(workspace), approval_mode="always")
        with pytest.raises(RunExecutionError) as failure:
            session.start("资源受限的递归")
        assert failure.value.code == code
        run = runtime.records.db.one("SELECT id,status,config_snapshot_json,state_json FROM runs")
        assert run["status"] == "failed" and len(models) == models_count
        assert json.loads(run["config_snapshot_json"])["execution_limits"] == limits.to_dict()
        assert json.loads(run["state_json"])["error_code"] == code
        assert not history(runtime, run["id"])["toolCalls"]
        assert not (workspace / "one.txt").exists()
        assert all(row["status"] == "failed" for row in history(runtime, run["id"])["agentRuns"])
    finally:
        runtime.close()


def test_cycle_is_bounded_even_when_roles_call_each_other(tmp_path, monkeypatch):
    runtime, workspace, _, _, models = tree_runtime(
        tmp_path, monkeypatch, limits=ExecutionLimits(max_depth=3), cycle=True
    )
    try:
        session = runtime.create_session(str(workspace), approval_mode="always")
        with pytest.raises(RunExecutionError) as failure:
            session.start("循环委派")
        assert failure.value.code == "DELEGATION_DEPTH_LIMIT"
        assert len(models) == 4
        assert len(runtime.records.db.all("SELECT id FROM agent_runs")) == 4
    finally:
        runtime.close()


def test_configured_depth_sixteen_finishes_without_exponential_result_wrapping(
    tmp_path, monkeypatch
):
    runtime, workspace, _, _, _ = tree_runtime(
        tmp_path, monkeypatch, levels=16, limits=ExecutionLimits(max_depth=16)
    )
    try:
        session = runtime.create_session(str(workspace), approval_mode="always")
        result = session.start("验证支持的最大深度")
        assert result.status == "completed"
        assert len(history(runtime, result.run_id)["agentRuns"]) == 17
        assert len(result.state["report"]) < 20_000
        assert (workspace / "one.txt").exists() and (workspace / "two.txt").exists()
    finally:
        runtime.close()


def test_wire_notifications_include_deep_parent_ids_and_specific_pending_leaf(
    tmp_path, monkeypatch
):
    runtime, workspace, agents, _, _ = tree_runtime(tmp_path, monkeypatch)
    frames = []
    api = RuntimeApi(runtime)
    unsubscribe = api.subscribe(frames.append)
    try:
        session = runtime.create_session(str(workspace))
        api.process_wire(
            {
                "protocol_version": 1,
                "request_id": "nested",
                "method": "run.start",
                "params": {"sessionId": session.id, "goal": "协议递归"},
            },
            frames.append,
        )
        response = next(frame for frame in frames if frame.get("request_id") == "nested")
        assert response["ok"] and response["result"]["status"] == "waiting"
        created = [frame for frame in frames if frame.get("type") == "task.created"]
        assert len(created) == 4
        assert all(frame["task_id"] == frame["payload"]["task"]["id"] for frame in created)
        assert all(frame["payload"]["task"]["parent_id"] for frame in created)
        approval = next(frame for frame in frames if frame.get("type") == "approval.requested")
        assert approval["payload"]["agentId"] == agents[3]["id"]
        assert approval["payload"]["taskId"]
        assert not any(frame.get("type", "").startswith("tool.") for frame in frames)
        json.dumps(frames, ensure_ascii=False, allow_nan=False)
    finally:
        unsubscribe()
        runtime.close()


@pytest.mark.parametrize("target", ["run", "subtree", "leaf"])
def test_recursive_retry_preserves_completed_leaf_and_source_scope(tmp_path, monkeypatch, target):
    runtime, workspace, agents, _, _ = tree_runtime(tmp_path, monkeypatch)
    try:
        session = runtime.create_session(str(workspace))
        paused = session.start("递归重试")
        paused = session.resume(paused.run_id, {"approved": True})
        runtime.cancel_run(session.id, paused.run_id)
        (workspace / "one.txt").write_text("保留修改", encoding="utf-8")
        before = history(runtime, paused.run_id)
        owner = next(row for row in before["agentRuns"] if row["agentId"] == agents[3]["id"])
        if target == "leaf":
            task_id = next(
                row["id"]
                for row in before["tasks"]
                if row["agentRunId"] == owner["id"] and row["taskKey"] == "two"
            )
        elif target == "subtree":
            task_id = owner["parentTaskId"]
        else:
            task_id = None
        retried = session.retry(paused.run_id, task_id)
        assert retried.status == "waiting"
        result = session.resume(retried.run_id, {"approved": True})
        assert result.status == "completed" and (workspace / "two.txt").exists()
        assert (workspace / "one.txt").read_text(encoding="utf-8") == "保留修改"
        assert len(history(runtime, result.run_id)["toolCalls"]) == 1
        assert history(runtime, paused.run_id) == before
    finally:
        runtime.close()


def test_recursive_scope_uses_full_path_and_frozen_limits_survive_approval(tmp_path, monkeypatch):
    runtime, workspace, agents, _, _ = tree_runtime(tmp_path, monkeypatch)
    runtime.capabilities.register(
        CapabilityPolicy(
            "nested-only",
            lambda context: Capability(context="nested-context"),
            scope=CapabilityScope("subtree", workspace, ("delegate", "delegate")),
        )
    )
    try:
        session = runtime.create_session(str(workspace))
        paused = session.start("验证作用域")
        detail = history(runtime, paused.run_id)
        by_agent = {row["agentId"]: row for row in detail["agentRuns"]}
        assert "nested-only" not in by_agent[agents[1]["id"]]["input"]["capabilities"]["applied"]
        for agent in agents[2:]:
            assert "nested-only" in by_agent[agent["id"]]["input"]["capabilities"]["applied"]
        runtime.execution_limits = ExecutionLimits(max_depth=0)
        paused = session.resume(paused.run_id, {"approved": True})
        assert session.resume(paused.run_id, {"approved": True}).status == "completed"
    finally:
        runtime.close()


def test_cancel_deep_process_preserves_completed_leaf_and_releases_workspace(tmp_path, monkeypatch):
    runtime, workspace, _, _, _ = tree_runtime(tmp_path, monkeypatch, slow=True)
    try:
        session = runtime.create_session(str(workspace), approval_mode="always")
        with ThreadPoolExecutor(max_workers=1) as workers:
            future = workers.submit(session.start, "取消深层进程")
            try:
                wait_until(lambda: (workspace / "ready").exists())
                run = runtime.records.db.one("SELECT id FROM runs")
                runtime.cancel_run(session.id, run["id"])
                assert future.result(timeout=5).status == "cancelled"
            finally:
                if not future.done():
                    runtime.cancel_run(
                        session.id, runtime.records.db.one("SELECT id FROM runs")["id"]
                    )
        assert (workspace / "one.txt").exists() and not (workspace / "late").exists()
        calls = history(runtime, run["id"])["toolCalls"]
        assert [call["status"] for call in calls] == ["completed", "cancelled"]
        with pytest.raises(RuntimeError, match="bad"):
            runtime._planner_factory = lambda llm, tools: (_ for _ in ()).throw(RuntimeError("bad"))
            session.start("检查锁已释放")
    finally:
        runtime.close()
