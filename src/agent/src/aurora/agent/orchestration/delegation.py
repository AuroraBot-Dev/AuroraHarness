"""在父运行内串行执行带独立检查点的子 Agent。"""

from __future__ import annotations

import base64
from contextvars import Context
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.errors import GraphInterrupt
from langgraph.types import Command, interrupt

from aurora.agent.conversation.session import _message_text
from aurora.agent.core import NoClarifier, build_delegation_graph
from aurora.agent.core.effort_policy import (
    apply_effort,
    current_effort,
    current_task_key,
    get_effort_policy,
    invoke_with_effort,
)
from aurora.agent.safety import build_gate
from aurora.agent.store.database import dumps, now, uid
from aurora.agent.store.model import AgentRun, Artifact
from aurora.agent.tools.base import Tool
from aurora.common.cancellation import OperationCancelled, check_cancelled

from .graph_checkpoint import checkpoint_saver, pending_interrupt
from .model_usage import MeasuredPlanner, invoke_with_usage, read_usage
from .task_events import publish_task, start_task
from .task_execution import read_only_delegates, record_plan, record_result, wait_for_input


def execute_delegate(session, run_id, state, task, configs, progress):
    """桥接子图中断，并缓存已经完成的执行结果。"""
    check_cancelled()
    records = session.records
    key = state["code_agent_run_id"] + ":" + task["id"]
    children = state.setdefault("children", {})
    args = task["args"]
    if set(args) - {"agent_id", "task", "artifact_ids"}:
        raise ValueError("未知 Agent 调用参数")
    config = next((c for c in configs if c["agent"]["id"] == args.get("agent_id")), None)
    if config is None:
        raise ValueError("Agent 未开放调用或不属于本轮可调用清单")
    goal = args.get("task")
    artifacts = args.get("artifact_ids") or []
    if not isinstance(goal, str) or not goal.strip():
        raise ValueError("子任务说明不能为空")
    if len(goal.encode("utf-8")) > config["model"]["context_budget_tokens"]:
        raise ValueError("子任务说明超过模型上下文预算")
    if not isinstance(artifacts, list) or any(not isinstance(item, str) for item in artifacts):
        raise ValueError("图片产物 ID 必须是字符串列表")
    if artifacts and config["model"]["model_type"] != "multimodal":
        raise ValueError("文本模型不支持图片输入")
    saved = state.get("saved_children", {}).pop(key, None)
    if saved and ("result" in saved or "error" in saved):
        children[key] = saved
    if key not in children:
        depth = state.get("delegation_depth", 0) + 1
        state["task_budget"].check_depth(depth)
        path = (*state.get("role_path", ()), task["id"])
        retry = state.get("retry_nodes", {}).get(task.get("source_task_id"))
        content: list[str | dict] = [{"type": "text", "text": goal}]
        for artifact_id in artifacts:
            artifact = records.get(Artifact, artifact_id)
            if artifact["run_id"] != run_id or artifact["media_type"] not in {
                "image/png",
                "image/jpeg",
                "image/webp",
            }:
                raise ValueError("图片必须是当前运行可访问的图片产物")
            _, data = records.artifact_bytes(artifact_id)
            content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{artifact['media_type']};base64,"
                        + base64.b64encode(data).decode()
                    },
                }
            )
        config, inputs, tools = session._specialize(state, config, goal, task_path=path)
        policy = get_effort_policy(task.get("effort", "medium"))
        config = apply_effort(config, policy.effort)
        inputs["effort_policy"] = config["effort_policy"]
        maximum = min(state["task_budget"].limits.max_plan_tasks, policy.max_steps)
        if retry:
            from .retry_plan import retry_context

            budget = (
                config["model"]["context_budget_tokens"]
                - len(inputs["current"].encode("utf-8"))
                - 128
            )
            inputs["current"] += "\n重试来源（仅供参考）：\n" + retry_context(retry, budget // 2)
            inputs["retry"] = {
                "source_agent_run_id": retry["source_agent_run_id"],
                "retained_task_ids": [item["id"] for item in retry["retained"]],
                "role_path": retry["path"],
                "role_goal": retry["goal"],
                "retained": retry["retained"],
                "serial": retry["serial"],
            }
        content[0] = {"type": "text", "text": inputs["current"]}
        child_id = saved["id"] if saved else uid()
        if not saved:
            records.insert(
                AgentRun,
                id=child_id,
                run_id=run_id,
                agent_id=config["agent"]["id"],
                parent_task_id=state["task_ids"][task["id"]],
                step_key="delegate_" + child_id,
                iteration=state["iteration"],
                status="running",
                input_json=dumps(
                    args
                    | {
                        "capabilities": inputs["capabilities"],
                        "current": inputs["current"],
                        "effort_policy": inputs["effort_policy"],
                    }
                    | {"role_path": list(path), "delegation_depth": depth}
                    | ({"retry": inputs["retry"]} if retry else {})
                ),
                config_snapshot_json=dumps(config),
                created_at=now(),
                updated_at=now(),
            )
        child = {
            "id": child_id,
            "tasks": dict(saved["tasks"]) if saved else {},
            "next": None if saved else {"goal": inputs["current"]},
        }
        if saved:
            child["pending"] = pending_interrupt(saved)
            child["pending_task_id"] = saved.get("pending_task_id")
            child["resolved"] = list(saved.get("resolved", []))
        children[key] = child
        child_state = {
            "snapshot": state["snapshot"],
            "capabilities": state["capabilities"],
            "history": [],
            "artifacts": [],
            "role_path": path,
            "delegation_depth": depth,
            "task_budget": state["task_budget"],
            "cancel_event": state.get("cancel_event"),
            "code_agent_run_id": child_id,
            "agent_run_id": child_id,
            "phase": "delegate_" + child_id,
            "iteration": state["iteration"],
            "children": children,
            "saved_children": state.setdefault("saved_children", {}),
            "retry_nodes": state.get("retry_nodes", {}),
            "task_ids": child["tasks"],
        }
        child["state"] = child_state
        if not saved:
            session._emit(
                run_id, child_state, "stage.started", {"agentId": config["agent"]["id"]}, progress
            )
        try:
            model = session._model(config)
            tools = {
                name: tool
                for name, tool in tools.items()
                if name not in {"list_agents", "call_agent"}
            }
            from aurora.agent.capability.agent_registry import catalog, registry_tools

            candidates = [
                entry
                for entry in state["snapshot"]["agents"]
                if entry["agent"]["id"] != config["agent"]["id"]
            ]
            allowed = config["agent"]["allowed_tools"]
            if candidates:
                tools.update(
                    {
                        name: tool
                        for name, tool in registry_tools(candidates).items()
                        if "*" in allowed or name in allowed
                    }
                )

            def respond(prompt: str) -> str:
                """使用角色模型回答需要推理或图片理解的子任务。"""
                response = invoke_with_usage(
                    records,
                    child_id,
                    current_task_key() or uid(),
                    lambda: invoke_with_effort(
                        model,
                        [
                            SystemMessage(content=config["agent"]["instructions"]),
                            HumanMessage(content=content),
                            HumanMessage(content=prompt),
                        ],
                        current_effort(policy.effort),
                        max_output_tokens=config["effort_policy"]["max_output_tokens"],
                    ),
                )
                check_cancelled()
                return _message_text(response)

            tools["agent_respond"] = Tool(
                "agent_respond", "分析、生成文本或理解所附图片并返回答案", respond
            )
            planner: Any
            if retry:
                from .retry_plan import RetryPlanner

                planner = RetryPlanner(retry)
            else:
                planner = MeasuredPlanner(
                    records, child_id, session.runtime._planner_factory(model, tools)
                )
            if hasattr(planner, "set_max_tasks"):
                planner.set_max_tasks(maximum)
            if hasattr(planner, "set_instructions"):
                instructions = config["agent"]["instructions"]
                if "call_agent" in tools:
                    instructions += "\n本轮可调用的 Agent：\n" + dumps(catalog(candidates))
                instructions += "\n\n执行强度策略：\n" + policy.instructions
                planner.set_instructions(instructions)
            if artifacts and hasattr(planner, "set_images"):
                planner.set_images(content[1:])
            child["planner"] = planner
            mode = state["snapshot"]["approval_mode"]
            safe_delegates = read_only_delegates(session, state)
            child["graph"] = build_delegation_graph(
                planner,
                tools,
                build_gate("interrupt" if mode == "interactive" else mode),
                clarifier=NoClarifier() if retry else session.runtime._clarifier_factory(model),
                max_clarification_rounds=policy.max_steps - 1,
                checkpointer=checkpoint_saver(saved),
                serial=True,
                is_parallel_safe=lambda task: task["args"].get("agent_id") in safe_delegates,
                cancellation=state.get("cancel_event"),
                tool_runner=lambda task, tool: session.runtime.traces.invoke(
                    run_id, child_id, task["id"], tool, task["args"]
                ),
                delegate=lambda nested: execute_delegate(
                    session, run_id, child_state, nested, candidates, progress
                ),
                max_plan_tasks=maximum,
                allow_empty_plan=bool(retry),
                plan_guard=lambda tasks: state["task_budget"].reserve(child_id, tasks),
                task_observer=lambda task: start_task(session, run_id, child["tasks"], task),
            )
        except OperationCancelled:
            raise
        except Exception as exc:
            _failed(session, child, exc)
            raise
    child = children[key]
    if child.get("error"):
        raise ValueError(child["error"])
    if "result" in child:
        return child["result"]
    graph_config = {
        "configurable": {"thread_id": child["id"]},
        "recursion_limit": max(100, 2 * state["task_budget"].limits.max_plan_tasks + 64),
    }

    def drive():
        """在隔离上下文中推进子图并持久化叶子任务。"""
        records.update(AgentRun, child["id"], status="running", updated_at=now())
        value = child.pop("next", None)
        for chunk in child["graph"].stream(value, graph_config, stream_mode="updates"):
            for node, update in chunk.items():
                if not isinstance(update, dict):
                    continue
                if node == "plan":
                    child["tasks"], _ = record_plan(session, child["id"], update["tasks"])
                    child["state"]["task_ids"] = child["tasks"]
                    for task_id in child["tasks"].values():
                        publish_task(session, run_id, task_id, "task.created")
                if node in {"dispatch", "execute"}:
                    for result in update.get("results", []):
                        record_result(session, run_id, child["tasks"], result)
        return child["graph"].get_state(graph_config)

    try:
        for request in child.get("resolved", []):
            interrupt(request)
        while True:
            if child.get("pending"):
                pending = child["pending"]
                request = dict(pending.value)
                request.setdefault("agentRunId", child["id"])
                request.setdefault("agentId", config["agent"]["id"])
                if child.get("pending_task_id"):
                    request.setdefault("taskId", child["pending_task_id"])
                request["delegationInterruptId"] = (
                    child["id"] + ":" + str(len(child.get("resolved", [])))
                )
                response = interrupt(request)
                child.setdefault("resolved", []).append(request)
                child["next"] = Command(resume={pending.id: response})
                child.pop("pending")
                child.pop("pending_task_id", None)
            snapshot = Context().run(drive)
            pending = [item for node in snapshot.tasks for item in node.interrupts]
            if pending:
                child["pending"] = pending[0]
                waiting = wait_for_input(session, run_id, child["id"])
                child["pending_task_id"] = pending[0].value.get("taskId") or (
                    waiting[0] if len(waiting) == 1 else None
                )
                records.update(AgentRun, child["id"], status="waiting", updated_at=now())
                session._emit(run_id, child["state"], "stage.waiting", {}, progress)
                continue
            if any(not result["ok"] for result in snapshot.values.get("results", [])):
                raise ValueError(snapshot.values.get("report", "子 Agent 执行失败"))
            output = session.runtime.redact(snapshot.values.get("report", ""))
            records.update(
                AgentRun,
                child["id"],
                status="completed",
                output=output,
                usage_json=dumps(
                    read_usage(records, child["id"], getattr(child["planner"], "usage", {}))
                ),
                updated_at=now(),
            )
            records.message(
                session.id,
                "assistant",
                output,
                run_id=run_id,
                agent_run_id=child["id"],
                visibility="internal",
            )
            child["result"] = dumps(
                {"agentRunId": child["id"], "status": "completed", "output": output}
            )
            session._emit(run_id, child["state"], "stage.completed", {"output": output}, progress)
            return child["result"]
    except (GraphInterrupt, OperationCancelled):
        raise
    except Exception as exc:
        _failed(session, child, exc)
        session._emit(run_id, child["state"], "stage.failed", {"error": child["error"]}, progress)
        if getattr(exc, "code", None):
            raise
        raise ValueError(
            dumps({"agentRunId": child["id"], "status": "failed", "error": child["error"]})
        ) from None


def _failed(session, child, exc):
    """保存脱敏后的子执行失败信息。"""
    child["error"] = session.runtime.redact(str(exc))
    session.records.update(
        AgentRun, child["id"], status="failed", error=child["error"], updated_at=now()
    )
