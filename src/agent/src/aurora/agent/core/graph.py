"""支持澄清循环、断点和轨迹的委派图。"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from functools import partial
from threading import Event
from typing import Any, Literal, cast

from langgraph.errors import GraphInterrupt
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send, interrupt

from aurora.common.cancellation import OperationCancelled, cancellation_scope, check_cancelled
from aurora.common.logging import get_logger

from ..safety.gate import ConfirmationGate, DenyApprover
from ..tools.base import RiskLevel, Tool
from .effort_policy import effort_scope
from .execution_limits import ExecutionLimitError
from .plan_validation import PlanValidationError, validate_plan
from .planner import Clarifier, NoClarifier, Planner
from .state import DelegationState, Evaluation, Result, Task, TraceEvent

log = get_logger(__name__)


def build_delegation_graph(
    planner: Planner,
    tools: Mapping[str, Tool],
    gate: ConfirmationGate | None = None,
    *,
    clarifier: Clarifier | None = None,
    max_clarification_rounds: int = 3,
    checkpointer: Any = None,
    interrupt_before: Sequence[str] | None = None,
    interrupt_after: Sequence[str] | None = None,
    collect_feedback: bool = False,
    feedback_sink: Callable[[DelegationState], None] | None = None,
    delegate: Callable | None = None,
    serial: bool = False,
    cancellation: Event | None = None,
    tool_runner: Callable | None = None,
    max_plan_tasks: int = 8,
    allow_empty_plan: bool = False,
    plan_guard: Callable | None = None,
    task_observer: Callable | None = None,
    is_parallel_safe: Callable[[Task], bool] | None = None,
):
    """构建并编译支持人工介入的委派图。"""
    if max_clarification_rounds < 0:
        raise ValueError("max_clarification_rounds 不能小于 0")
    gate = gate or ConfirmationGate(DenyApprover())
    clarifier = clarifier or NoClarifier()

    def planning_goal(state: DelegationState) -> str:
        """把已有澄清合并为下一轮规划输入。"""
        clarifications = state.get("clarifications", [])
        if not clarifications:
            return state["goal"]
        context = "\n".join(
            f"问题：{item['question']}\n回答：{item['answer']}" for item in clarifications
        )
        return f"{state['goal']}\n\n用户补充信息：\n{context}"

    def plan_node(state: DelegationState) -> dict:
        """根据目标和已有澄清生成任务。"""
        tasks = validate_plan(
            planner.plan(planning_goal(state)),
            tools,
            max_tasks=max_plan_tasks,
            allow_empty=allow_empty_plan,
        )
        if plan_guard:
            plan_guard(tasks)
        log.info("规划完成，共 %d 个子任务", len(tasks))
        for task in tasks:
            log.info(
                "  [%s] %s → %s",
                task["effort"].value,
                task["description"],
                task["tool"],
            )
        return {
            "tasks": tasks,
            "trace": [{"node": "plan", "detail": f"生成 {len(tasks)} 个任务"}],
        }

    def clarify_node(state: DelegationState) -> dict:
        """判断是否需要暂停并询问用户。"""
        count = state.get("clarification_count", 0)
        if count >= max_clarification_rounds:
            return {
                "clarification_needed": False,
                "trace": [{"node": "clarify", "detail": "达到澄清轮数上限"}],
            }
        decision = clarifier.assess(
            state["goal"],
            state["tasks"],
            list(state.get("clarifications", [])),
        )
        needed = decision.needed and bool(decision.question)
        detail = decision.reason or ("需要用户澄清" if needed else "计划信息充分")
        return {
            "clarification_needed": needed,
            "clarification_question": decision.question,
            "trace": [{"node": "clarify", "detail": detail}],
        }

    def route_after_clarify(state: DelegationState) -> Literal["ask_user", "dispatch"]:
        """按澄清判断选择人工门或任务派发。"""
        return "ask_user" if state.get("clarification_needed", False) else "dispatch"

    def ask_user_node(state: DelegationState) -> dict:
        """暂停图执行并接收用户补充信息。"""
        question = state.get("clarification_question", "")
        if not question:
            raise ValueError("澄清问题不能为空")
        answer = interrupt(
            {
                "kind": "clarification",
                "question": question,
                "round": state.get("clarification_count", 0) + 1,
            }
        )
        history = [
            *state.get("clarifications", []),
            {"question": question, "answer": str(answer)},
        ]
        return {
            "clarifications": history,
            "clarification_count": state.get("clarification_count", 0) + 1,
            "trace": [{"node": "ask_user", "detail": question}],
        }

    def has_dependencies(state: DelegationState) -> bool:
        """区分显式依赖计划与旧计划的派发约定。"""
        return any("depends_on" in task for task in state["tasks"])

    def parallel_safe(task: Task) -> bool:
        """允许只读叶子及调用方确认的只读委派共享工作区。"""
        if tools[task["tool"]].risk != RiskLevel.READ:
            return False
        if task["tool"] == "call_agent":
            return is_parallel_safe is not None and is_parallel_safe(task)
        return True

    def dispatch(state: DelegationState) -> list[Send] | str:
        """派发前置均成功的任务，具有副作用的任务独占一波。"""
        if not state["tasks"]:
            return "summarize"
        if has_dependencies(state):
            results = {result["task_id"]: result for result in state.get("results", [])}
            if len(results) == len(state["tasks"]):
                return "summarize"
            ready = [
                task
                for task in state["tasks"]
                if task["id"] not in results
                and all(dep in results and results[dep]["ok"] for dep in task.get("depends_on", []))
            ]
            if not ready:
                raise PlanValidationError(
                    "INVALID_DEPENDENCY_GRAPH", "剩余任务缺少可执行的前置路径"
                )
            wave = []
            for task in ready:
                if not parallel_safe(task):
                    if not wave:
                        wave.append(task)
                    break
                wave.append(task)
            return [Send("execute", {"current_task": task}) for task in wave]
        if serial:
            return "execute"
        return [Send("execute", {"current_task": task}) for task in state["tasks"]]

    def dispatch_node(state: DelegationState) -> dict:
        """在派发前收束因前置失败而无法执行的任务。"""
        if not has_dependencies(state):
            return {}
        outcomes = {result["task_id"]: result["ok"] for result in state.get("results", [])}
        blocked: list[Result] = []
        while True:
            previous = len(blocked)
            for task in state["tasks"]:
                deps = task.get("depends_on", [])
                if task["id"] in outcomes:
                    continue
                failed = [dep for dep in deps if outcomes.get(dep) is False]
                if failed:
                    blocked.append(
                        {
                            "task_id": task["id"],
                            "ok": False,
                            "blocked_by": failed,
                            "output": "前置任务失败，未执行：" + "、".join(failed),
                        }
                    )
                    outcomes[task["id"]] = False
            if len(blocked) == previous:
                return {"results": blocked} if blocked else {}

    def after_execute(state: DelegationState) -> Literal["dispatch", "execute", "summarize"]:
        """依赖计划在本波结束后重算就绪任务，旧计划遵循原串并行模式。"""
        if has_dependencies(state):
            return "dispatch"
        results = state.get("results", [])
        if (
            serial
            and len(results) < len(state["tasks"])
            and all(result["ok"] for result in results)
        ):
            return "execute"
        return "summarize"

    def execute_node(state: DelegationState) -> dict:
        """通过确认门执行一个叶子任务。"""
        task = state.get("current_task")
        if task is None and serial:
            task = state["tasks"][len(state.get("results", []))]
        if task is None:
            raise ValueError("当前任务不能为空")
        task_id = task_observer(task) if task_observer else None
        try:
            tool = tools.get(task["tool"])
            if tool is None:
                raise ValueError("工具不存在或未获当前角色授权: " + task["tool"])
            with effort_scope(task["effort"], task_key=task["id"]):
                output = (
                    delegate(task)
                    if delegate and tool.name == "call_agent"
                    else gate.invoke(
                        tool,
                        task["args"],
                        run_tool=partial(tool_runner, task, tool) if tool_runner else None,
                        **({"task_id": task_id} if isinstance(task_id, str) else {}),
                    )
                )
            ok = True
            log.info("[%s] ✓ %s", task["effort"].value, task["description"])
        except (GraphInterrupt, OperationCancelled, PlanValidationError, ExecutionLimitError):
            raise
        except Exception as exc:
            output = f"执行失败: {exc}"
            ok = False
            log.warning("[%s] ✗ %s：%s", task["effort"].value, task["description"], exc)

        result: Result = {"task_id": task["id"], "ok": ok, "output": output}
        return {
            "results": [result],
            "trace": [{"node": "execute", "detail": f"{task['id']}:{'ok' if ok else 'failed'}"}],
        }

    def summarize_node(state: DelegationState) -> dict:
        """按原任务顺序汇总并行结果。"""
        by_id = {result["task_id"]: result for result in state.get("results", [])}
        parts: list[str] = []
        for index, task in enumerate(state["tasks"], 1):
            result = by_id.get(task["id"])
            if result is None:
                parts.append(
                    f"{index}) [{task['effort'].value}] {task['description']} ✗\n   未产生结果"
                )
                continue
            mark = "✓" if result["ok"] else "✗"
            output = result["output"]
            if task["tool"] == "call_agent":
                try:
                    delegated = json.loads(output.removeprefix("执行失败: "))
                    text = delegated.get("output" if result["ok"] else "error")
                    if delegated.get("agentRunId") and isinstance(text, str):
                        output = text
                except (ValueError, TypeError, AttributeError):
                    pass
            parts.append(
                f"{index}) [{task['effort'].value}] {task['description']} {mark}\n   {output}"
            )
        return {
            "report": "\n\n".join(parts),
            "trace": [{"node": "summarize", "detail": f"汇总 {len(by_id)} 个结果"}],
        }

    def feedback_node(state: DelegationState) -> dict:
        """暂停图执行并收集用户评分。"""
        value = interrupt(
            {
                "kind": "evaluation",
                "question": "请为本次结果评分（1-5），可附带 comment。",
                "report": state["report"],
                "trace": state.get("trace", []),
            }
        )
        if isinstance(value, Mapping):
            score = int(value.get("score", 0))
            comment = str(value.get("comment", ""))
        else:
            score = int(value)
            comment = ""
        if score not in range(1, 6):
            raise ValueError("评分必须是 1 到 5")
        evaluation: Evaluation = {"score": score, "comment": comment}
        event: TraceEvent = {"node": "feedback", "detail": f"用户评分 {score}"}
        if feedback_sink is not None:
            updated = cast(
                DelegationState,
                {**state, "evaluation": evaluation, "trace": [*state.get("trace", []), event]},
            )
            feedback_sink(updated)
        return {
            "evaluation": evaluation,
            "trace": [event],
        }

    def cancellable(node):
        """为每个节点绑定同一信号，隔离子线程也能回收工具进程。"""

        def run(state):
            with cancellation_scope(cancellation):
                check_cancelled()
                result = node(state)
                check_cancelled()
                return result

        return run

    builder = StateGraph(DelegationState)
    builder.add_node("plan", cancellable(plan_node))
    builder.add_node("clarify", cancellable(clarify_node))
    builder.add_node("ask_user", cancellable(ask_user_node))
    builder.add_node("dispatch", cancellable(dispatch_node))
    builder.add_node("execute", cancellable(execute_node))
    builder.add_node("summarize", cancellable(summarize_node))
    if collect_feedback:
        builder.add_node("feedback", cancellable(feedback_node))

    builder.add_edge(START, "plan")
    builder.add_edge("plan", "clarify")
    builder.add_conditional_edges("clarify", route_after_clarify)
    builder.add_edge("ask_user", "plan")
    builder.add_conditional_edges("dispatch", dispatch)
    builder.add_conditional_edges("execute", after_execute)
    builder.add_edge("summarize", "feedback" if collect_feedback else END)
    if collect_feedback:
        builder.add_edge("feedback", END)

    return builder.compile(
        checkpointer=checkpointer,
        interrupt_before=list(interrupt_before) if interrupt_before else None,
        interrupt_after=list(interrupt_after) if interrupt_after else None,
    ).with_config(recursion_limit=max(100, 2 * max_plan_tasks + 2 * max_clarification_rounds + 16))
