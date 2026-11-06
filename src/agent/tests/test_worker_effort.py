"""验证委派强度、规划边界与审批恢复的真实行为。"""

import pytest
from langchain_core.messages import AIMessage
from test_persistence import runtime_at
from test_retries import history
from test_workflow_checkpoints import pending_input, release_process_resources

from aurora.agent.core import ClarificationDecision, Effort, NoClarifier
from aurora.agent.core.effort_policy import get_effort_policy
from aurora.agent.orchestration.runtime import RunExecutionError


def worker_runtime(tmp_path, monkeypatch, tier, *, count=1, local=False, pause=False, limit=None):
    """使用真实任务持久化和可观察模型构造器创建父子角色。"""
    monkeypatch.setenv("AGENT_API_KEY", "effort-test-key")
    runtime, workspace, _, _ = runtime_at(tmp_path)
    records = runtime.records
    agents = records.list_configs("agent")
    root = agents[0]
    child = next((agent for agent in agents if agent["name"] == "effort-child"), None)
    if child is None:
        model = records.list_configs("model")[0]
        records.save(
            "model",
            {"model_name": "effort-test", "parameters": {"max_tokens": limit} if limit else {}},
            model["id"],
        )
        records.save("agent", {"allowed_tools": ["call_agent"]}, root["id"])
        child = records.save(
            "agent",
            {
                "name": "effort-child",
                "callable": True,
                "model_config_id": model["id"],
                "allowed_tools": ["write_file"],
                "instructions": "只处理委派目标",
            },
        )
    seen, calls = [], []

    class Model:
        def __init__(self, config):
            self.agent_id = config["agent"]["id"]
            seen.append(config)

        def invoke(self, messages, **kwargs):
            calls.append((self.agent_id, messages, kwargs))
            return AIMessage(
                content="答案",
                usage_metadata={"input_tokens": 4, "output_tokens": 2, "total_tokens": 6},
            )

    class Planner:
        def __init__(self, model):
            self.root = model.agent_id == root["id"]

        def plan(self, goal):
            if self.root:
                return [
                    dict(
                        id="delegate",
                        description="交给子角色",
                        effort=Effort(tier),
                        tool="call_agent",
                        args={"agent_id": child["id"], "task": "处理委派目标"},
                    )
                ]
            tasks = [
                dict(
                    id=str(index),
                    description="完成子步骤",
                    effort=Effort.HIGH,
                    tool="write_file" if local else "agent_respond",
                    args={"path": f"step-{index}.txt", "content": "done"}
                    if local
                    else {"prompt": "回答目标"},
                )
                for index in range(count)
            ]
            if pause:
                tasks.insert(
                    0,
                    dict(
                        id="write",
                        description="批准写入",
                        effort=Effort.LOW,
                        tool="write_file",
                        args={"path": "approved.txt", "content": "done"},
                    ),
                )
            return tasks

    runtime._configured_llm_factory = Model
    runtime._planner_factory = lambda model, tools: Planner(model)
    return runtime, workspace, child, seen, calls


@pytest.mark.parametrize("tier,cap", [("low", 256), ("medium", 1024), ("high", 2048)])
def test_delegated_effort_changes_factory_actual_worker_and_saved_ownership(
    tmp_path, monkeypatch, tier, cap
):
    runtime, workspace, child, seen, calls = worker_runtime(tmp_path, monkeypatch, tier)
    try:
        result = runtime.create_session(str(workspace), approval_mode="always").start("实际回答")
        assert result.status == "completed"
        effective = next(config for config in seen if config["agent"]["id"] == child["id"])
        assert effective["model"]["parameters"]["max_tokens"] == cap
        assert len(calls) == 1 and calls[0][0] == child["id"]
        assert calls[0][2] == {"max_tokens": cap}
        assert get_effort_policy(tier).instructions in calls[0][1][0].content
        execution = next(
            row
            for row in history(runtime, result.run_id)["agentRuns"]
            if row["agentId"] == child["id"]
        )
        assert execution["input"]["effort_policy"]["effort"] == tier
        assert execution["configSnapshot"]["effort_policy"]["max_output_tokens"] == cap
    finally:
        runtime.close()


@pytest.mark.parametrize("tier,count", [("low", 4), ("medium", 7), ("high", 7)])
def test_worker_rejects_over_budget_plan_before_any_local_effect(
    tmp_path, monkeypatch, tier, count
):
    runtime, workspace, _, _, calls = worker_runtime(
        tmp_path, monkeypatch, tier, count=count, local=True
    )
    try:
        with pytest.raises(RunExecutionError) as failure:
            runtime.create_session(str(workspace), approval_mode="always").start("拒绝超量")
        assert failure.value.code == "PLAN_TASK_LIMIT"
        assert not list(workspace.glob("step-*.txt")) and not calls
    finally:
        runtime.close()


def test_local_tools_need_no_worker_model_invocation_and_keep_lower_cap(tmp_path, monkeypatch):
    runtime, workspace, child, seen, calls = worker_runtime(
        tmp_path, monkeypatch, "high", local=True, limit=64
    )
    try:
        result = runtime.create_session(str(workspace), approval_mode="always").start("直接写入")
        assert result.status == "completed"
        assert (workspace / "step-0.txt").read_text(encoding="utf-8") == "done"
        assert not calls
        assert (
            next(config for config in seen if config["agent"]["id"] == child["id"])["model"][
                "parameters"
            ]["max_tokens"]
            == 64
        )
    finally:
        runtime.close()


def test_checkpoint_and_leaf_retry_preserve_original_worker_effort(tmp_path, monkeypatch):
    runtime, workspace, child, _, calls = worker_runtime(tmp_path, monkeypatch, "low", pause=True)
    session = runtime.create_session(str(workspace))
    paused = session.start("先确认再回答")
    assert paused.status == "waiting" and not calls
    release_process_resources(runtime, session)
    restarted, _, _, seen, calls = worker_runtime(tmp_path, monkeypatch, "low", pause=True)
    try:
        session = restarted.get_session(session.id)
        request = pending_input(restarted, session.id, paused.run_id)
        result = session.resume(paused.run_id, {"approved": True}, request["interruptId"])
        assert result.status == "completed" and len(calls) == 1
        assert calls[0][2] == {"max_tokens": 256}
        assert (
            next(config for config in seen if config["agent"]["id"] == child["id"])[
                "effort_policy"
            ]["effort"]
            == "low"
        )
        task = next(
            row
            for row in history(restarted, result.run_id)["tasks"]
            if row["tool"] == "agent_respond"
        )
        retry = session.retry(result.run_id, task["id"])
        assert retry.status == "completed" and len(calls) == 2
        assert calls[1][2] == {"max_tokens": 256}
        assert get_effort_policy("low").instructions in calls[1][1][0].content
        execution = history(restarted, retry.run_id)["agentRuns"][0]
        assert execution["input"]["effort_policy"]["effort"] == "low"
    finally:
        restarted.close()


@pytest.mark.parametrize("tier,rounds", [("low", 3), ("medium", 6), ("high", 6)])
def test_worker_clarification_cannot_exceed_its_planning_rounds(
    tmp_path, monkeypatch, tier, rounds
):
    runtime, workspace, child, _, calls = worker_runtime(tmp_path, monkeypatch, tier)
    create = runtime._planner_factory
    goals = []

    class Planner:
        def __init__(self, model, tools):
            self.child = model.agent_id == child["id"]
            self.original = create(model, tools)

        def plan(self, goal):
            if self.child:
                goals.append(goal)
            return self.original.plan(goal)

    class Clarifier:
        def assess(self, goal, tasks, history):
            return ClarificationDecision(True, "补充范围", "测试规划边界")

    runtime._planner_factory = Planner
    runtime._clarifier_factory = lambda model: (
        Clarifier() if model.agent_id == child["id"] else NoClarifier()
    )
    try:
        session = runtime.create_session(str(workspace))
        result = session.start("明确范围后回答")
        for _ in range(rounds - 1):
            assert result.status == "waiting"
            result = session.resume(result.run_id, "按默认范围执行")
        assert result.status == "completed" and len(goals) == rounds and len(calls) == 1
    finally:
        runtime.close()


def test_nested_workers_have_independent_effort_and_leaf_cannot_escalate_owner(
    tmp_path, monkeypatch
):
    runtime, workspace, child, seen, calls = worker_runtime(tmp_path, monkeypatch, "low")
    records = runtime.records
    root = records.list_configs("agent")[0]
    records.save("agent", {"allowed_tools": ["call_agent"]}, child["id"])
    grand = records.save(
        "agent",
        {
            "name": "grand",
            "callable": True,
            "model_config_id": child["model_config_id"],
            "instructions": "grand-private",
            "allowed_tools": [],
        },
    )

    class Planner:
        def __init__(self, model, tools):
            self.agent = model.agent_id

        def plan(self, goal):
            if self.agent != grand["id"]:
                target, tier = (
                    (child, Effort.LOW) if self.agent == root["id"] else (grand, Effort.HIGH)
                )
                return [
                    dict(
                        id="next",
                        description="委派下层",
                        effort=tier,
                        tool="call_agent",
                        args={"agent_id": target["id"], "task": "只做下层目标"},
                    )
                ]
            return [
                dict(
                    id="answer",
                    description="回答",
                    effort=Effort.MEDIUM,
                    tool="agent_respond",
                    args={"prompt": "给出答案"},
                )
            ]

    runtime._planner_factory = Planner
    try:
        result = runtime.create_session(str(workspace), approval_mode="always").start(
            "两层独立 worker"
        )
        assert result.status == "completed"
        configs = {config["agent"]["id"]: config for config in seen}
        assert configs[child["id"]]["effort_policy"]["max_output_tokens"] == 256
        assert configs[grand["id"]]["effort_policy"]["max_output_tokens"] == 2048
        assert len(calls) == 1 and calls[0][0] == grand["id"]
        assert calls[0][2] == {"max_tokens": 1024}
        assert get_effort_policy("medium").instructions in calls[0][1][0].content
        assert "grand-private" in calls[0][1][0].content
        assert "只处理委派目标" not in calls[0][1][0].content
    finally:
        runtime.close()
