"""发布可按持久任务 ID 合并的子树变化。"""

from __future__ import annotations

from sqlalchemy import select

from aurora.agent.store.database import now
from aurora.agent.store.model import AgentRun, Task
from aurora.agent.store.records import task_links


def publish_task(session, run_id, task_id, event, *, sibling_ids=None):
    """提交后发布完整展示字段，省略未经脱敏的工具参数。"""
    row = session.records.get(Task, task_id)
    owner = session.records.get(AgentRun, row["agent_run_id"])
    if sibling_ids is None:
        keys = (row["depends_on"] or []) + row["blocked_by"]
        sibling_ids = (
            {
                item["task_key"]: item["id"]
                for item in session.records.db.rows(
                    select(Task.task_key, Task.id).where(
                        Task.agent_run_id == owner["id"], Task.task_key.in_(keys)
                    )
                )
            }
            if keys
            else {}
        )
    task = {
        key: row[key]
        for key in (
            "id",
            "description",
            "tool",
            "effort",
            "status",
            "output",
            "error",
            "created_at",
            "updated_at",
            "retry_of_task_id",
        )
    }
    task |= {"parent_id": owner["parent_task_id"], "run_id": run_id} | task_links(row, sibling_ids)
    payload = {
        "session_id": session.id,
        "run_id": run_id,
        "task_id": task_id,
        "agent_run_id": owner["id"],
        "task": task,
        "output": row["output"],
        "error": row["error"],
    }
    payload = session.runtime.traces.clean(payload)
    session.records.event(run_id, event, payload)
    session.runtime.events.publish(event, payload)


def start_task(session, run_id, task_ids, task):
    """实际进入执行节点时更新状态，审批恢复不回退已完成任务。"""
    task_id = task_ids[task["id"]]
    with session.records.db.transaction():
        row = session.records.get(Task, task_id)
        if row["status"] in {"completed", "failed", "blocked", "cancelled", "interrupted"}:
            return task_id
        session.records.update(Task, task_id, status="running", updated_at=now())
    publish_task(session, run_id, task_id, "task.started", sibling_ids=task_ids)
    return task_id
