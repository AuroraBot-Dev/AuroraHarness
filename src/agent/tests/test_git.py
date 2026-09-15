"""会话级 Git 状态、差异与回滚测试。"""

from __future__ import annotations

import subprocess

import pytest

from aurora.agent.store import GitError, GitRepository, build_git_tools
from aurora.agent.tools import RiskLevel


def git(path, *args):
    """在测试仓库执行 Git 命令。"""
    return subprocess.run(
        ["git", "-C", str(path), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def repository(tmp_path):
    """创建带初始提交的测试仓库。"""
    git(tmp_path, "init")
    git(tmp_path, "config", "user.email", "aurora@example.test")
    git(tmp_path, "config", "user.name", "Aurora Test")
    (tmp_path / "tracked.txt").write_text("original\n", encoding="utf-8")
    git(tmp_path, "add", "tracked.txt")
    git(tmp_path, "commit", "-m", "initial")
    return GitRepository.ensure(tmp_path)


def test_ensure_initializes_unborn_repository(tmp_path):
    repo = GitRepository.ensure(tmp_path)
    try:
        assert (tmp_path / ".git").is_dir()
        status = repo.status()
        assert status["unborn"] is True
        assert status["files"] == []
    finally:
        repo.close()


def test_workspace_status_and_diff_include_untracked_and_binary(tmp_path):
    repo = repository(tmp_path)
    try:
        (tmp_path / "tracked.txt").write_text("changed\n", encoding="utf-8")
        (tmp_path / "空 格.txt").write_text("新增\n", encoding="utf-8")
        (tmp_path / "image.bin").write_bytes(b"\x00\x01")
        files = {item["path"]: item for item in repo.status()["files"]}
        assert files["tracked.txt"]["status"] == "M"
        assert files["空 格.txt"]["untracked"] is True
        assert files["image.bin"]["binary"] is True
        assert "+changed" in repo.diff("tracked.txt")["content"]
    finally:
        repo.close()


def test_run_rollback_preserves_dirty_baseline(tmp_path):
    repo = repository(tmp_path)
    try:
        (tmp_path / "tracked.txt").write_text("user change\n", encoding="utf-8")
        (tmp_path / "before.txt").write_text("keep\n", encoding="utf-8")
        repo.begin_run("run-1")
        (tmp_path / "tracked.txt").write_text("agent change\n", encoding="utf-8")
        (tmp_path / "created.txt").write_text("created\n", encoding="utf-8")
        repo.finish_run("run-1", "completed")

        status = repo.status("run", "run-1")
        assert status["canRollback"] is True
        assert {item["path"] for item in status["files"]} == {"created.txt", "tracked.txt"}
        repo.rollback("run-1")

        assert (tmp_path / "tracked.txt").read_text(encoding="utf-8") == "user change\n"
        assert (tmp_path / "before.txt").read_text(encoding="utf-8") == "keep\n"
        assert not (tmp_path / "created.txt").exists()
    finally:
        repo.close()


def test_rollback_restores_staged_and_unstaged_baseline(tmp_path):
    repo = repository(tmp_path)
    try:
        (tmp_path / "tracked.txt").write_text("staged by user\n", encoding="utf-8")
        git(tmp_path, "add", "tracked.txt")
        (tmp_path / "tracked.txt").write_text("unstaged by user\n", encoding="utf-8")
        repo.begin_run("run-1")
        (tmp_path / "tracked.txt").write_text("agent\n", encoding="utf-8")
        (tmp_path / "temporary.txt").write_text("agent\n", encoding="utf-8")
        repo.finish_run("run-1", "completed")

        repo.rollback("run-1")

        assert (tmp_path / "tracked.txt").read_text(encoding="utf-8") == "unstaged by user\n"
        assert "+staged by user" in git(tmp_path, "diff", "--cached")
        assert "+unstaged by user" in git(tmp_path, "diff")
        assert not (tmp_path / "temporary.txt").exists()
    finally:
        repo.close()


def test_rollback_rejects_changes_after_run(tmp_path):
    repo = repository(tmp_path)
    try:
        repo.begin_run("run-1")
        (tmp_path / "tracked.txt").write_text("agent\n", encoding="utf-8")
        repo.finish_run("run-1", "completed")
        (tmp_path / "tracked.txt").write_text("later\n", encoding="utf-8")
        with pytest.raises(GitError, match="发生变化"):
            repo.rollback("run-1")
        assert (tmp_path / "tracked.txt").read_text(encoding="utf-8") == "later\n"
    finally:
        repo.close()


def test_parent_repository_is_reused_and_scoped(tmp_path):
    repo = repository(tmp_path)
    repo.close()
    child = tmp_path / "child"
    child.mkdir()
    scoped = GitRepository.ensure(child)
    try:
        assert scoped.root == tmp_path
        (tmp_path / "outside.txt").write_text("outside\n", encoding="utf-8")
        (child / "inside.txt").write_text("inside\n", encoding="utf-8")
        assert [item["path"] for item in scoped.status()["files"]] == ["inside.txt"]
    finally:
        scoped.close()


def test_git_tools_are_read_only(tmp_path):
    repo = repository(tmp_path)
    try:
        tools = build_git_tools(repo)
        assert set(tools) == {"git_status", "git_diff"}
        assert all(tool.risk == RiskLevel.READ for tool in tools.values())
    finally:
        repo.close()
