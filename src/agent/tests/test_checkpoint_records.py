"""验证检查点的原子更新、兼容性与限定范围的启动恢复。"""

from contextlib import closing

import pytest
from sqlalchemy import delete

from aurora.agent.store.database import Database, now, uid
from aurora.agent.store.model import AgentRun, ConversationSession, Interaction, Run, Task, ToolCall
from aurora.agent.store.records import Records


def new_run(store, path, status="waiting"):
    stamp = now()
    run_id = uid()
    store.insert(
        ConversationSession,
        id=run_id,
        project_id=store.project(path)["id"],
        title="会话",
        created_at=stamp,
        updated_at=stamp,
    )
    store.insert(
        Run,
        id=run_id,
        session_id=run_id,
        request_key=uid(),
        goal="任务",
        status=status,
        config_snapshot_json="{}",
        created_at=stamp,
        updated_at=stamp,
    )
    return run_id


def waiting_records(store, run_id):
    stamp = now()
    store.insert(
        AgentRun,
        id=run_id,
        run_id=run_id,
        step_key="code",
        iteration=0,
        status="waiting",
        input_json="[]",
        created_at=stamp,
        updated_at=stamp,
    )
    store.insert(
        Task,
        id=run_id,
        agent_run_id=run_id,
        task_key="write",
        description="写入文件",
        tool="write_file",
        args_json="{}",
        effort="low",
        status="waiting",
        created_at=stamp,
        updated_at=stamp,
    )
    store.insert(
        Interaction,
        id=run_id,
        run_id=run_id,
        interrupt_id=uid(),
        kind="approval",
        request_json="{}",
        status="pending",
        created_at=stamp,
        updated_at=stamp,
    )
    return store.message(run_id, "assistant", "…", run_id=run_id, status="streaming")["id"]


def test_checkpoint_commit_rollback_reopen_and_cascade(tmp_path):
    path = tmp_path / "checkpoint.db"
    with closing(Records(Database(path))) as store:
        run_id = new_run(store, tmp_path)
        store.save_checkpoint(run_id, {"cursor": 1})
        with pytest.raises(RuntimeError):
            with store.db.transaction():
                store.save_checkpoint(run_id, {"cursor": 2})
                raise RuntimeError("中途退出")
        assert store.load_checkpoint(run_id) == {"cursor": 1}
        store.save_checkpoint(run_id, {"cursor": 3, "args": [False, "中文", None]})
        with pytest.raises(ValueError):
            store.save_checkpoint(run_id, {"value": float("nan")})
    with closing(Records(Database(path))) as reopened:
        assert reopened.checkpoint_run_ids() == [run_id]
        assert reopened.load_checkpoint(run_id) == {"cursor": 3, "args": [False, "中文", None]}
        reopened.db.execute(delete(Run).where(Run.id == run_id))
        assert reopened.checkpoint_run_ids() == []
        assert reopened.load_checkpoint(run_id) is None
        reopened.delete_checkpoint(run_id)


def test_recover_isolates_accepted_waiting_run_and_its_children(tmp_path):
    with closing(Records(Database(tmp_path / "checkpoint.db"))) as store:
        keep, abandoned = (new_run(store, tmp_path) for _ in range(2))
        kept_message, abandoned_message = (waiting_records(store, rid) for rid in (keep, abandoned))
        for rid in (keep, abandoned):
            store.save_checkpoint(rid, {"cursor": 1})
        store.insert(
            ToolCall,
            id=uid(),
            run_id=abandoned,
            agent_run_id=abandoned,
            task_key="write",
            tool_name="write_file",
            input_json="{}",
            status="running",
            started_at=now(),
        )
        store.recover([keep])
        for table in (Run, Task, AgentRun):
            assert store.get(table, keep)["status"] == "waiting"
            assert store.get(table, abandoned)["status"] == "interrupted"
        assert store.get(Interaction, keep)["status"] == "pending"
        assert store.get(Interaction, abandoned)["resolution"] == "runtime_restart"
        assert store.get("messages", kept_message)["status"] == "streaming"
        assert store.get("messages", abandoned_message)["status"] == "failed"
        assert store.db.one("SELECT status FROM tool_calls")["status"] == "interrupted"
        assert store.checkpoint_run_ids() == [keep]
        store.recover()
        assert store.checkpoint_run_ids() == []
        assert store.get(Interaction, keep)["status"] == "expired"


@pytest.mark.parametrize(
    "version, data, status",
    [
        (2, "{}", "waiting"),
        (1, "bad", "waiting"),
        (1, "[]", "waiting"),
        (1, '{"x":1e999}', "waiting"),
        (1, "{}", "running"),
    ],
)
def test_recover_drops_unusable_candidates(tmp_path, version, data, status):
    with closing(Records(Database(tmp_path / "checkpoint.db"))) as store:
        rid = new_run(store, tmp_path, status)
        store.insert("run_checkpoints", run_id=rid, version=version, payload_json=data)
        if status == "waiting":
            assert store.load_checkpoint(rid) is None
        store.recover([rid])
        assert store.checkpoint_run_ids() == []
        assert store.get(Run, rid)["status"] == "interrupted"
