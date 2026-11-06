"""验证计划入口与局部重试共同遵守同一角色的依赖关系。"""

from types import SimpleNamespace
from typing import cast

import pytest
from langchain_core.runnables import RunnableLambda
from test_persistence import runtime_at
from test_retries import history

from aurora.agent.core import (
    LLMPlanner,
    PlanValidationError,
    build_delegation_graph,
    invoke_with_responder,
    validate_plan,
)
from aurora.agent.core.planner import Planner
from aurora.agent.orchestration.runtime import RunControlError
from aurora.agent.tools.base import Tool


def record(key, **fields):
    """构造可观察的无副作用工具节点。"""
    return (
        dict(id=key, description=f"记录 {key}", effort="low", tool="record", args={"key": key})
        | fields
    )


def model_plan(tasks, tools):
    """从真实结构化规划器入口返回固定计划。"""
    model = SimpleNamespace(
        with_structured_output=lambda schema, **kw: RunnableLambda(
            lambda messages: {"parsed": schema.model_validate({"tasks": tasks})}
        )
    )
    return LLMPlanner(model, tools).plan("记录输入")


def test_direct_and_model_plans_preserve_forward_references_and_explicit_independence():
    tasks = [record("end", depends_on=[2, "side"]), record("side", depends_on=[]), record(2)]
    tools = {"record": Tool("record", "", lambda key: key)}
    for validated in (validate_plan(tasks, tools), model_plan(tasks, tools)):
        assert [task["id"] for task in validated] == ["end", "side", "2"]
        assert validated[0].get("depends_on") == ["2", "side"]
        assert validated[1].get("depends_on") == []
        assert "depends_on" not in validated[2]
    assert tasks[0]["depends_on"] == [2, "side"]


@pytest.mark.parametrize(
    "references,code",
    [
        (["missing"], "INVALID_TASK_DEPENDENCY"),
        (["bad"], "INVALID_TASK_DEPENDENCY"),
        (["start", "start"], "INVALID_TASK_DEPENDENCY"),
        (["end"], "CYCLIC_TASK_DEPENDENCY"),
    ],
)
def test_invalid_dependencies_fail_both_entry_points_before_tools(references, code):
    seen = []
    tools = {"record": Tool("record", "", lambda key: seen.append(key) or str(key))}
    tasks = [
        record("start"),
        record("bad", depends_on=references),
        record("end", depends_on=["bad"]),
    ]
    graph = build_delegation_graph(cast(Planner, SimpleNamespace(plan=lambda goal: tasks)), tools)
    with pytest.raises(PlanValidationError) as direct:
        invoke_with_responder(graph, "测试", lambda request: pytest.fail("无效计划进入人工审批"))
    with pytest.raises(PlanValidationError) as model:
        model_plan(tasks, tools)
    assert direct.value.code == model.value.code == code and not seen


@pytest.mark.parametrize(
    "invalid", [None, "start", ("start",), [True], [None], [["start"]], [" start"]]
)
def test_malformed_dependency_declarations_are_rejected(invalid):
    tasks = [record("start"), record("bad", depends_on=invalid)]
    tools = {"record": Tool("record", "", lambda key: key)}
    with pytest.raises(PlanValidationError, match="前置"):
        validate_plan(tasks, tools)


def work_plan():
    """让发布任务依赖一个会失败的生成步骤，同时保留独立分支。"""

    def code(key, body, dependencies):
        return dict(
            id=key,
            description=key,
            effort="low",
            tool="run_python",
            args={"code": "from pathlib import Path\n" + body},
            depends_on=dependencies,
        )

    return [
        code("publish", "assert Path('package').exists()\nPath('published').touch()", ["package"]),
        code("package", "assert Path('built').exists()\nPath('package').touch()", ["build"]),
        code("build", "assert Path('fixed').exists()\nPath('built').touch()", ["seed"]),
        code("seed", "with Path('counter').open('a', encoding='utf-8') as f: f.write('x')", []),
        code("unrelated", "assert Path('other-fix').exists()\nPath('unrelated').touch()", []),
    ]


@pytest.mark.parametrize("scope", ["whole", "blocked", "chain"])
def test_retry_runs_only_required_unfinished_predecessors(tmp_path, scope):
    runtime, workspace, _, _ = runtime_at(tmp_path)
    runtime._planner_factory = lambda llm, tools: SimpleNamespace(plan=lambda goal: work_plan())
    session = runtime.create_session(str(workspace), approval_mode="always")
    try:
        with pytest.raises(RuntimeError):
            session.start("构建项目")
        (row,) = runtime.records.db.all("SELECT id FROM runs")
        source_id = row["id"]
        original = history(runtime, source_id)
        tasks = {task["taskKey"]: task for task in original["tasks"]}
        assert tasks["publish"]["status"] == tasks["package"]["status"] == "blocked"
        assert tasks["seed"]["status"] == "completed"
        target = tasks["publish"]["id"] if scope != "whole" else None
        if scope == "chain":
            with pytest.raises(RuntimeError):
                session.retry(source_id, target)
            (row,) = runtime.records.db.all(
                "SELECT id FROM runs WHERE retry_of_run_id=?", (source_id,)
            )
            source_id = row["id"]
            intermediate = history(runtime, source_id)
            tasks = {task["taskKey"]: task for task in intermediate["tasks"]}
            assert set(tasks) == {"build", "package", "publish"}
            target = tasks["publish"]["id"]
        (workspace / "fixed").touch()
        if scope == "whole":
            (workspace / "other-fix").touch()
        resumed = session.retry(source_id, target)
        assert resumed.status == "completed" and resumed.retry_task_id == target
        retried = history(runtime, resumed.run_id)
        current = {task["taskKey"]: task for task in retried["tasks"]}
        keys = {"build", "package", "publish"} | ({"unrelated"} if scope == "whole" else set())
        assert current.keys() == keys
        assert all(
            task["status"] == "completed" and task["retryOfTaskId"] == tasks[key]["id"]
            for key, task in current.items()
        )
        saved = runtime.records.get("runs", resumed.run_id)["config_snapshot"]["retry"]["plan"]
        assert {task["id"]: task["depends_on"] for task in saved["tasks"]} == {
            key: dependencies
            for key, dependencies in [
                ("build", []),
                ("package", ["build"]),
                ("publish", ["package"]),
                ("unrelated", []),
            ]
            if key in keys
        }
        assert (workspace / "counter").read_text(encoding="utf-8") == "x"
        assert (workspace / "published").exists()
        assert (workspace / "unrelated").exists() == (scope == "whole")
        assert history(runtime, original["id"]) == original
    finally:
        runtime.close()


def test_retry_rejects_inconsistent_persisted_dependency_before_allocating_run(tmp_path):
    runtime, workspace, _, _ = runtime_at(tmp_path)
    runtime._planner_factory = lambda llm, tools: SimpleNamespace(plan=lambda goal: work_plan())
    session = runtime.create_session(str(workspace), approval_mode="always")
    try:
        with pytest.raises(RuntimeError):
            session.start("构建项目")
        (run,) = runtime.records.db.all("SELECT id FROM runs")
        (task,) = runtime.records.db.all("SELECT id FROM tasks WHERE task_key='publish'")
        runtime.records.update("tasks", task["id"], depends_on_json='["missing"]')
        with pytest.raises(RunControlError) as failure:
            session.retry(run["id"], task["id"])
        assert failure.value.code == "RUN_NOT_RETRYABLE"
        assert len(runtime.records.db.all("SELECT id FROM runs")) == 1
        assert not (workspace / "published").exists()
    finally:
        runtime.close()


def test_retrying_blocked_child_keeps_role_scope_and_rechecks_predecessor_permissions(
    tmp_path, monkeypatch
):
    from test_recursive_delegation import tree_runtime

    runtime, workspace, agents, _, _ = tree_runtime(tmp_path, monkeypatch, levels=1)
    owner = agents[-1]["id"]

    def child_plan(goal):
        tasks = work_plan()
        tasks[0].update(tool="write_file", args={"path": "published", "content": "ok"})
        return tasks

    def root_plan(goal):
        return [
            dict(
                id="delegate",
                description="委派完整构建计划",
                effort="medium",
                tool="call_agent",
                args={"agent_id": owner, "task": goal},
            )
        ]

    runtime._planner_factory = lambda llm, tools: SimpleNamespace(
        plan=child_plan if llm.agent_id == owner else root_plan
    )
    session = runtime.create_session(str(workspace), approval_mode="always")
    try:
        with pytest.raises(RuntimeError):
            session.start("委派构建")
        (row,) = runtime.records.db.all("SELECT id FROM runs")
        original = history(runtime, row["id"])
        target = next(t for t in original["tasks"] if t["taskKey"] == "publish")
        assert target["status"] == "blocked" and target["parentId"]
        (workspace / "fixed").touch()
        runtime.records.save("agent", {"allowed_tools": ["write_file"]}, owner)
        with pytest.raises(RuntimeError) as failure:
            session.retry(row["id"], target["id"])
        assert getattr(failure.value, "code", None) == "TOOL_NOT_ALLOWED"
        assert not (workspace / "published").exists() and not (workspace / "built").exists()
        runtime.records.save("agent", {"allowed_tools": ["write_file", "run_python"]}, owner)
        current = session.retry(row["id"], target["id"])
        assert current.status == "completed"
        fresh = history(runtime, current.run_id)
        (execution,) = fresh["agentRuns"]
        assert execution["agentId"] == owner and execution["input"]["retry"]["role_path"] == [
            "delegate"
        ]
        assert set(t["taskKey"] for t in fresh["tasks"]) == {"build", "package", "publish"}
        assert "root-private" not in execution["input"]["current"]
        assert (workspace / "counter").read_text(encoding="utf-8") == "x"
        assert history(runtime, row["id"]) == original
    finally:
        runtime.close()
