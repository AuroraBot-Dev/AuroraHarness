"""保存暂停图及递归审批桥，恢复时仅重建已知图类型。"""

from __future__ import annotations

import json
from collections import defaultdict
from importlib.metadata import version
from math import isfinite
from typing import TypeGuard

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.types import Interrupt, Send
from sqlalchemy import select

from aurora.agent.core.state import Effort
from aurora.agent.store.model import AgentRun, Interaction, Task
from aurora.agent.store.records import decode

_TAG = "$aurora.graph"
_ENGINE = version("langgraph")


def _scalar(value: object) -> TypeGuard[bool | str | float | int | None]:
    return (
        value is None or type(value) in (bool, str, int) or type(value) is float and isfinite(value)
    )


def _encode(value: object) -> dict | list | bool | str | float | int | None:
    """把已知图值转换为不执行构造指令的 JSON。"""
    if isinstance(value, Effort):
        return {_TAG: ["effort", value.value]}
    if isinstance(value, Interrupt):
        return {_TAG: ["interrupt", value.id, _encode(value.value)]}
    if isinstance(value, Send) and value.node == "execute" and value.timeout is None:
        return {_TAG: ["execute", _encode(value.arg)]}
    if type(value) is tuple:
        return {_TAG: ["tuple", [_encode(item) for item in value]]}
    if type(value) is list:
        return [_encode(item) for item in value]
    if type(value) is dict and all(type(key) is str for key in value):
        encoded = {key: _encode(item) for key, item in value.items()}
        return {_TAG: ["mapping", encoded]} if _TAG in value else encoded
    if _scalar(value):
        return value
    raise ValueError("图检查点包含不支持的类型")


def _decode(
    value: object,
) -> dict | list | tuple | Send | Interrupt | bool | str | float | int | None:
    """只解析内置标量、容器和图所需的三种固定数据类型。"""
    if type(value) is list:
        return [_decode(item) for item in value]
    if type(value) is not dict:
        if _scalar(value):
            return value
        raise ValueError("图检查点值无效")
    if _TAG not in value:
        return {key: _decode(item) for key, item in value.items()}
    if set(value) == {_TAG}:
        match value[_TAG]:
            case ["effort", str(effort)]:
                return Effort(effort)
            case ["interrupt", str(identifier), dict(data)]:
                return Interrupt(id=identifier, value=_decode(data))
            case ["execute", {"current_task": dict(task)}]:
                return Send("execute", {"current_task": _decode(task)})
            case ["tuple", list(items)]:
                return tuple(_decode(item) for item in items)
            case ["mapping", dict(mapping)]:
                return {key: _decode(item) for key, item in mapping.items()}
    raise ValueError("图检查点类型标签无效")


def _snapshot(graph, agent_run_id):
    """保存最近的完整通道值及同一步已经完成的并行写入。"""
    latest = graph.checkpointer.get_tuple({"configurable": {"thread_id": agent_run_id}})
    if not latest or not any(key == "__interrupt__" for _, key, _ in latest.pending_writes or []):
        raise ValueError("图不在可恢复的等待点")
    return _encode(
        {
            "checkpoint": latest.checkpoint,
            "metadata": latest.metadata,
            "writes": latest.pending_writes,
        }
    )


def _cursor(saved) -> dict:
    """读取当前受支持的检查点格式。"""
    value = _decode(saved["cursor"])
    if not isinstance(value, dict):
        raise ValueError("图检查点无效")
    checkpoint = value["checkpoint"]
    if (
        not isinstance(checkpoint, dict)
        or checkpoint.get("v") != 4
        or not isinstance(checkpoint.get("channel_values"), dict)
    ):
        raise ValueError("图检查点版本或通道无效")
    return value


def checkpoint_saver(saved=None):
    """在原线程与检查点 ID 上恢复图，跳过已提交的同级节点。"""
    saver = InMemorySaver(
        serde=JsonPlusSerializer(allowed_msgpack_modules=[(Effort.__module__, Effort.__name__)])
    )
    if saved is None:
        return saver
    value = _cursor(saved)
    checkpoint = value["checkpoint"]
    config: RunnableConfig = {"configurable": {"thread_id": saved["id"], "checkpoint_ns": ""}}
    config = saver.put(config, checkpoint, value["metadata"], checkpoint["channel_versions"])
    writes = defaultdict(list)
    for task_id, key, item in value["writes"]:
        writes[task_id].append((key, item))
    for task_id, items in writes.items():
        saver.put_writes(config, items, task_id)
    return saver


def export_graphs(state):
    """导出当前编写角色和仍参与当前层委派的子图。"""
    if "graph_restart" in state:
        return {
            "engine": _ENGINE,
            "root": state["graph_restart"],
            "children": state["saved_children"],
        }
    identifier = state["code_agent_run_id"]
    root = {
        "id": identifier,
        "tasks": dict(state["task_ids"]),
        "cursor": _snapshot(state["graph"], identifier),
    }
    children = dict(state.get("saved_children", {}))
    for key, child in state.get("children", {}).items():
        if "result" in child or "error" in child:
            children[key] = {k: child[k] for k in ("id", "result", "error") if k in child}
            continue
        saved = {k: child[k] for k in ("id", "tasks", "pending_task_id", "resolved") if k in child}
        saved["pending"] = _encode(child["pending"])
        saved["cursor"] = _snapshot(child["graph"], child["id"])
        children[key] = saved
    return {"engine": _ENGINE, "root": root, "children": children}


def _validate_role(records, run_id, saved):
    """核对角色、任务及暂停记录的归属。"""
    row = records.get(AgentRun, saved["id"])
    if row["run_id"] != run_id:
        raise ValueError("检查点角色归属不符")
    if "cursor" not in saved:
        if not (
            row["status"] == "completed"
            and "result" in saved
            or row["status"] == "failed"
            and "error" in saved
        ):
            raise ValueError("子执行终态不符")
        return row
    if row["status"] != "waiting":
        raise ValueError("检查点角色未在等待")
    stored = [
        decode(item) for item in records.db.rows(select(Task).where(Task.agent_run_id == row["id"]))
    ]
    if saved["tasks"] != {item["task_key"]: item["id"] for item in stored}:
        raise ValueError("检查点缺少已登记的任务")
    value = _cursor(saved)
    channels = value["checkpoint"]["channel_values"]
    plan = {task["id"]: task for task in channels["tasks"]}
    if len(plan) != len(channels["tasks"]) or plan.keys() != saved["tasks"].keys():
        raise ValueError("检查点计划与已登记的任务不符")
    for item in stored:
        task = plan[item["task_key"]]
        if (
            not isinstance(task["effort"], Effort)
            or any(task[key] != item[key] for key in ("tool", "args", "description", "effort"))
            or task.get("source_task_id") != item["retry_of_task_id"]
            or task.get("depends_on") != item.get("depends_on")
        ):
            raise ValueError("检查点中的工具任务内容与数据库不符")
    for send in channels.get("__pregel_tasks", []):
        if (
            not isinstance(send, Send)
            or send.arg["current_task"] != plan[send.arg["current_task"]["id"]]
        ):
            raise ValueError("并行派发内容与已验证的计划不符")
    completed = list(channels.get("results", []))
    for _, key, writes in value["writes"]:
        if key == "results":
            completed.extend(writes)
    facts = {
        item["task_key"]: (item["status"] == "completed", item.get("blocked_by", []))
        for item in stored
        if item["status"] in {"completed", "failed", "blocked"}
    }
    if (
        len(completed) != len(facts)
        or {item["task_id"]: (item["ok"], item.get("blocked_by", [])) for item in completed}
        != facts
    ):
        raise ValueError("检查点结果与已执行的工具任务不符")
    checkpoint_saver(saved)
    return row


def restore_graphs(session, run_id, state, payload):
    """校验暂停树并延迟绑定模型与工具，不执行用户任务。"""
    try:
        if payload["engine"] != _ENGINE:
            raise ValueError("图引擎版本已改变")
        root, children = payload["root"], payload["children"]
        row = _validate_role(session.records, run_id, root)
        if row["parent_task_id"] is not None or row["step_key"] != "code" or "cursor" not in root:
            raise ValueError("根角色检查点无效")
        for key, child in children.items():
            row = _validate_role(session.records, run_id, child)
            parent = session.records.get(Task, row["parent_task_id"])
            if key != parent["agent_run_id"] + ":" + parent["task_key"]:
                raise ValueError("子角色检查点归属不符")
            if "cursor" in child and not isinstance(_decode(child["pending"]), Interrupt):
                raise ValueError("子角色缺少暂停点")
        expected = {
            item["id"]
            for item in session.records.db.rows(
                select(AgentRun.id).where(AgentRun.run_id == run_id, AgentRun.status == "waiting")
            )
        }
        actual = {role["id"] for role in (root, *children.values()) if "cursor" in role}
        if expected != actual:
            raise ValueError("等待中的委派树不完整")
        writes = _cursor(root)["writes"]
        finished = {task_id for task_id, key, items in writes if key == "results" and items}
        requests = {
            i.id: i.value
            for task_id, key, items in writes
            if key == "__interrupt__" and task_id not in finished
            for i in items
            if isinstance(i, Interrupt)
        }
        pending = session.records.db.rows(
            select(Interaction).where(Interaction.run_id == run_id, Interaction.status == "pending")
        )
        confirmed = {
            state["interrupt_ids"][item["interrupt_id"]]: json.loads(item["request_json"])
            for item in pending
        }
        if not confirmed or requests != confirmed or len(confirmed) != len(pending):
            raise ValueError("图暂停点与待处理请求不符")
        state.update(
            graph_restart=root,
            saved_children=dict(children),
            code_agent_run_id=root["id"],
            task_ids=dict(root["tasks"]),
        )
    except (KeyError, IndexError, TypeError, AttributeError, RecursionError) as exc:
        raise ValueError("无效的工具图检查点") from exc


def pending_interrupt(saved):
    """解码原子图公开审批所对应的中断。"""
    return _decode(saved["pending"])
