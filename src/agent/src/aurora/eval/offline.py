"""在临时工作区验证确定性规划、实际文件工具与确认门。"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from contextvars import Context
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from typing import Any

from langsmith import tracing_context

from aurora.agent.core.graph import build_delegation_graph
from aurora.agent.core.planner import Planner, classify_effort
from aurora.agent.core.state import DelegationState, Task
from aurora.agent.safety.gate import build_gate
from aurora.agent.sandbox import Sandbox
from aurora.agent.tools.sandbox_tools import build_sandbox_tools

from .fixtures import FIXTURES, EvalCase, Expectation


class OfflinePlanner:
    """识别基准中的目录、读取、对比和写入指令。"""

    def plan(self, goal: str) -> list[Task]:
        tasks: list[Task] = []
        writers: dict[str, str] = {}
        counts: dict[str, int] = {}

        def add(verb: str, path: str, clause: str, content: str | None = None) -> None:
            normalized = PurePosixPath(path)
            key = normalized.as_posix()
            stem = (
                normalized.with_suffix("").as_posix().lower().replace("/", "_")
                if path != "."
                else "files"
            )
            base = f"{verb}_{stem}"
            counts[base] = counts.get(base, 0) + 1
            task_id = base if counts[base] == 1 else f"{base}_{counts[base]}"
            deps = [writers[key]] if key in writers else []
            args: dict[str, object] = {"path": path}
            if verb == "list":
                deps = [
                    key
                    for name, key in writers.items()
                    if PurePosixPath(name).is_relative_to(normalized)
                ]
            if content is not None:
                args["content"] = content
                writers[key] = task_id
            tasks.append(
                Task(
                    id=task_id,
                    description=clause,
                    effort=classify_effort(clause),
                    tool={"list": "list_files", "read": "read_file", "write": "write_file"}[verb],
                    args=args,
                    depends_on=deps,
                )
            )

        for clause in re.split(r"\s*[；;]\s*", goal.strip()):
            if match := re.fullmatch(r"列出\s*(?:(\S+)\s+)?目录", clause):
                add("list", match[1] or ".", clause)
            elif match := re.fullmatch(r"(?:读取|查看|分析)\s+(\S+)", clause):
                add("read", match[1], clause)
            elif match := re.fullmatch(r"对比\s+(\S+)\s+与\s+(\S+)", clause):
                for path in match.groups():
                    add("read", path, clause)
            elif match := re.fullmatch(r"(?:创建|修改)\s+(\S+)\s+为\s+(.+)", clause, re.DOTALL):
                add("write", match[1], clause, match[2])
            else:
                raise ValueError("离线规划器无法识别指令")
        return tasks


@dataclass(frozen=True)
class CheckResult:
    """保存断言的期望值与实际观测值。"""

    kind: str
    task: str
    expected: object
    actual: object
    passed: bool


@dataclass(frozen=True)
class CaseResult:
    """保存单个用例的检查结果。"""

    id: str
    checks: tuple[CheckResult, ...]
    error: str | None = None

    @property
    def passed(self) -> bool:
        return (
            self.error is None and bool(self.checks) and all(check.passed for check in self.checks)
        )


@dataclass(frozen=True)
class EvalReport:
    """汇总离线基准，通过数只包含全部断言成立的用例。"""

    cases: tuple[CaseResult, ...]

    @property
    def total(self) -> int:
        return len(self.cases)

    @property
    def passed_count(self) -> int:
        return sum(case.passed for case in self.cases)

    @property
    def passed(self) -> bool:
        return bool(self.cases) and self.passed_count == self.total

    def to_dict(self) -> dict[str, Any]:
        """提供供 CLI 与 CI 消费的结构化报告。"""
        return {
            "mode": "offline",
            "passed": self.passed,
            "passed_count": self.passed_count,
            "total": self.total,
            "cases": [
                {
                    "id": case.id,
                    "passed": case.passed,
                    "error": case.error,
                    "checks": [asdict(check) for check in case.checks],
                }
                for case in self.cases
            ],
        }


def _check(spec: Expectation, tasks, results, calls, sandbox: Sandbox) -> CheckResult:
    """从执行事实而非计划输入中取得断言观测值。"""
    expected = spec.expected
    contains = False
    if spec.kind == "tasks_include":
        actual = list(tasks)
        contains = True
    elif spec.kind == "effort_is":
        task = tasks.get(spec.task)
        actual = str(task["effort"]) if task else None
    elif spec.kind == "result_contains":
        result = results.get(spec.task)
        actual = result["output"].replace(str(sandbox.root), "<workspace>") if result else None
        contains = True
    elif spec.kind == "called_tasks":
        actual = tuple(sorted(calls))
    elif spec.kind == "file_is":
        target = sandbox.resolve(spec.task)
        actual = sandbox.read_file(spec.task) if target.exists() else None
    elif spec.kind == "status_is":
        result = results.get(spec.task)
        actual = None
        if result is not None:
            actual = (
                "blocked" if result.get("blocked_by") else "completed" if result["ok"] else "failed"
            )
    else:
        raise ValueError(f"未知离线断言 {spec.kind}")
    if contains:
        if not isinstance(expected, tuple) or not expected:
            raise ValueError(f"{spec.kind} 需要至少一个期望值")
        passed = actual is not None and all(item in actual for item in expected)
    else:
        passed = actual == expected
    return CheckResult(spec.kind, spec.task, expected, actual, passed)


def _run_case(case: EvalCase, planner_factory: Callable[[], Planner]) -> CaseResult:
    with TemporaryDirectory(prefix="aurora-eval-") as temporary, tracing_context(enabled=False):
        try:
            sandbox = Sandbox(Path(temporary))
            for path, content in case.files:
                sandbox.write_file(path, content)
            available = build_sandbox_tools(sandbox)
            tools = {name: available[name] for name in ("list_files", "read_file", "write_file")}
            calls = []

            def invoke(task, tool):
                calls.append(task["id"])
                return tool.run(**task["args"])

            graph = build_delegation_graph(
                planner_factory(),
                tools,
                build_gate("always" if case.allow_writes else "never"),
                tool_runner=invoke,
            )
            initial = DelegationState(goal=case.goal, tasks=[], results=[], trace=[], report="")
            state = graph.invoke(initial)
            tasks = {task["id"]: task for task in state["tasks"]}
            results = {result["task_id"]: result for result in state["results"]}
            explicit = {spec.task for spec in case.assertions if spec.kind == "status_is"}
            checks = list(case.assertions)
            checks.extend(
                Expectation("status_is", key, "completed") for key in tasks if key not in explicit
            )
            return CaseResult(
                case.id, tuple(_check(spec, tasks, results, calls, sandbox) for spec in checks)
            )
        except Exception as error:
            return CaseResult(
                case.id, (), f"{type(error).__name__}: {error}".replace(temporary, "<workspace>")
            )


def run_offline(
    case_ids: Sequence[str] | None = None,
    *,
    fixtures: Sequence[EvalCase] = FIXTURES,
    planner_factory: Callable[[], Planner] = OfflinePlanner,
) -> EvalReport:
    """顺序执行选定基准，每个用例获得全新工作区和规划器。"""
    identifiers = [case.id for case in fixtures]
    if not identifiers or len(set(identifiers)) != len(identifiers) or not all(identifiers):
        raise ValueError("离线用例名称不能为空或重复")
    if isinstance(case_ids, str):
        raise ValueError("case_ids 应为用例名称列表")
    selected = set(identifiers if case_ids is None else case_ids)
    if not selected or (case_ids is not None and len(case_ids) != len(selected)):
        raise ValueError("请选择至少一个不重复的离线用例")
    if unknown := selected.difference(identifiers):
        raise ValueError("未知离线用例：" + "、".join(sorted(unknown)))
    cases = []
    for case in fixtures:
        if case.id not in selected:
            continue
        if not case.assertions or type(case.allow_writes) is not bool:
            raise ValueError(f"离线用例配置无效：{case.id}")
        cases.append(Context().run(_run_case, case, planner_factory))
    return EvalReport(tuple(cases))
