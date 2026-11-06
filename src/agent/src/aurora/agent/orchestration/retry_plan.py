"""从不可变历史构造只重试目标范围的执行计划。"""

from __future__ import annotations

import base64
from copy import deepcopy
from typing import cast

from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy import literal_column, select

from aurora.agent.conversation.session import _message_text
from aurora.agent.core import Effort
from aurora.agent.core.effort_policy import current_effort, current_task_key, invoke_with_effort
from aurora.agent.core.plan_validation import PlanValidationError, validate_dependencies
from aurora.agent.core.state import Task as TaskSpec
from aurora.agent.store.database import dumps, uid
from aurora.agent.store.model import AgentRun, Artifact, Project, Run, Task, Workflow
from aurora.agent.store.records import decode
from aurora.agent.tools.base import Tool
from aurora.common.cancellation import check_cancelled

from .model_usage import invoke_with_usage
from .runtime import RunControlError


class RetryPlanner:
    """复用已持久化任务，不允许重新规划扩大重试范围。"""

    def __init__(self, node):
        self.node = node

    def plan(self, goal: str) -> list[TaskSpec]:
        """返回新的任务副本并恢复执行强度枚举。"""
        return [
            cast(TaskSpec, dict(deepcopy(task), effort=Effort(task["effort"])))
            for task in self.node["tasks"]
        ]


def _retry_scope(owned, selected, force):
    """选择重试目标及其未满足的前置，裁掉已保留的依赖边。"""
    by_key = {row["task_key"]: row for row in owned}
    declared = {
        row["task_key"]: row["depends_on"] for row in owned if row.get("depends_on") is not None
    }
    try:
        dependencies = validate_dependencies(by_key, declared)
    except PlanValidationError as exc:
        raise RunControlError("RUN_NOT_RETRYABLE", "来源任务的依赖记录无效") from exc
    if any(
        by_key[key]["status"] == "completed"
        and any(by_key[ref]["status"] != "completed" for ref in refs)
        for key, refs in dependencies.items()
    ):
        raise RunControlError("RUN_NOT_RETRYABLE", "来源任务与前置任务的完成状态不一致")
    if selected is None:
        keys = {key for key, row in by_key.items() if force or row["status"] != "completed"}
    else:
        keys = {selected["task_key"]}
        pending = list(keys)
        while pending:
            for ref in dependencies.get(pending.pop(), []):
                if ref not in keys and by_key[ref]["status"] != "completed":
                    keys.add(ref)
                    pending.append(ref)
    chosen = [row for row in owned if row["task_key"] in keys]
    edges = {
        key: [ref for ref in refs if ref in keys]
        for key, refs in dependencies.items()
        if key in keys
    }
    if chosen and declared and not edges:
        edges[chosen[0]["task_key"]] = []
    return chosen, edges


def build_retry(session, source_id, task_id=None):
    """检查来源并冻结角色配置、任务范围与已完成结果引用。"""
    records = session.records
    source = session.runtime.require_run(session.id, source_id)
    if source["status"] in {"queued", "running", "waiting"}:
        raise RunControlError("RUN_NOT_RETRYABLE", "运行仍在执行或等待输入，请先停止")
    if not task_id and source["status"] == "completed":
        raise RunControlError("RUN_NOT_RETRYABLE", "整轮重试只用于未成功结束的运行")
    source = decode(source)
    executions = [
        decode(row) for row in records.db.rows(select(AgentRun).where(AgentRun.run_id == source_id))
    ]
    tasks = [
        decode(row)
        for row in records.db.rows(
            select(Task)
            .where(Task.agent_run_id.in_([row["id"] for row in executions]))
            .order_by(literal_column("tasks.rowid"))
        )
    ]
    by_execution = {row["id"]: row for row in executions}
    by_task = {row["id"]: row for row in tasks}

    def role_path(execution):
        """保留来源角色的子树作用域，不把局部重试提升到全局。"""
        parent = by_task.get(execution["parent_task_id"])
        if parent is None:
            return []
        return [*role_path(by_execution[parent["agent_run_id"]]), parent["task_key"]]

    def node(execution, selected=None, force=False):
        """复制该角色范围，并只为未完成委派递归收集原有计划。"""
        owned = [task for task in tasks if task["agent_run_id"] == execution["id"]]
        chosen, edges = _retry_scope(owned, selected, force)
        parent = by_task.get(execution["parent_task_id"])
        inherited = execution["input"].get("retry", {})
        retained = list(inherited.get("retained", []))
        retained.extend(
            dict(id=task["id"], description=task["description"], output=task["output"])
            for task in owned
            if task not in chosen and task["status"] == "completed"
        )
        result = {
            "source_agent_run_id": execution["id"],
            "effort": execution["input"].get("effort_policy", {}).get("effort")
            or inherited.get("effort")
            or (parent["effort"] if parent else None),
            "agent_id": execution["agent_id"],
            "path": inherited.get("role_path", role_path(execution)),
            "serial": inherited.get(
                "serial",
                bool(parent)
                or bool(
                    {"*", "call_agent"}.intersection(
                        execution["config_snapshot"].get("agent", {}).get("allowed_tools", ["*"])
                    )
                ),
            ),
            "goal": inherited.get("role_goal")
            or (parent["args"].get("task", source["goal"]) if parent else source["goal"]),
            "artifact_ids": list(parent["args"].get("artifact_ids", []))
            if parent
            else list(execution["input"].get("artifact_ids", [])),
            "tasks": [],
            "retained": list({item["id"]: item for item in retained}.values()),
            "children": {},
        }
        for task in chosen:
            result["tasks"].append(
                {
                    "id": task["task_key"],
                    "source_task_id": task["id"],
                    "description": task["description"],
                    "tool": task["tool"],
                    "args": deepcopy(task["args"]),
                    "effort": task["effort"],
                    **(
                        {"depends_on": edges[task["task_key"]]} if task["task_key"] in edges else {}
                    ),
                }
            )
            child = next((row for row in executions if row["parent_task_id"] == task["id"]), None)
            if child and any(row["agent_run_id"] == child["id"] for row in tasks):
                result["children"][task["id"]] = node(
                    child, force=force or task["status"] == "completed"
                )
        return result

    snapshot = deepcopy(source["config_snapshot"])
    selected = by_task.get(task_id) if task_id else None
    if task_id and selected is None:
        raise RunControlError("TASK_NOT_FOUND", "任务不存在或不属于来源运行")
    if selected:
        execution = by_execution[selected["agent_run_id"]]
        root = node(execution, selected, force=selected["status"] == "completed")
        config = records.agent_config(execution["agent_id"])
        if root["path"] and not config["agent"]["callable"]:
            raise RunControlError("RUN_NOT_RETRYABLE", "来源角色已撤回委派权限")
        snapshot["workflow"] = dict(snapshot["workflow"], kind="single", max_revisions=0)
        snapshot["steps"] = [
            dict(
                step_key="code",
                kind="code",
                position=0,
                agent_id=config["agent"]["id"],
                config=config,
            )
        ]
        goal, mode = selected["description"], "run"
    else:
        if not records.get(Workflow, snapshot["workflow"]["id"])["enabled"]:
            raise RunControlError("RUN_NOT_RETRYABLE", "来源工作流已停用")
        for step in snapshot["steps"]:
            if step["agent_id"]:
                step["config"] = records.agent_config(step["agent_id"])
        code = sorted(
            (row for row in executions if row["step_key"] == "code"),
            key=lambda row: row["iteration"],
        )
        root = (
            node(code[-1])
            if source["mode"] == "run"
            and code
            and (
                any(task["agent_run_id"] == code[-1]["id"] for task in tasks)
                or code[-1]["status"] == "completed"
            )
            else None
        )
        if root and root["path"]:
            config = next(step["config"] for step in snapshot["steps"] if step["kind"] == "code")
            if not config["agent"]["callable"]:
                raise RunControlError("RUN_NOT_RETRYABLE", "来源角色已撤回委派权限")
        goal, mode = source["goal"], source["mode"]
    from aurora.agent.capability.agent_registry import AgentRegistry

    snapshot["agents"] = AgentRegistry(session.runtime).snapshot()
    snapshot["sandbox_mode"] = session.record["sandbox_mode"]
    snapshot["approval_mode"] = session.record["approval_mode"]
    snapshot["preview"] = records.get(Project, session.record["project_id"])["preview"]
    history = (
        execution["input"].get("history", [])
        if selected
        else code[-1]["input"].get("history", [])
        if code
        else []
    )
    snapshot["retry"] = {
        "source_run_id": source_id,
        "task_id": task_id,
        "plan": root,
        "history": history,
    }
    return goal, mode, snapshot


def prepare_retry(session, run_id, state):
    """复制重试范围中的图片证据并生成本轮可用的子计划索引。"""
    retry = state["snapshot"].get("retry")
    if not retry:
        return
    root = deepcopy(retry["plan"])
    if root is None:
        return
    copied = {}

    def image(source_id):
        """校验来源归属并创建新的产物记录。"""
        if source_id not in copied:
            artifact = session.records.get(Artifact, source_id)
            if artifact["run_id"] != retry["source_run_id"]:
                raise ValueError("重试图片不属于来源运行")
            _, data = session.records.artifact_bytes(source_id)
            row = session.records.artifact(
                run_id,
                None,
                artifact["kind"],
                data,
                artifact["media_type"],
                artifact["metadata"] | {"retry_of_artifact_id": source_id},
            )
            copied[source_id] = row["id"]
        return copied[source_id]

    def visit(node):
        """替换当前运行证据引用，并保留原任务的来源键。"""
        node["artifact_ids"] = [image(value) for value in node["artifact_ids"]]
        for task in node["tasks"]:
            if task["tool"] == "call_agent" and task["args"].get("artifact_ids"):
                task["args"]["artifact_ids"] = [
                    image(value) for value in task["args"]["artifact_ids"]
                ]
        for task_id, child in node["children"].items():
            visit(child)
            state.setdefault("retry_nodes", {})[task_id] = child

    visit(root)
    state["retry_root"] = root
    state["snapshot"]["retry"] = retry | {"plan": root, "artifacts": copied}
    session.records.update(Run, run_id, config_snapshot_json=dumps(state["snapshot"]))


def retry_context(node, budget):
    """为同一角色提供有界的已完成结果摘要。"""
    value = "原任务：" + node["goal"] + "\n已保留的同角色任务：\n" + dumps(node["retained"])
    return value.encode("utf-8")[: max(0, budget)].decode("utf-8", errors="ignore")


def response_tool(session, model, config, inputs, artifacts, run_id, agent_run):
    """在原角色隔离上下文中重试文字或图片理解任务。"""
    content: list[str | dict] = [{"type": "text", "text": inputs["current"]}]
    for artifact_id in artifacts:
        artifact = session.records.get(Artifact, artifact_id)
        if artifact["run_id"] != run_id or artifact["media_type"] not in {
            "image/png",
            "image/jpeg",
            "image/webp",
        }:
            raise ValueError("重试图片无效或不属于当前运行")
        if config["model"]["model_type"] != "multimodal":
            raise ValueError("文本模型不支持重试图片任务")
        _, data = session.records.artifact_bytes(artifact_id)
        content.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{artifact['media_type']};base64,"
                    + base64.b64encode(data).decode()
                },
            }
        )

    def respond(prompt: str) -> str:
        """执行一次受当前取消信号约束的角色回答。"""
        response = invoke_with_usage(
            session.records,
            agent_run,
            current_task_key() or uid(),
            lambda: invoke_with_effort(
                model,
                [
                    SystemMessage(content=config["agent"]["instructions"]),
                    HumanMessage(content=content),
                    HumanMessage(content=prompt),
                ],
                current_effort(config.get("effort_policy", {}).get("effort", Effort.HIGH)),
                max_output_tokens=config.get("effort_policy", {}).get("max_output_tokens"),
            ),
        )
        check_cancelled()
        return _message_text(response)

    return Tool("agent_respond", "重试原角色的分析或图片理解任务", respond)
