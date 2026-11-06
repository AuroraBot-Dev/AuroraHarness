"""离线评估的任务、初始文件和验收条件。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

ExpectationKind = Literal[
    "tasks_include", "effort_is", "result_contains", "status_is", "file_is", "called_tasks"
]
Expected = str | tuple[str, ...] | None


@dataclass(frozen=True)
class Expectation:
    """声明一项可在实际执行之后检查的事实。"""

    kind: ExpectationKind
    task: str
    expected: Expected


def tasks_include(*ids: str) -> Expectation:
    return Expectation("tasks_include", "", ids)


def effort_is(task_id: str, level: str) -> Expectation:
    return Expectation("effort_is", task_id, level)


def result_contains(task_id: str, *strings: str) -> Expectation:
    return Expectation("result_contains", task_id, strings)


def status_is(task_id: str, status: str) -> Expectation:
    return Expectation("status_is", task_id, status)


def file_is(path: str, content: str | None) -> Expectation:
    return Expectation("file_is", path, content)


def called_tasks(*ids: str) -> Expectation:
    return Expectation("called_tasks", "", tuple(sorted(ids)))


SEED_FILES = (
    ("README.md", "# Aurora\n本地 Agent 工作台\n"),
    ("src/main.py", "print('Aurora')\n"),
    ("docs/guide.md", "在工作区中运行任务。\n"),
    ("docs/说明.md", "中文内容与 UTF-8 字符 ✓\n"),
)


@dataclass(frozen=True)
class EvalCase:
    """保存离线任务输入和独立的验收条件。"""

    id: str
    goal: str
    assertions: Sequence[Expectation]
    allow_writes: bool = False
    files: Sequence[tuple[str, str]] = SEED_FILES


FIXTURES = (
    EvalCase(
        "plan_list_files",
        "列出目录",
        (
            tasks_include("list_files"),
            effort_is("list_files", "low"),
            result_contains("list_files", "README.md", "main.py"),
        ),
    ),
    EvalCase(
        "list_subdirectory",
        "列出 src 目录",
        (result_contains("list_src", "main.py"), called_tasks("list_src")),
    ),
    EvalCase(
        "read_readme",
        "读取 README.md",
        (tasks_include("read_readme"), result_contains("read_readme", "# Aurora", "本地 Agent")),
    ),
    EvalCase(
        "analyze_readme",
        "分析 README.md",
        (effort_is("read_readme", "medium"), result_contains("read_readme", "工作台")),
    ),
    EvalCase(
        "read_source",
        "查看 src/main.py",
        (effort_is("read_src_main", "low"), result_contains("read_src_main", "print('Aurora')")),
    ),
    EvalCase(
        "compare_documents",
        "对比 README.md 与 docs/guide.md",
        (
            tasks_include("read_readme", "read_docs_guide"),
            effort_is("read_docs_guide", "medium"),
            result_contains("read_docs_guide", "运行任务"),
        ),
    ),
    EvalCase(
        "unicode_file", "读取 docs/说明.md", (result_contains("read_docs_说明", "UTF-8", "✓"),)
    ),
    EvalCase(
        "list_and_read",
        "列出目录；读取 docs/guide.md",
        (
            tasks_include("list_files", "read_docs_guide"),
            called_tasks("list_files", "read_docs_guide"),
        ),
    ),
    EvalCase(
        "deny_write",
        "创建 note.txt 为 已就绪",
        (
            effort_is("write_note", "high"),
            status_is("write_note", "failed"),
            result_contains("write_note", "拒绝"),
            file_is("note.txt", None),
            called_tasks(),
        ),
    ),
    EvalCase(
        "allow_write",
        "创建 note.txt 为 已就绪",
        (
            effort_is("write_note", "high"),
            file_is("note.txt", "已就绪"),
            called_tasks("write_note"),
        ),
        allow_writes=True,
    ),
    EvalCase(
        "read_generated_file",
        "创建 output.txt 为 校验通过；读取 output.txt",
        (
            tasks_include("write_output", "read_output"),
            result_contains("read_output", "校验通过"),
            called_tasks("write_output", "read_output"),
        ),
        allow_writes=True,
    ),
    EvalCase(
        "dependency_denied",
        "创建 note.txt 为 ready；读取 note.txt；读取 README.md",
        (
            status_is("write_note", "failed"),
            status_is("read_note", "blocked"),
            result_contains("read_readme", "本地 Agent"),
            called_tasks("read_readme"),
            file_is("note.txt", None),
        ),
    ),
    EvalCase(
        "deny_overwrite",
        "修改 README.md 为 replaced",
        (
            status_is("write_readme", "failed"),
            file_is("README.md", "# Aurora\n本地 Agent 工作台\n"),
            called_tasks(),
        ),
    ),
    EvalCase(
        "allow_overwrite",
        "修改 README.md 为 更新后的工作台；读取 README.md",
        (
            effort_is("write_readme", "high"),
            result_contains("read_readme", "更新后的工作台"),
            file_is("README.md", "更新后的工作台"),
        ),
        allow_writes=True,
    ),
)
