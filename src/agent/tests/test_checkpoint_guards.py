"""验证持久恢复的权限、数据完整性及工作区边界。"""

from pathlib import Path

import pytest
from test_persistence import runtime_at

from aurora.agent.orchestration.runtime import RunControlError


@pytest.mark.parametrize("after_boot", [True, False])
def test_revoked_sandbox_prevents_resuming_waiting_write(tmp_path, after_boot):
    runtime, workspace, _, _ = runtime_at(tmp_path)
    session = runtime.create_session(str(workspace))
    paused = session.start("写页面")
    if not after_boot:
        runtime.records.update("sessions", session.id, sandbox_mode="read-only")
    runtime.close()
    restarted, _, goals, _ = runtime_at(tmp_path)
    try:
        restored = restarted.get_session(session.id)
        if after_boot:
            (pending,) = restored._result(paused.run_id).interruptions
            restarted.records.update("sessions", session.id, sandbox_mode="read-only")
            with pytest.raises(RunControlError) as caught:
                restored.resume(paused.run_id, True, pending.id)
            assert caught.value.code == "CHECKPOINT_STALE"
        assert restarted.records.get("runs", paused.run_id)["status"] == "interrupted"
        assert not (workspace / "index.html").exists() and not goals
        assert not restarted.records.checkpoint_run_ids()
    finally:
        restarted.close()


def test_corrupt_checkpoint_cannot_replenish_task_budget(tmp_path):
    runtime, workspace, _, _ = runtime_at(tmp_path)
    session = runtime.create_session(str(workspace))
    paused = session.start("写页面")
    payload = runtime.records.load_checkpoint(paused.run_id)
    assert payload["budget"]
    payload["budget"] = {}
    runtime.records.save_checkpoint(paused.run_id, payload)
    runtime.close()
    restarted, _, _, _ = runtime_at(tmp_path)
    try:
        assert restarted.records.get("runs", paused.run_id)["status"] == "interrupted"
        assert not restarted.records.checkpoint_run_ids()
        assert not (workspace / "index.html").exists()
    finally:
        restarted.close()


def test_database_in_project_keeps_private_git_archive_outside_worktree(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    (project / ".gitignore").write_text("data/\n", encoding="utf-8")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "profile"))
    runtime, _, _, _ = runtime_at(project)
    session = runtime.create_session(str(project))
    paused = session.start("写页面")
    archive = runtime.checkpoints.external / paused.run_id
    assert list(archive.glob("*.pack")) and not archive.is_relative_to(project)
    runtime.close()
    restarted, _, _, _ = runtime_at(project)
    try:
        restored = restarted.get_session(session.id)
        (item,) = restored._result(paused.run_id).interruptions
        assert restored.resume(paused.run_id, True, item.id).status == "completed"
        assert not archive.exists()
    finally:
        restarted.close()


def test_startup_preserves_user_files_in_checkpoint_directories(tmp_path):
    runtime, workspace, _, _ = runtime_at(tmp_path)
    session = runtime.create_session(str(workspace))
    paused = session.start("写页面")
    root = runtime.checkpoints.root
    owned = root / paused.run_id
    unrelated = root / "4ff1ba86-aa61-4bc6-be97-a1cf2ced8a20"
    unrelated.mkdir()
    pack = unrelated / ("b" * 64 + ".pack")
    pack.write_bytes(b"user archive")
    extra = owned / ("c" * 64 + ".pack")
    extra.write_bytes(b"another archive")
    notes = owned / "notes.txt"
    notes.write_text("用户记录", encoding="utf-8")
    session.cancel(paused.run_id)
    runtime.close()
    restarted, _, _, _ = runtime_at(tmp_path)
    try:
        assert notes.read_text(encoding="utf-8") == "用户记录"
        assert pack.read_bytes() == b"user archive"
        assert list(owned.glob("*.pack")) == [extra]
        assert extra.read_bytes() == b"another archive"
    finally:
        restarted.close()


@pytest.mark.parametrize("kind", ["registered_secret", "structured_secret", "dynamic_policy"])
def test_process_only_waits_reject_persistent_replay(tmp_path, monkeypatch, kind):
    from aurora.agent.capability.specialization import Capability, CapabilityPolicy

    fake = "test-only-checkpoint-credential"
    monkeypatch.setenv("AGENT_API_KEY", fake)
    runtime, workspace, _, _ = runtime_at(tmp_path)
    goal = "写页面"
    if kind == "registered_secret":
        goal += fake
    elif kind == "structured_secret":

        class Planner:
            def plan(self, goal):
                return [
                    dict(
                        id="write",
                        description=goal,
                        effort="low",
                        tool="write_file",
                        args={"path": "index.html", "content": '{"token":"private-value"}'},
                    )
                ]

        runtime._planner_factory = lambda model, tools: Planner()
    else:
        runtime.capabilities.register(CapabilityPolicy("in-process", lambda context: Capability()))
    session = runtime.create_session(str(workspace))
    paused = session.start(goal)
    try:
        assert paused.status == "waiting"
        assert runtime.records.load_checkpoint(paused.run_id) is None
        assert not runtime.checkpoints.root.exists()
    finally:
        runtime.close()
    restarted, _, _, _ = runtime_at(tmp_path)
    try:
        assert restarted.records.get("runs", paused.run_id)["status"] == "interrupted"
        assert not (workspace / "index.html").exists()
    finally:
        restarted.close()
