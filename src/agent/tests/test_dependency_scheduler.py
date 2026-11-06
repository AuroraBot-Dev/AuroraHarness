"""验证依赖派发、失败传播和共享工作区的并发边界。"""

import threading

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from aurora.agent.core.graph import build_delegation_graph
from aurora.agent.safety.gate import build_gate
from aurora.agent.tools.base import RiskLevel, Tool


class FixedPlan:
    def __init__(self, tasks):
        self.tasks = tasks

    def plan(self, goal):
        return self.tasks


def task(key, *dependencies, tool="read"):
    return dict(
        id=key,
        description=key,
        effort="low",
        tool=tool,
        args={"key": key},
        depends_on=list(dependencies),
    )


def test_diamond_waits_for_both_concurrent_readers():
    paired = threading.Barrier(2, timeout=5)
    finished = []

    def read(key):
        if key in {"a", "b"}:
            assert finished == ["seed"]
            paired.wait()
        if key == "join":
            assert set(finished) == {"seed", "a", "b"}
        finished.append(key)
        return key

    plan = [task("join", "b", "a"), task("b", "seed"), task("seed"), task("a", "seed")]
    graph = build_delegation_graph(
        FixedPlan(plan), {"read": Tool("read", "读取", read)}, serial=True
    )
    result = graph.invoke({"goal": "准备后并行检查并汇总"})
    assert all(item["ok"] for item in result["results"])
    assert finished[0] == "seed" and finished[-1] == "join"
    assert result["report"].index("join ✓") < result["report"].index("seed ✓")


def test_failure_blocks_only_descendants_and_skips_tool_and_observer():
    calls, observed = [], []

    def read(key):
        calls.append(key)
        if key in {"input1", "input2"}:
            raise OSError(key)
        return key

    plan = [
        task("publish", "merge"),
        task("merge", "input1", "input2"),
        task("independent"),
        task("input2"),
        task("input1"),
        task("report", "independent"),
    ]
    graph = build_delegation_graph(
        FixedPlan(plan),
        {"read": Tool("read", "读取", read)},
        task_observer=lambda t: observed.append(t["id"]),
    )
    result = graph.invoke({"goal": "发布前校验"})
    outcomes = {item["task_id"]: item for item in result["results"]}
    assert outcomes["merge"]["blocked_by"] == ["input1", "input2"]
    assert outcomes["publish"]["blocked_by"] == ["merge"]
    assert outcomes["report"]["ok"] and not outcomes["input1"]["ok"]
    assert set(calls) == set(observed) == {"input1", "input2", "independent", "report"}
    assert len(outcomes) == len(result["results"]) == len(plan)


@pytest.mark.parametrize("explicit", [False, True])
def test_serial_failure_policy_preserves_legacy_plans(explicit):
    calls = []

    def read(key):
        calls.append(key)
        if key == "first":
            raise OSError("unavailable")
        return key

    plan = [task("first"), task("next")]
    if not explicit:
        for item in plan:
            del item["depends_on"]
    graph = build_delegation_graph(
        FixedPlan(plan), {"read": Tool("read", "读取", read)}, serial=True
    )
    graph.invoke({"goal": "校验"})
    if explicit:
        assert sorted(calls) == ["first", "next"]
    else:
        assert calls == ["first"]


def test_shared_writes_and_unknown_delegates_are_exclusive():
    active = set()
    paired = threading.Barrier(2, timeout=5)
    lock = threading.Lock()
    log = []

    def run(key):
        exclusive = key in {"writer", "unknown"}
        with lock:
            if exclusive:
                assert not active
            assert not ({"writer", "unknown"} & active)
            active.add(key)
            log.append(key)
        if key.startswith("safe"):
            paired.wait()
        with lock:
            active.remove(key)
        return key

    tools = {
        "call_agent": Tool("call_agent", "子角色", lambda key: key),
        "write": Tool("write", "写入", run, risk=RiskLevel.WRITE),
    }
    plan = [task(key, tool="call_agent") for key in ["safe1", "safe2", "unknown"]]
    plan.append(task("writer", tool="write"))
    graph = build_delegation_graph(
        FixedPlan(plan),
        tools,
        build_gate("always"),
        serial=True,
        delegate=lambda t: run(t["id"]),
        is_parallel_safe=lambda t: t["id"].startswith("safe"),
    )
    result = graph.invoke({"goal": "并行只读角色"})
    assert all(item["ok"] for item in result["results"])
    assert set(log[:2]) == {"safe1", "safe2"} and log[2:] == ["unknown", "writer"]


def test_full_length_chain_completes_without_default_recursion_cutoff():
    plan = [task(str(i), *([str(i - 1)] if i else [])) for i in range(256)]
    graph = build_delegation_graph(
        FixedPlan(plan[::-1]), {"read": Tool("read", "读取", lambda key: key)}, max_plan_tasks=256
    )
    result = graph.invoke({"goal": "完整依赖链"})
    assert [item["task_id"] for item in result["results"]] == [str(i) for i in range(256)]


def test_rejected_prerequisite_never_requests_dependent_approval():
    writes = []
    plan = [
        task("release", "prepare", tool="write"),
        task("prepare", tool="write"),
        task("audit", tool="write"),
    ]
    tools = {
        "write": Tool("write", "写入", lambda key: writes.append(key) or key, risk=RiskLevel.WRITE)
    }
    graph = build_delegation_graph(
        FixedPlan(plan),
        tools,
        build_gate("interrupt"),
        checkpointer=InMemorySaver(),
        task_observer=lambda task: "uuid-" + task["id"],
    )
    config = {"configurable": {"thread_id": "dependency-approval"}}
    paused = graph.invoke({"goal": "分别确认"}, config)
    (first,) = paused["__interrupt__"]
    assert first.value["taskId"] == "uuid-prepare"
    paused = graph.invoke(Command(resume={first.id: False}), config)
    (second,) = paused["__interrupt__"]
    assert second.value["taskId"] == "uuid-audit"
    assert next(item for item in paused["results"] if item["task_id"] == "release")[
        "blocked_by"
    ] == ["prepare"]
    result = graph.invoke(Command(resume={second.id: True}), config)
    assert len(result["results"]) == 3 and writes == ["audit"]
