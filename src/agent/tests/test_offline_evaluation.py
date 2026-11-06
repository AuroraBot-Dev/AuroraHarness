"""验证离线基准的独立观测、失败报告与工作区边界。"""

import json
import socket
import tempfile
from dataclasses import replace

import pytest

from aurora.agent.core.state import Effort, Task
from aurora.eval.fixtures import EvalCase, file_is, result_contains
from aurora.eval.offline import FIXTURES, run_offline


def test_builtin_suite_is_reproducible_with_network_and_tracing_disabled(monkeypatch):
    attempts = []

    def forbid_connection(*args, **kwargs):
        attempts.append(True)
        raise AssertionError("离线评估尝试了网络连接")

    monkeypatch.setattr(socket.socket, "connect", forbid_connection)
    monkeypatch.setattr(socket, "getaddrinfo", forbid_connection)
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "test-placeholder")
    monkeypatch.delenv("AGENT_API_KEY", raising=False)
    first = run_offline()
    second = run_offline()
    assert first.passed and first.passed_count == first.total == len(FIXTURES) >= 14
    assert json.dumps(first.to_dict()) == json.dumps(second.to_dict())
    assert not attempts


def test_different_actual_plans_and_expectations_independently_fail():
    baseline = next(case for case in FIXTURES if case.id == "plan_list_files")
    alternate = replace(
        baseline,
        id="wrong_expectation",
        assertions=(result_contains("list_files", "does-not-exist"),),
    )
    report = run_offline(fixtures=(baseline, alternate))
    assert not report.passed and report.passed_count == 1
    assert report.cases[0].passed
    failed = next(item for item in report.cases[1].checks if not item.passed)
    assert failed.kind == "result_contains" and failed.expected == ("does-not-exist",)
    assert "README.md" in str(failed.actual)

    class WrongPlanner:
        def plan(self, goal: str) -> list[Task]:
            return [
                Task(
                    id="list_files",
                    description=goal,
                    effort=Effort.HIGH,
                    tool="read_file",
                    args={"path": "README.md"},
                )
            ]

    report = run_offline([baseline.id], planner_factory=WrongPlanner)
    assert not report.passed
    assert {check.kind for check in report.cases[0].checks if not check.passed} == {
        "effort_is",
        "result_contains",
    }


def test_case_selection_errors_are_explicit():
    report = run_offline(["read_readme", "plan_list_files"])
    assert [case.id for case in report.cases] == ["plan_list_files", "read_readme"]
    for selected in ([], ["unlisted"], ["read_readme", "read_readme"]):
        with pytest.raises(ValueError):
            run_offline(selected)
    with pytest.raises(ValueError):
        run_offline(fixtures=(FIXTURES[0], FIXTURES[0]))


def test_failed_cases_leave_caller_and_later_workspaces_untouched(tmp_path, monkeypatch):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    monkeypatch.chdir(tmp_path)
    owner = tmp_path / "note.txt"
    owner.write_text("owner", encoding="utf-8")
    create = EvalCase(
        "create",
        "创建 note.txt 为 temporary",
        (file_is("note.txt", "temporary"),),
        allow_writes=True,
    )
    bad = replace(create, id="bad", goal="无法识别")
    check = EvalCase(
        "isolated",
        "读取 note.txt",
        (file_is("note.txt", None), result_contains("read_note", "文件不存在")),
    )
    report = run_offline(fixtures=(create, bad, check))
    assert [case.passed for case in report.cases] == [True, False, True]
    assert "无法识别" in str(report.cases[1].error)
    assert owner.read_text(encoding="utf-8") == "owner"
    assert list(scratch.iterdir()) == []


def test_default_gate_refuses_write_and_blocks_dependent_tool_but_executes_independent_read():
    (case,) = run_offline(["dependency_denied"]).cases
    statuses = {check.task: check.actual for check in case.checks if check.kind == "status_is"}
    assert statuses == {"write_note": "failed", "read_note": "blocked", "read_readme": "completed"}
    calls = next(check for check in case.checks if check.kind == "called_tasks")
    assert calls.actual == ("read_readme",)
    assert case.passed


def test_invalid_plan_never_reaches_registered_tools():
    class InvalidPlanner:
        def plan(self, goal: str) -> list[Task]:
            return [
                Task(
                    id="bad",
                    description=goal,
                    effort=Effort.HIGH,
                    tool="run_command",
                    args={"command": "exit 0"},
                )
            ]

    report = run_offline(["plan_list_files"], planner_factory=InvalidPlanner)
    assert not report.passed
    assert "PlanValidationError" in str(report.cases[0].error)
