"""验证依赖关系的旧库升级、持久标识与事件边界。"""

import sqlite3
from contextlib import closing

from test_persistence import runtime_at

from aurora.agent.orchestration.task_events import publish_task, start_task
from aurora.agent.store.database import dumps, now, uid
from aurora.agent.store.model import AgentRun, Task
from aurora.agent.transport import RuntimeApi


def read(runtime, method, **params):
    """通过运行时公开接口读取历史。"""
    return RuntimeApi(runtime).handle({"id": "read", "method": method, "params": params})[0][
        "result"
    ]


def test_upgrade_six_preserves_legacy_plan_and_task_contents(tmp_path):
    runtime, workspace, _, _ = runtime_at(tmp_path)
    session = runtime.create_session(str(workspace), approval_mode="always")
    run = session.start("写入页面")
    task = read(runtime, "run.get", runId=run.run_id)["tasks"][0]
    path = runtime.records.db.path
    runtime.close()
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript(
            "ALTER TABLE tasks DROP COLUMN depends_on_json;"
            "ALTER TABLE tasks DROP COLUMN blocked_by_json;"
            "DELETE FROM schema_migrations WHERE version=7;"
        )
    restarted, _, _, _ = runtime_at(tmp_path)
    try:
        assert restarted.records.get(Task, task["id"])["depends_on"] is None
        assert restarted.records.get(Task, task["id"])["blocked_by"] == []
        saved = read(restarted, "run.get", runId=run.run_id)["tasks"][0]
        assert saved == task
        assert saved["dependsOn"] == saved["blockedBy"] == []
    finally:
        restarted.close()


def test_dependencies_use_owner_scoped_persistent_ids_in_snapshots_and_events(tmp_path):
    runtime, workspace, _, _ = runtime_at(tmp_path)
    try:
        session = runtime.create_session(str(workspace))
        run = session.start("保存角色历史", mode="say")
        records = runtime.records
        original = records.db.one("SELECT * FROM agent_runs WHERE run_id=?", (run.run_id,))
        assert original is not None
        sibling = records.insert(AgentRun, **(original | {"id": uid(), "step_key": "other"}))
        expected = {}
        for owner in [original["id"], sibling["id"]]:
            ids = {key: uid() for key in ("prepare", "build", "publish")}
            for key in ("publish", "build", "prepare"):
                args = {
                    "publish": (["prepare", "build"], ["build"], "blocked"),
                    "build": (["prepare"], [], "failed"),
                    "prepare": ([], [], "completed"),
                }[key]
                records.insert(
                    Task,
                    id=ids[key],
                    agent_run_id=owner,
                    task_key=key,
                    description=key,
                    tool="write_file",
                    args_json=dumps({"private": "task-internal-value"}),
                    depends_on_json=dumps(args[0]),
                    blocked_by_json=dumps(args[1]),
                    effort="low",
                    status=args[2],
                    created_at=now(),
                    updated_at=now(),
                )
            expected[owner] = ids
        views = [
            read(runtime, "run.get", runId=run.run_id),
            read(runtime, "session.get", sessionId=session.id),
        ]
        for view in views:
            assert len(view["tasks"]) == 6
            for owner, ids in expected.items():
                row = next(task for task in view["tasks"] if task["id"] == ids["publish"])
                assert row["status"] == "blocked" and row["agentRunId"] == owner
                assert row["dependsOn"] == [ids["prepare"], ids["build"]]
                assert row["blockedBy"] == [ids["build"]]
        events = []
        unsubscribe = runtime.events.subscribe(lambda name, data: events.append((name, data)))
        for owner, ids in expected.items():
            assert records.get(Task, ids["prepare"])["depends_on"] == []
            details = read(runtime, "agentRun.get", agentRunId=owner)
            assert {task["id"] for task in details["tasks"]} == set(ids.values())
            row = next(task for task in details["tasks"] if task["id"] == ids["publish"])
            publish_task(session, run.run_id, row["id"], "task.blocked")
            name, data = events[-1]
            assert name == "task.blocked" and data["task_id"] == row["id"]
            assert data["task"]["dependsOn"] == row["dependsOn"]
            assert data["task"]["blockedBy"] == row["blockedBy"]
            assert "args" not in data["task"] and "task-internal-value" not in str(data)
            assert start_task(session, run.run_id, ids, {"id": "publish"}) == row["id"]
            assert records.get(Task, row["id"])["status"] == "blocked"
        assert len(events) == 2
        unsubscribe()
    finally:
        runtime.close()
