"""把同级计划与执行结果映射为持久任务、依赖和事件。"""

from sqlalchemy import delete, select

from aurora.agent.store.database import dumps, now, uid
from aurora.agent.store.model import Task
from aurora.agent.tools.base import RiskLevel

from .task_events import publish_task


def record_plan(session, agent_run, tasks):
    """先分配所有 UUID，再原子写入含前向依赖的计划。"""
    ids = {task["id"]: uid() for task in tasks}
    mapped = []
    with session.records.db.transaction():
        session.records.db.execute(delete(Task).where(Task.agent_run_id == agent_run))
        for task in tasks:
            depends = task.get("depends_on")
            session.records.insert(
                Task,
                id=ids[task["id"]],
                agent_run_id=agent_run,
                task_key=task["id"],
                description=task["description"],
                tool=task["tool"],
                args_json=dumps(task["args"]),
                retry_of_task_id=task.get("source_task_id"),
                depends_on_json=dumps(depends) if depends is not None else None,
                effort=str(task["effort"]),
                status="queued",
                created_at=now(),
                updated_at=now(),
            )
            public = {key: value for key, value in task.items() if key != "depends_on"}
            mapped.append(
                public
                | {
                    "id": ids[task["id"]],
                    "dependsOn": [ids[key] for key in depends or []],
                    "status": "queued",
                }
            )
    return ids, mapped


def record_result(session, run_id, ids, result):
    """区分实际工具失败和前置阻断，提交后发布对应终态。"""
    task_id = ids[result["task_id"]]
    blocked = result.get("blocked_by", [])
    output = session.runtime.redact(result["output"])
    status = "blocked" if blocked else "completed" if result["ok"] else "failed"
    session.records.update(
        Task,
        task_id,
        status=status,
        output="" if blocked else output,
        error="" if result["ok"] else output,
        blocked_by_json=dumps(blocked),
        updated_at=now(),
    )
    publish_task(session, run_id, task_id, "task." + status)
    return {
        "task_id": task_id,
        "ok": result["ok"],
        "output": output,
        "blockedBy": [ids[key] for key in blocked],
    }


def wait_for_input(session, run_id, agent_run):
    """仅暂停已进入执行节点的任务，返回对应稳定标识。"""
    rows = session.records.db.rows(
        select(Task.id).where(Task.agent_run_id == agent_run, Task.status == "running")
    )
    for row in rows:
        session.records.update(Task, row["id"], status="waiting", updated_at=now())
        publish_task(session, run_id, row["id"], "task.waiting")
    return [row["id"] for row in rows]


def read_only_delegates(session, state):
    """从冻结白名单推导可并行读取共享工作区的子树入口。"""
    if state["capabilities"].policies or session.runtime._mcp_tools:
        return set()
    tools = session._tools({"allowed_tools": ["*"]})
    read = {name for name, tool in tools.items() if tool.risk == RiskLevel.READ}
    read.update({"call_agent", "list_agents", "agent_respond"})
    agents = {
        config["agent"]["id"]: set(config["agent"]["allowed_tools"])
        for config in state["snapshot"]["agents"]
    }
    unsafe = {identifier for identifier, allowed in agents.items() if allowed - read}
    return {
        identifier
        for identifier, allowed in agents.items()
        if identifier not in unsafe and ("call_agent" not in allowed or not unsafe)
    }
