"""验证跨进程 Git 基线恢复、完整性与归属边界。"""

import json
import subprocess

import pytest

from aurora.agent.store import GitError, GitRepository


def git(path, *args):
    return subprocess.run(
        ["git", "-C", str(path), *args], capture_output=True, check=True, text=True
    ).stdout.strip()


def test_reopen_after_prune_preserves_diff_and_exact_staging(tmp_path):
    work, archive = tmp_path / "work", tmp_path / "state"
    work.mkdir()
    first = GitRepository.ensure(work)
    tracked = work / "tracked"
    tracked.write_text("user staged\n", encoding="utf-8")
    git(work, "add", "tracked")
    original_index = (work / ".git/index").read_bytes()
    staged = git(work, "rev-parse", ":tracked")
    tracked.write_text("user working copy\n", encoding="utf-8")
    first.begin_run("r")
    tracked.write_text("agent write\n", encoding="utf-8")
    git(work, "add", "tracked")
    payload = first.export_checkpoint("r", archive)
    assert "user" not in json.dumps(payload)
    first.close()
    git(work, "gc", "--prune=now")
    assert subprocess.run(
        ["git", "-C", str(work), "cat-file", "-e", staged], capture_output=True
    ).returncode
    reopened = GitRepository(work)
    try:
        reopened.restore_checkpoint("r", json.loads(json.dumps(payload)), archive)
        diff = reopened.diff("tracked", "run", "r")["content"]
        assert "-user working copy" in diff and "+agent write" in diff
        assert tracked.read_text(encoding="utf-8") == "agent write\n"
        reopened.finish_run("r", "completed")
        reopened.rollback("r")
        assert tracked.read_text(encoding="utf-8") == "user working copy\n"
        assert git(work, "show", ":tracked") == "user staged"
        assert (work / ".git/index").read_bytes() == original_index
    finally:
        reopened.close()


def test_unborn_missing_index_restores(tmp_path):
    work, archive = tmp_path / "work", tmp_path / "state"
    work.mkdir()
    repository = GitRepository.ensure(work)
    repository.begin_run("r")
    (work / "new").write_text("change", encoding="utf-8")
    payload = repository.export_checkpoint("r", archive)
    repository.close()
    reopened = GitRepository(work)
    try:
        reopened.restore_checkpoint("r", payload, archive)
        reopened.finish_run("r", "completed")
        reopened.rollback("r")
        assert not (work / "new").exists()
        assert not (work / ".git/index").exists()
    finally:
        reopened.close()


@pytest.mark.parametrize(
    "failure", ["scope", "run", "edit", "index", "branch", "pack", "oid", "version"]
)
def test_checkpoint_refuses_corruption_and_workspace_changes(tmp_path, failure):
    work, archive = tmp_path / "work", tmp_path / "state"
    work.mkdir()
    original = GitRepository.ensure(work)
    child = work / "child"
    child.mkdir()
    (child / "file").write_text("baseline", encoding="utf-8")
    original.begin_run("r")
    (child / "file").write_text("after", encoding="utf-8")
    payload = original.export_checkpoint("r", archive)
    original.close()
    if failure == "edit":
        (child / "file").write_text("later edits", encoding="utf-8")
    if failure == "index":
        git(work, "add", "child")
    if failure == "branch":
        git(work, "symbolic-ref", "HEAD", "refs/heads/changed")
    if failure == "pack":
        next(archive.glob("*.pack")).write_bytes(b"bad")
    if failure == "oid":
        payload["manifest"] = "HEAD:../malicious"
    if failure == "version":
        payload["version"] = True
    reopened = GitRepository(child if failure == "scope" else work)
    expected = (child / "file").read_text(encoding="utf-8")
    try:
        with pytest.raises(GitError):
            reopened.restore_checkpoint("another" if failure == "run" else "r", payload, archive)
        with pytest.raises(GitError, match="没有可用"):
            reopened.status("run", "r")
        assert (child / "file").read_text(encoding="utf-8") == expected
    finally:
        reopened.close()


def test_recovered_rollback_unlinks_only_the_link(tmp_path):
    work, archive = tmp_path / "work", tmp_path / "state"
    work.mkdir()
    original = GitRepository.ensure(work)
    target = work / "user-file"
    target.write_text("preserve", encoding="utf-8")
    original.begin_run("r")
    link = work / "agent-link"
    try:
        link.symlink_to(target)
    except OSError:
        original.close()
        pytest.skip("本机不能创建符号链接")
    payload = original.export_checkpoint("r", archive)
    original.close()
    reopened = GitRepository(work)
    try:
        reopened.restore_checkpoint("r", payload, archive)
        reopened.finish_run("r", "completed")
        reopened.rollback("r")
        assert not link.is_symlink()
        assert target.read_text(encoding="utf-8") == "preserve"
    finally:
        reopened.close()


def test_subdirectory_pack_excludes_sibling_contents_and_protects_sibling_staging(tmp_path):
    root, archive = tmp_path / "repo", tmp_path / "state"
    root.mkdir()
    git(root, "init")
    child = root / "child"
    child.mkdir()
    keep = root / "sibling"
    keep.write_text("outside head", encoding="utf-8")
    git(root, "add", "sibling")
    git(root, "-c", "user.name=test", "-c", "user.email=t@test", "commit", "-m", "initial")
    outside_head = git(root, "rev-parse", "HEAD:sibling")
    keep.write_text("user staged outside", encoding="utf-8")
    git(root, "add", "sibling")
    outside_stage = git(root, "rev-parse", ":sibling")
    repository = GitRepository(child)
    (child / "file").write_text("base", encoding="utf-8")
    repository.begin_run("r")
    (child / "file").write_text("agent", encoding="utf-8")
    payload = repository.export_checkpoint("r", archive)
    pack = next(archive.glob("*.pack"))
    git(root, "index-pack", str(pack))
    members = git(root, "verify-pack", "-v", str(pack.with_suffix(".idx")))
    assert outside_head not in members and outside_stage not in members
    repository.close()
    reopened = GitRepository(child)
    try:
        reopened.restore_checkpoint("r", payload, archive)
        assert "+agent" in reopened.diff("file", "run", "r")["content"]
        keep.write_text("new sibling edit", encoding="utf-8")
        git(root, "add", "sibling")
        reopened.finish_run("r", "completed")
        with pytest.raises(GitError, match="工作区外"):
            reopened.rollback("r")
        assert git(root, "show", ":sibling") == "new sibling edit"
    finally:
        reopened.close()


def test_intermediate_stage_keeps_diff_after_repeated_export(tmp_path):
    work, archive = tmp_path / "work", tmp_path / "state"
    work.mkdir()
    repo = GitRepository.ensure(work)
    target = work / "page"
    target.write_text("baseline", encoding="utf-8")
    repo.begin_run("r")
    target.write_text("finished code phase", encoding="utf-8")
    repo.finish_run("r", "running")
    target.write_text("paused worktree", encoding="utf-8")
    first = repo.export_checkpoint("r", archive)
    second = repo.export_checkpoint("r", archive)
    repo.close()
    for payload in (first, second):
        reopened = GitRepository(work)
        try:
            reopened.restore_checkpoint("r", payload, archive)
            diff = reopened.diff("page", "run", "r")["content"]
            assert "+finished code phase" in diff and "paused worktree" not in diff
            assert target.read_text(encoding="utf-8") == "paused worktree"
        finally:
            reopened.close()
