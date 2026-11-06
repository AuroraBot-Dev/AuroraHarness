"""验证计划在派发前整体拒绝、JSON Schema 与模型解析边界。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda
from test_persistence import runtime_at

from aurora.agent.core import (
    Effort,
    ExecutionLimitError,
    ExecutionLimits,
    LLMPlanner,
    PlanValidationError,
    TaskBudget,
    build_delegation_graph,
    validate_plan,
)
from aurora.agent.mcp import build_mcp_tools
from aurora.agent.tools.base import Tool
from aurora.agent.transport import RuntimeApi


def task(**changes):
    """创建可观察工具的基准任务。"""
    return (
        dict(
            id="one", description="记录任务", effort=Effort.LOW, tool="record", args={"value": "ok"}
        )
        | changes
    )


@pytest.mark.parametrize(
    "change,code",
    [
        ({"id": None}, "INVALID_TASK_ID"),
        ({"id": True}, "INVALID_TASK_ID"),
        ({"id": "  "}, "INVALID_TASK_ID"),
        ({"id": " a"}, "INVALID_TASK_ID"),
        ({"id": "a\nb"}, "INVALID_TASK_ID"),
        ({"id": "a" * 129}, "INVALID_TASK_ID"),
        ({"description": " "}, "INVALID_TASK"),
        ({"effort": "maximum"}, "INVALID_EFFORT"),
        ({"tool": "not_allowed"}, "TOOL_NOT_ALLOWED"),
        ({"args": None}, "INVALID_TOOL_ARGUMENTS"),
        ({"args": {}}, "INVALID_TOOL_ARGUMENTS"),
        ({"args": {"value": "ok", "extra": "secret-input"}}, "INVALID_TOOL_ARGUMENTS"),
        ({"args": {"value": float("nan")}}, "INVALID_PLAN"),
        ({"unknown_field": ["first"]}, "INVALID_TASK"),
    ],
)
def test_entire_plan_is_validated_before_any_tool_or_approval(change, code):
    received = []
    tools = {"record": Tool("record", "记录", lambda value: received.append(value) or "ok")}
    planner = SimpleNamespace(plan=lambda goal: [task(id="first"), task(**change)])
    graph = build_delegation_graph(planner, tools)
    with pytest.raises(PlanValidationError) as failure:
        graph.invoke({"goal": "验证"})
    assert failure.value.code == code and received == []
    assert "secret-input" not in str(failure.value)


@pytest.mark.parametrize(
    "value,code",
    [
        ([], "EMPTY_PLAN"),
        ({"tasks": []}, "INVALID_PLAN"),
        ([task(), task()], "DUPLICATE_TASK_ID"),
        ([task(id=1), task(id="1")], "DUPLICATE_TASK_ID"),
        ([task(id=str(index)) for index in range(9)], "PLAN_TASK_LIMIT"),
    ],
)
def test_plan_identity_shape_and_fanout(value, code):
    with pytest.raises(PlanValidationError) as failure:
        validate_plan(value, {"record": Tool("record", "", lambda value: value)})
    assert failure.value.code == code


def test_schema_checks_nested_arguments_local_references_and_rejects_external_fetch():
    schema = {
        "type": "object",
        "properties": {"item": {"$ref": "#/$defs/item"}},
        "required": ["item"],
        "additionalProperties": False,
        "$defs": {
            "item": {
                "type": "object",
                "properties": {"count": {"type": "integer", "minimum": 1}},
                "required": ["count"],
                "additionalProperties": False,
            }
        },
    }
    tools = {"record": Tool("record", "", lambda item: "ok", params_schema=schema)}
    valid = [task(args={"item": {"count": 1}})]
    assert validate_plan(valid, tools)[0]["args"] == valid[0]["args"]
    for value in [True, "1", 0]:
        with pytest.raises(PlanValidationError, match="约束"):
            validate_plan([task(args={"item": {"count": value}})], tools)
    tools["record"].params_schema = {"$ref": "https://127.0.0.1:1/must-not-fetch"}
    with pytest.raises(PlanValidationError) as failure:
        validate_plan(valid, tools)
    assert failure.value.code == "INVALID_TOOL_SCHEMA"


def test_validation_copies_the_plan_and_keeps_explicit_empty_retry_supported():
    tools = {"record": Tool("record", "", lambda value: value)}
    value = [task(id=1, args={"value": {"nested": [1]}})]
    result = validate_plan(value, tools)
    result[0]["args"]["value"]["nested"].append(2)
    assert value[0]["args"]["value"]["nested"] == [1]
    assert result[0]["id"] == "1"
    assert validate_plan([], tools, allow_empty=True) == []


@pytest.mark.parametrize(
    "parsed", [None, {"tasks": []}, {"tasks": [task(id=None)]}, {"tasks": [task(unknown=True)]}]
)
def test_llm_parser_returns_a_stable_error_without_raw_response_leak(parsed):
    class Model:
        """提供结构化响应边界的确定性模型。"""

        def with_structured_output(self, schema, **kwargs):
            return RunnableLambda(
                lambda messages: {
                    "parsed": parsed,
                    "raw": AIMessage(content="private-raw-response"),
                    "parsing_error": ValueError("private-parser-error"),
                }
            )

    planner = LLMPlanner(Model(), {"record": Tool("record", "", lambda value: value)})
    with pytest.raises(PlanValidationError) as failure:
        planner.plan("检查")
    assert failure.value.code == "INVALID_PLANNER_RESPONSE"
    assert "private" not in str(failure.value)


def test_runtime_persists_failed_plan_and_returns_its_error_code_without_writes(tmp_path):
    runtime, workspace, _, _ = runtime_at(tmp_path)
    runtime._planner_factory = lambda llm, tools: SimpleNamespace(
        plan=lambda goal: [
            dict(
                id="first",
                description="不应写入",
                effort=Effort.LOW,
                tool="write_file",
                args={"path": "unexpected", "content": "bad"},
            ),
            dict(id="first", description="冲突", effort=Effort.LOW, tool="list_files", args={}),
        ]
    )
    try:
        session = runtime.create_session(str(workspace), approval_mode="always")
        frames = RuntimeApi(runtime).handle(
            {
                "id": "run",
                "method": "run.start",
                "params": {"sessionId": session.id, "goal": "验证"},
            }
        )
        assert frames[0]["error"]["code"] == "DUPLICATE_TASK_ID"
        assert runtime.records.db.one("SELECT status FROM runs")["status"] == "failed"
        assert not runtime.records.db.all("SELECT id FROM tasks")
        assert not runtime.records.db.all("SELECT id FROM tool_calls")
        assert not (workspace / "unexpected").exists()
    finally:
        runtime.close()


def test_shared_task_budget_is_atomic_and_replanning_replaces_reservation():
    budget = TaskBudget(ExecutionLimits(max_total_tasks=3))
    budget.reserve("root", [1, 2])
    budget.reserve("root", [1])
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(budget.reserve, key, [1, 2]) for key in ("a", "b")]
    failures = [future.exception() for future in futures]
    assert sum(isinstance(error, ExecutionLimitError) for error in failures) == 1
    budget.reserve("a" if futures[0].exception() is None else "b", [1, 2])


def test_mcp_binding_cannot_change_target_through_model_arguments():
    received = []
    client = SimpleNamespace(
        config=SimpleNamespace(name="test"),
        list_tools=lambda: [
            SimpleNamespace(
                name="safe",
                description="",
                title="",
                input_schema={"type": "object", "properties": {}},
                annotations=None,
            )
        ],
        call_tool=lambda name, args: received.append((name, args)) or "ok",
    )
    tools = build_mcp_tools(client)
    assert tools["mcp.test.safe"].params_schema == {"type": "object", "properties": {}}
    tools["mcp.test.safe"].run(_remote_name="privileged")
    assert received == [("safe", {"_remote_name": "privileged"})]
