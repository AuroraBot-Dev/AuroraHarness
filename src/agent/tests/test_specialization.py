"""验证能力作用域、原子回退、权限和运行快照。"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select
from test_agent_registry import setup_registry
from test_persistence import runtime_at, visual_session

from aurora.agent.capability import (
    Capability,
    CapabilityContext,
    CapabilityPolicy,
    CapabilityRegistry,
    CapabilityScope,
)
from aurora.agent.core import Effort
from aurora.agent.store.model import AgentRun
from aurora.agent.tools.base import RiskLevel, Tool


def policy(name, **kwargs):
    """创建可区分来源的上下文策略。"""
    return CapabilityPolicy(name, lambda context: Capability(context=name), **kwargs)


@pytest.mark.parametrize(
    "kind,path,expected",
    [
        ("global", (), [True, True, True, True, True]),
        ("project", (), [True, True, True, True, False]),
        ("subtree", ("parent",), [False, True, True, False, False]),
        ("task", ("parent",), [False, True, False, False, False]),
        ("task", (), [True, False, False, False, False]),
    ],
)
def test_scope_matches_complete_projects_and_task_paths(tmp_path, kind, path, expected):
    project = tmp_path / "project"
    scope = CapabilityScope(kind, None if kind == "global" else project, path)
    contexts = [
        CapabilityContext(project, "goal", task_path=task_path)
        for task_path in ((), ("parent",), ("parent", "child"), ("parent-other",))
    ]
    contexts.append(CapabilityContext(tmp_path / "project-other", "goal", task_path=path))
    assert [scope.matches(context) for context in contexts] == expected


@pytest.mark.parametrize(
    "kwargs",
    [
        {"kind": "unknown"},
        {"kind": "project"},
        {"kind": "global", "project_path": "."},
        {"kind": "project", "project_path": ".", "task_path": ("task",)},
        {"kind": "subtree", "project_path": "."},
        {"kind": "task", "project_path": ".", "task_path": ("",)},
    ],
)
def test_scope_rejects_ambiguous_configuration(kwargs):
    with pytest.raises(ValueError):
        CapabilityScope(**kwargs)


def test_policy_order_is_stable_and_run_registration_is_frozen(tmp_path):
    registry = CapabilityRegistry()
    registry.register(policy("last", priority=10))
    registry.register(policy("project", scope=CapabilityScope("project", tmp_path)))
    registry.register(policy("z-global"))
    registry.register(policy("a-global"))
    frozen = registry.freeze()
    registry.unregister("a-global")
    registry.register(policy("new"))
    result = frozen.resolve(CapabilityContext(tmp_path, "goal"), instructions="角色指令")
    assert result.applied == ("a-global", "z-global", "project", "last")
    assert result.instructions == "角色指令"
    assert result.context.index("a-global") < result.context.index("project")
    assert "new" in registry.freeze().resolve(CapabilityContext(tmp_path, "goal")).applied
    with pytest.raises(ValueError, match="重复"):
        registry.register(policy("new"))


def test_failed_policy_is_atomic_and_does_not_leak_exception(tmp_path, caplog):
    registry = CapabilityRegistry()

    def failing_retriever(context):
        raise RuntimeError("secret-must-not-be-logged")

    registry.register(
        CapabilityPolicy(
            "broken",
            lambda context: Capability(context="不应注入", retrieve=failing_retriever),
        )
    )
    registry.register(policy("healthy"))
    registry.register(
        CapabilityPolicy(
            "collision",
            lambda context: Capability(
                instructions="不应注入",
                tools=(Tool("builtin", "替换内置", lambda: "bad"),),
            ),
        )
    )
    registry.register(CapabilityPolicy("invalid", lambda context: None))
    original = Tool("builtin", "内置", lambda: "original")
    result = registry.freeze().resolve(
        CapabilityContext(tmp_path, "goal"), tools={"builtin": original}
    )
    assert result.applied == ("healthy",)
    assert set(result.failed) == {"broken", "collision", "invalid"}
    assert "不应注入" not in result.context + result.instructions
    assert result.tools["builtin"] is original
    assert "secret-must-not-be-logged" not in caplog.text


def test_retrieval_tools_and_permission_intersection(tmp_path):
    registry = CapabilityRegistry()
    calls = []
    tool = Tool("project.search", "项目检索", lambda query: query, RiskLevel.EXECUTE)

    def retrieve(context):
        calls.append(context.goal)
        return "检索结果仅作参考"

    registry.register(
        CapabilityPolicy(
            "project",
            lambda context: Capability("团队背景", "输出简洁", (tool,), retrieve),
            CapabilityScope("project", tmp_path),
        )
    )
    frozen = registry.freeze()
    empty = frozen.resolve(CapabilityContext(tmp_path / "other", "unmatched"))
    assert not empty.applied and not calls
    result = frozen.resolve(CapabilityContext(tmp_path, "search"), allowed_tools=())
    assert calls == ["search"]
    assert "团队背景" in result.context and "检索结果仅作参考" in result.context
    assert "输出简洁" in result.instructions
    assert "检索结果" not in result.instructions
    assert not result.tools
    allowed = frozen.resolve(CapabilityContext(tmp_path, "search"), allowed_tools=(tool.name,))
    assert allowed.tools[tool.name].risk == RiskLevel.EXECUTE
    tool.params_schema["properties"].clear()
    assert "query" in allowed.tools[tool.name].params_schema["properties"]


@pytest.mark.parametrize("name", ["call_agent", "list_agents", "agent_respond"])
def test_policy_cannot_inject_delegation_or_internal_model_tools(tmp_path, name):
    registry = CapabilityRegistry()
    registry.register(
        CapabilityPolicy("unsafe", lambda context: Capability(tools=(Tool(name, "", lambda: ""),)))
    )
    result = registry.freeze().resolve(CapabilityContext(tmp_path, "goal"))
    assert result.failed == ("unsafe",)
    assert not result.tools


def test_budget_falls_back_without_discarding_other_policies(tmp_path):
    registry = CapabilityRegistry()
    registry.register(CapabilityPolicy("oversized", lambda context: Capability(context="中" * 100)))
    registry.register(policy("small"))
    result = registry.freeze().resolve(CapabilityContext(tmp_path, "goal"), max_bytes=100)
    assert result.failed == ("oversized",)
    assert result.applied == ("small",)


class SpecializedPlanner:
    """记录规划输入并调用一个特化工具。"""

    def __init__(self, tools, seen):
        self.tools = tools
        self.seen = seen

    def set_instructions(self, value):
        self.seen.append(value)

    def plan(self, goal):
        self.seen.append(goal)
        return [
            dict(
                id="custom",
                description="项目检查",
                effort=Effort.LOW,
                tool="project.check",
                args={},
            )
        ]


def test_runtime_keeps_resolved_capabilities_through_approval_and_audits_inputs(tmp_path):
    runtime, workspace, _, _ = runtime_at(tmp_path)
    calls, seen, executed = [], [], []

    def apply(context):
        calls.append(context)
        return Capability(
            context="项目规范",
            instructions="角色补充",
            tools=(
                Tool(
                    "project.check",
                    "执行检查",
                    lambda: executed.append("run") or "OK",
                    RiskLevel.EXECUTE,
                ),
            ),
        )

    runtime.capabilities.register(
        CapabilityPolicy("project", apply, CapabilityScope("project", workspace))
    )
    runtime._planner_factory = lambda model, tools: SpecializedPlanner(tools, seen)
    try:
        session = runtime.create_session(str(workspace), approval_mode="interactive")
        result = session.start("检查项目")
        assert result.status == "waiting"
        assert not executed and len(calls) == 1
        runtime.capabilities.unregister("project")
        result = session.resume(result.run_id, {"approved": True})
        assert result.status == "completed"
        assert executed == ["run"] and len(calls) == 1
        assert any("项目规范" in value for value in seen)
        assert any("角色补充" in value for value in seen)
        row = runtime.records.db.first(select(AgentRun).where(AgentRun.run_id == result.run_id))
        assert row is not None
        inputs = json.loads(row["input_json"])
        assert inputs["capabilities"] == {"applied": ["project"], "failed": []}
        assert "项目规范" in inputs["current"]
        assert "角色补充" in json.loads(row["config_snapshot_json"])["agent"]["instructions"]
        assert session.start("下一轮", mode="say").status == "completed"
        assert len(calls) == 1
    finally:
        runtime.close()


def test_runtime_does_not_grant_tools_outside_role_allowlist(tmp_path):
    runtime, workspace, _, _ = runtime_at(tmp_path)
    executed = []
    runtime.capabilities.register(
        CapabilityPolicy(
            "custom",
            lambda context: Capability(
                tools=(Tool("project.check", "", lambda: executed.append(True) or "bad"),)
            ),
        )
    )
    agent = runtime.records.list_configs("agent")[0]
    runtime.records.save("agent", {"allowed_tools": []}, agent["id"])
    runtime._planner_factory = lambda model, tools: SpecializedPlanner(tools, [])
    try:
        session = runtime.create_session(str(workspace), approval_mode="always")
        with pytest.raises(RuntimeError) as failure:
            session.start("尝试越权")
        assert failure.value.code == "TOOL_NOT_ALLOWED"
        assert not executed
    finally:
        runtime.close()


def test_runtime_oversized_policy_falls_back_with_larger_model_budget(tmp_path):
    runtime, workspace, _, calls = runtime_at(tmp_path)
    model = runtime.records.list_configs("model")[0]
    runtime.records.save("model", {"context_budget_tokens": 40_000}, model["id"])
    runtime.capabilities.register(
        CapabilityPolicy("oversized", lambda context: Capability(context="x" * 17_000))
    )
    try:
        session = runtime.create_session(str(workspace), approval_mode="always")
        result = session.start("保持当前需求", mode="say")
        assert result.status == "completed"
        assert "保持当前需求" in str(calls[-1])
        assert "x" * 100 not in str(calls[-1])
        row = runtime.records.db.first(select(AgentRun).where(AgentRun.run_id == result.run_id))
        assert row is not None
        assert json.loads(row["input_json"])["capabilities"] == {
            "applied": [],
            "failed": ["oversized"],
        }
    finally:
        runtime.close()


def test_delegated_task_inherits_project_and_exact_task_capabilities(tmp_path, monkeypatch):
    runtime, workspace, _, seen = setup_registry(tmp_path, monkeypatch)
    calls = []

    def scoped(context):
        calls.append(context.task_path)
        return Capability(context="专家任务背景", instructions="专家任务补充")

    runtime.capabilities.register(policy("project", scope=CapabilityScope("project", workspace)))
    runtime.capabilities.register(
        CapabilityPolicy("task", scoped, CapabilityScope("task", workspace, ("parent",)))
    )
    try:
        session = runtime.create_session(str(workspace), approval_mode="interactive")
        result = session.start("委派专家")
        runtime.capabilities.unregister("task")
        while result.status == "waiting":
            result = session.resume(result.run_id, {"approved": True})
        assert result.status == "completed"
        assert calls == [("parent",)]
        assert sum("专家任务补充" in value for value in seen) == 1
        rows = runtime.records.db.rows(select(AgentRun).where(AgentRun.run_id == result.run_id))
        parent = next(row for row in rows if not row["parent_task_id"])
        child = next(row for row in rows if row["parent_task_id"])
        assert json.loads(parent["input_json"])["capabilities"]["applied"] == ["project"]
        assert json.loads(child["input_json"])["capabilities"]["applied"] == ["project", "task"]
        assert "专家任务背景" in json.loads(child["input_json"])["current"]
    finally:
        runtime.close()


def test_review_and_summary_receive_context_without_executing_injected_tools(tmp_path):
    runtime, workspace, _, calls = runtime_at(tmp_path, ["passed"])
    executed = []
    runtime.capabilities.register(
        CapabilityPolicy(
            "team",
            lambda context: Capability(
                context="团队规范",
                tools=(Tool("project.check", "", lambda: executed.append(True) or "bad"),),
            ),
        )
    )
    try:
        session = visual_session(runtime, workspace)
        assert session.start("生成页面").status == "completed"
        assert not executed
        assert calls and all("团队规范" in str(messages) for messages in calls)
    finally:
        runtime.close()
