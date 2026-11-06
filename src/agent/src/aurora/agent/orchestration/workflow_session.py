"""管理工作流会话，执行并持久化编写、审查及交接阶段。"""

from __future__ import annotations

import base64
import json
import threading
from collections.abc import Mapping
from typing import Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.types import Command
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy import update as orm_update

from aurora.agent.capability import CapabilityContext
from aurora.agent.conversation.session import SYSTEM_PROMPT, _message_text
from aurora.agent.core import NoClarifier, TaskBudget, build_delegation_graph, validate_plan
from aurora.agent.core.effort_policy import apply_effort, get_effort_policy, limit_model_output
from aurora.agent.model_access.client_factory import build_configured_llm
from aurora.agent.orchestration.model_usage import MeasuredPlanner, read_usage
from aurora.agent.orchestration.runtime import (
    AgentSession,
    RunControlError,
    RunExecutionError,
    RuntimeInterrupt,
    RunUpdate,
)
from aurora.agent.orchestration.task_events import start_task
from aurora.agent.orchestration.task_execution import (
    read_only_delegates,
    record_plan,
    record_result,
    wait_for_input,
)
from aurora.agent.preview.browser import validate_preview
from aurora.agent.safety import build_gate
from aurora.agent.store.database import dumps, now, uid
from aurora.agent.store.model import (
    AgentRun,
    ConversationSession,
    Interaction,
    Project,
    Review,
    ReviewArtifact,
    ReviewFinding,
    Run,
    Task,
)
from aurora.agent.store.records import decode
from aurora.common.cancellation import OperationCancelled, cancellation_scope, check_cancelled


class Finding(BaseModel):
    """结构化视觉问题与截图位置。"""

    severity: Literal["blocking", "suggestion"]
    description: str
    artifact_id: str
    location: str
    suggestion: str
    bbox: list[float] | None = None
    previous_finding_id: str | None = None


class ReviewResult(BaseModel):
    """视觉审查的可验证结论。"""

    verdict: Literal["passed", "changes_requested", "unable_to_review"]
    summary: str
    findings: list[Finding] = Field(default_factory=list)


class WorkflowSession(AgentSession):
    """在同一会话中串行驱动编写、截图、审查与返修。"""

    def __init__(self, runtime, record, workspace, repository):
        self.runtime = runtime
        self.records = runtime.records
        self.record = record
        self.id = record["id"]
        self.workspace = workspace
        self.repository = repository
        self._run_ids = set()
        self._active = {}
        self.feedback_sink: Any = None
        self._lock = threading.Lock()
        self._control_lock = threading.RLock()
        self.sandbox = runtime._sandbox_factory(root=workspace.path, mode=record["sandbox_mode"])

    @property
    def llm(self):
        """构建默认流程编写角色的模型。"""
        return self._model(self._snapshot()["steps"][0]["config"])

    @property
    def planner(self):
        """提供 CLI 兼容规划器。"""
        return self.runtime._planner_factory(self.llm, self._tools({"allowed_tools": ["*"]}))

    def _snapshot(self, workflow_id=None):
        """读取会话当前配置并冻结工作流。"""
        self.record = self.records.get(ConversationSession, self.id)
        result = self.records.snapshot(
            workflow_id or self.record["workflow_id"],
            self.record["sandbox_mode"],
            self.record["approval_mode"],
        )
        from aurora.agent.capability.agent_registry import AgentRegistry

        result["agents"] = AgentRegistry(self.runtime).snapshot()
        result["preview"] = self.records.get(Project, self.record["project_id"])["preview"]
        result["execution_limits"] = self.runtime.execution_limits.to_dict()
        return result

    def _model(self, config):
        """为阶段创建独立的模型客户端。"""
        if self.runtime._configured_llm_factory:
            model = self.runtime._configured_llm_factory(config)
        elif self.runtime._custom_llm:
            model = self.runtime._llm_factory()
        else:
            model = build_configured_llm(config)
        policy = config.get("effort_policy")
        return limit_model_output(model, policy["max_output_tokens"]) if policy else model

    def _tools(self, agent):
        """按角色权限过滤当前运行时工具。"""
        from aurora.agent.store import build_git_tools
        from aurora.agent.tools import build_sandbox_tools, get_available_tools

        tools = get_available_tools()
        tools.update(build_sandbox_tools(self.sandbox))
        tools.update(build_git_tools(self.repository))
        for server_tools in self.runtime._mcp_tools.values():
            tools.update(server_tools)
        allowed = agent["allowed_tools"]
        return {key: value for key, value in tools.items() if "*" in allowed or key in allowed}

    def start(
        self,
        goal,
        on_progress=None,
        *,
        workflow_id=None,
        request_key=None,
        mode="run",
        _retry_source=None,
        _retry_task=None,
        _on_created=None,
    ):
        """原子登记一轮请求并开始执行，重复请求返回原记录。"""
        from aurora.common.text import sanitize_text

        goal = sanitize_text(goal).strip()
        if not goal or mode not in {"run", "say", "plan"}:
            raise ValueError("目标不能为空且运行模式必须有效")
        key = request_key or uid()
        existing = self.records.db.first(
            select(Run.id).where(Run.session_id == self.id, Run.request_key == key)
        )
        if existing:
            return self._result(existing["id"])
        if not self._lock.acquire(blocking=False):
            if _retry_source:
                raise RunControlError("RUN_ALREADY_ACTIVE", "会话正在处理请求")
            raise ValueError("会话正在处理请求")
        state = None
        try:
            with self._control_lock, self.records.db.transaction():
                existing = self.records.db.first(
                    select(Run.id).where(Run.session_id == self.id, Run.request_key == key)
                )
                if existing:
                    return self._result(existing["id"])
                try:
                    self.records.require_idle(self.id)
                except ValueError as exc:
                    if _retry_source:
                        raise RunControlError("RUN_ALREADY_ACTIVE", str(exc)) from None
                    raise
                if _retry_source:
                    from .retry_plan import build_retry

                    self.record = self.records.get(ConversationSession, self.id)
                    goal, mode, snapshot = build_retry(self, _retry_source, _retry_task)
                else:
                    snapshot = self._snapshot(workflow_id)
                limits = self.runtime.execution_limits
                snapshot["execution_limits"] = limits.to_dict()
                if snapshot["sandbox_mode"] not in {
                    "read-only",
                    "workspace-write",
                    "danger-full-access",
                } or snapshot["approval_mode"] not in {"interactive", "always", "never"}:
                    raise ValueError("会话执行策略无效")
                if self.sandbox.mode != snapshot["sandbox_mode"]:
                    self.sandbox = self.runtime._sandbox_factory(
                        root=self.workspace.path, mode=snapshot["sandbox_mode"]
                    )
                config = snapshot["steps"][0]["config"]
                history = self.records.context(
                    self.id, config["model"]["context_budget_tokens"], goal
                )
                if _retry_source:
                    history = snapshot["retry"].get("history", [])
                run_id = uid()
                self.records.insert(
                    Run,
                    id=run_id,
                    session_id=self.id,
                    request_key=key,
                    goal=goal,
                    mode=mode,
                    status="running",
                    config_snapshot_json=dumps(snapshot),
                    created_at=now(),
                    updated_at=now(),
                    retry_of_run_id=_retry_source,
                    retry_task_id=_retry_task,
                )
                self.records.message(self.id, "user", goal, run_id=run_id)
                self.records.update(ConversationSession, self.id, updated_at=now())
                self._run_ids.add(run_id)
                state = {
                    "snapshot": snapshot,
                    "history": history,
                    "goal": goal,
                    "mode": mode,
                    "iteration": 0,
                    "phase": "code",
                    "handoff": "",
                    "tasks": [],
                    "results": [],
                    "capabilities": self.runtime.capabilities.freeze(),
                    "cancel_event": threading.Event(),
                    "executing": True,
                    "task_budget": TaskBudget(limits),
                    "role_path": (),
                    "delegation_depth": 0,
                }
                self._active[run_id] = state
            return self._execute(run_id, state, on_progress, started=True, on_created=_on_created)
        finally:
            self._lock.release()
            if state:
                self._notify_timeout(state, run_id)

    def retry(self, source_id, task_id=None, on_progress=None, on_created=None):
        """创建新的来源关联运行，旧检查点和历史任务保持不可变。"""
        source = self.runtime.require_run(self.id, source_id)
        if source["status"] in {"queued", "running", "waiting"}:
            raise RunControlError("RUN_NOT_RETRYABLE", "来源运行仍在活动，请先停止")
        try:
            return self.start(
                "重试",
                on_progress,
                _retry_source=source_id,
                _retry_task=task_id,
                _on_created=on_created,
            )
        except RunControlError:
            raise
        except ValueError as exc:
            raise RunControlError("RUN_NOT_RETRYABLE", str(exc)) from None

    def resume(
        self,
        run_id,
        response,
        interrupt_id=None,
        on_progress=None,
        *,
        approval_id=None,
        on_accepted=None,
    ):
        """恢复本进程持有的阶段或审批，不重放已完成阶段。"""
        if not self._lock.acquire(blocking=False):
            if approval_id is not None:
                raise RunControlError("RUN_BUSY", "会话正在处理请求")
            raise ValueError("会话正在处理请求")
        state = None
        worktree = None
        try:
            with self._control_lock:
                run = self.records.get(Run, run_id)
                if run["session_id"] != self.id:
                    raise ValueError("运行不属于当前会话")
                state = self._active.get(run_id)
                self.expire_approvals(run_id)
                if approval_id is not None:
                    approval = self.records.db.first(
                        select(Interaction).where(
                            Interaction.id == approval_id,
                            Interaction.run_id == run_id,
                            Interaction.kind == "approval",
                        )
                    )
                    if approval is None:
                        raise RunControlError("APPROVAL_NOT_FOUND", "审批不存在或不属于当前运行")
                    if approval["status"] != "pending":
                        code = (
                            "APPROVAL_EXPIRED"
                            if approval["status"] == "expired"
                            else "APPROVAL_ALREADY_RESOLVED"
                        )
                        raise RunControlError(
                            code, "审批已过期" if code == "APPROVAL_EXPIRED" else "审批已解决"
                        )
                    interrupt_id = approval["interrupt_id"]
                run = self.records.get(Run, run_id)
                if run["status"] != "waiting" or run_id not in self._active:
                    raise ValueError("运行已结束或中断，不能恢复旧审批")
                state = self._active[run_id]
                if state.get("restored_workspace") and not interrupt_id:
                    raise RunControlError("CHECKPOINT_INPUT_REQUIRED", "请使用重启后换发的输入标识")
                pending = self.records.db.rows(
                    select(Interaction).where(
                        Interaction.run_id == run_id, Interaction.status == "pending"
                    )
                )
                if interrupt_id is None and len(pending) != 1:
                    raise ValueError("必须指定 interruptId")
                selected = next(
                    (
                        item
                        for item in pending
                        if interrupt_id is None or item["interrupt_id"] == interrupt_id
                    ),
                    None,
                )
                if selected is None:
                    raise ValueError("旧审批已失效、已处理或不存在")
                worktree = self.runtime.reserve_worktree(self.repository.root)
                if worktree is None:
                    raise RunControlError("RUN_BUSY", "工作区正在被其他会话使用")
                try:
                    self._verify_waiting_workspace(state)
                except (OSError, ValueError, RuntimeError) as exc:
                    self._finish(run_id, state, "interrupted", "恢复条件已变化，请重试")
                    raise RunControlError("CHECKPOINT_STALE", "恢复条件已变化，请重试") from exc
                if selected["kind"] == "approval":
                    approved = (
                        response.get("approved") if isinstance(response, Mapping) else response
                    )
                    if not isinstance(approved, bool):
                        raise ValueError("approved 必须是布尔值")
                    response = {"approved": approved}
                if state.get("manual") == "preview":
                    if isinstance(response, str):
                        response = json.loads(response)
                    config = validate_preview(response, self.sandbox)
                    state["snapshot"]["preview"] = config
                    self.records.update(
                        Project,
                        self.record["project_id"],
                        preview_json=dumps(config),
                        updated_at=now(),
                    )
                    self.records.update(Run, run_id, config_snapshot_json=dumps(state["snapshot"]))
                elif state.get("manual") == "capture_approval":
                    if not isinstance(response, Mapping) or response.get("approved") is not True:
                        state["cancelled"] = True
                    else:
                        state["capture_approved"] = True
                elif state.get("manual") in {"review_limit", "review_unavailable"}:
                    action = response.get("action") if isinstance(response, Mapping) else response
                    if action not in {"accept", "cancel", "retry"}:
                        raise ValueError("请选择 accept、cancel 或 retry")
                    if action == "accept":
                        state["phase"] = "summary"
                        state["accepted_with_issues"] = True
                    elif action == "cancel":
                        state["cancelled"] = True
                    elif state["manual"] == "review_limit":
                        state["iteration"] += 1
                        state["phase"] = "code"
                    else:
                        state["review_attempt"] = state.get("review_attempt", 0) + 1
                        state["phase"] = "capture"
                else:
                    graph_id = state.get("interrupt_ids", {}).get(
                        selected["interrupt_id"], selected["interrupt_id"]
                    )
                    state["resume"] = Command(resume={graph_id: response})
                with self.records.db.transaction():
                    resolved = (
                        [self.runtime.approvals.resolve(selected, response["approved"])]
                        if selected["kind"] == "approval"
                        else []
                    )
                    if not resolved:
                        self.records.update(
                            Interaction,
                            selected["id"],
                            response_json=dumps(response),
                            status="resolved",
                            updated_at=now(),
                        )
                    if selected["kind"] == "clarification":
                        self.records.message(
                            self.id, "user", "补充信息：" + dumps(response), run_id=run_id
                        )
                    self.records.update(Run, run_id, status="running", updated_at=now())
                    self.records.delete_checkpoint(run_id)
                state.pop("manual", None)
                state["executing"] = True
            self.runtime.approvals.publish(resolved)
            self.runtime.approvals.arm(self, run_id)
            if on_accepted:
                try:
                    on_accepted()
                except (ConnectionError, OSError):
                    pass
            return self._execute(run_id, state, on_progress, reserved_worktree=worktree)
        except RunControlError as exc:
            if exc.code == "APPROVAL_EXPIRED":
                self.expire_approvals(run_id)
            raise
        finally:
            if worktree is not None:
                self.runtime.release_worktree(worktree)
            self._lock.release()
            if state:
                self._notify_timeout(state, run_id)

    def git_rollback(self, run_id):
        """独占工作树后复核当前状态并回滚本轮改动。"""
        worktree = self.runtime.reserve_worktree(self.repository.root)
        if worktree is None:
            raise RunControlError("RUN_BUSY", "工作区正在被其他会话使用")
        try:
            return super().git_rollback(run_id)
        finally:
            self.runtime.release_worktree(worktree)

    def _verify_waiting_workspace(self, state):
        """核对上次暂停时的工作树，重启后同时校验冻结策略。"""
        if state.get("restored_workspace"):
            self.runtime.checkpoints.verify_resume(self, state)
        elif (
            state.get("waiting_workspace")
            and self.repository.capture() != state["waiting_workspace"]
        ):
            raise ValueError("等待期间工作区已变化")

    def _execute(
        self, run_id, state, progress, started=False, on_created=None, *, reserved_worktree=None
    ):
        """隔离工作区写入并在异常时提交失败状态。"""
        worktree = reserved_worktree or self.runtime.reserve_worktree(self.repository.root)
        failure = None
        failure_code = None
        result = None
        try:
            with cancellation_scope(state["cancel_event"]):
                if state.get("cancelled"):
                    state["cancel_event"].set()
                check_cancelled()
                if on_created:
                    try:
                        on_created(run_id)
                    except (ConnectionError, OSError):
                        pass
                    check_cancelled()
                if worktree is None:
                    raise ValueError("工作区正在被其他会话使用")
                if started:
                    self.repository.begin_run(run_id)
                self._emit(
                    run_id,
                    state,
                    "run.started" if started else "run.resumed",
                    {
                        "goal": state["goal"],
                        "retry_of_run_id": state["snapshot"].get("retry", {}).get("source_run_id"),
                        "retry_task_id": state["snapshot"].get("retry", {}).get("task_id"),
                    },
                    progress,
                )
                if started and state["snapshot"].get("retry"):
                    from .retry_plan import prepare_retry

                    prepare_retry(self, run_id, state)
                if state.get("restored_workspace") or state.get("waiting_workspace"):
                    try:
                        self._verify_waiting_workspace(state)
                    except (OSError, ValueError, RuntimeError):
                        state["error_code"] = "CHECKPOINT_STALE"
                        return self._finish(run_id, state, "interrupted", "恢复条件已变化，请重试")
                    state.pop("restored_workspace", None)
                    state.pop("waiting_workspace", None)
                result = self._drive(run_id, state, progress)
                check_cancelled()
        except OperationCancelled:
            result = self._finish(run_id, state, "cancelled", "用户取消了运行")
        except Exception as exc:
            failure = self.runtime.redact(str(exc))
            failure_code = getattr(exc, "code", None)
            if failure_code:
                state["error_code"] = failure_code
            result = self._finish(run_id, state, "failed", failure)
        finally:
            if reserved_worktree is None and worktree is not None:
                self.runtime.release_worktree(worktree)
            with self._control_lock:
                state["executing"] = False
        with self._control_lock:
            if state["cancel_event"].is_set():
                return self._finish(run_id, state, "cancelled", "用户取消了运行")
            if failure:
                if failure_code:
                    raise RunExecutionError(failure_code, failure) from None
                raise RuntimeError(failure) from None
            if result and result.status == "waiting":
                self.expire_approvals(run_id)
                self.runtime.approvals.arm(self, run_id)
                result = self._result(run_id)
            return result

    def expire_approvals(self, run_id):
        """到期拒绝整轮未解决操作，活动执行先收束到安全点。"""
        with self._control_lock:
            state = self._active.get(run_id)
            if state is None or state["cancel_event"].is_set():
                return False
            due = self.records.db.first(
                select(Interaction.id).where(
                    Interaction.run_id == run_id,
                    Interaction.kind == "approval",
                    Interaction.status == "pending",
                    Interaction.expires_at <= self.runtime.approvals.clock().isoformat(),
                )
            )
            if not due:
                return False
            state["stop_status"] = "failed"
            state["stop_report"] = "审批超时，操作已拒绝；请重新发起任务"
            state["approval_timeout_notification"] = True
            state["cancel_event"].set()
            if not state["executing"]:
                self._finish(run_id, state, "failed", state["stop_report"])
        self._notify_timeout(state, run_id)
        return True

    def cancel(self, run_id):
        """请求协作取消，等待中的运行立即收束，活动执行在安全点结束。"""
        with self._control_lock:
            run = self.records.get(Run, run_id)
            if run["session_id"] != self.id:
                raise ValueError("运行不属于当前会话")
            if run["status"] not in {"queued", "running", "waiting"}:
                return run["status"] == "cancelled", None
            state = self._active.get(run_id)
            if state is None:
                raise ValueError("当前进程未持有该运行，不能继续操作")
            state["cancel_event"].set()
            if state["executing"]:
                return True, None
            return True, self._finish(run_id, state, "cancelled", "用户取消了运行")

    def _emit(self, run_id, state, name, value, progress):
        """先持久化事件再通知协议层。"""
        check_cancelled()
        payload = json.loads(self.runtime.redact(dumps(dict(value))))
        if state.get("agent_run_id"):
            payload |= {
                "agentRunId": state["agent_run_id"],
                "stepKey": state["phase"],
                "iteration": state["iteration"],
            }
        self.records.event(run_id, name, payload)
        if progress:
            try:
                progress(run_id, name, payload)
            except (ConnectionError, OSError):
                pass

    def _drive(self, run_id, state, progress):
        """驱动预设流程直到完成或等待用户。"""
        while True:
            check_cancelled()
            phase = state["phase"]
            if phase == "code":
                if not self._code(run_id, state, progress):
                    return self._result(run_id)
                if state["mode"] != "run" or state["snapshot"]["workflow"]["kind"] == "single":
                    result = self._finish(run_id, state, "completed", state["report"])
                    if result.status == "completed":
                        self._emit(
                            run_id, state, "summarize", {"report": state["report"]}, progress
                        )
                    return result
                state["phase"] = "capture"
            elif phase == "capture":
                preview = state["snapshot"]["preview"]
                if not preview.get("url"):
                    return self._wait(
                        run_id,
                        state,
                        "preview",
                        "clarification",
                        {
                            "question": "请设置本项目的预览地址、启动方式和页面。",
                            "previewRequired": True,
                        },
                    )
                if not state.get("capture_approved"):
                    if state["snapshot"]["approval_mode"] == "never":
                        return self._unable(
                            run_id, state, "当前审批策略不允许启动浏览器预览", progress
                        )
                    if state["snapshot"]["approval_mode"] == "interactive":
                        return self._wait(
                            run_id,
                            state,
                            "capture_approval",
                            "approval",
                            {"tool": "browser_preview", "risk": "execute", "args": preview},
                        )
                    state["capture_approved"] = True
                self._emit(run_id, state, "stage.started", {"stepKey": "capture"}, progress)
                try:
                    if self.repository.capture().tree != state["code_tree"]:
                        raise ValueError("工作区已发生变化，请重新运行编写阶段后审查")
                    shots, log = self.runtime.capture.capture(preview, self.sandbox)
                    check_cancelled()
                    if self.repository.capture().tree != state["code_tree"]:
                        raise ValueError("截图期间工作区发生变化，证据已失效")
                    if not shots:
                        raise ValueError("截图为空")
                    artifacts = []
                    status = self.repository.status("run", run_id)
                    diff = "\n".join(
                        self.repository.diff(item["path"], "run", run_id)["content"]
                        for item in status["files"]
                    )
                    code_diff = self.records.artifact(
                        run_id,
                        state["code_agent_run_id"],
                        "code_diff",
                        diff.encode(),
                        "text/plain",
                        {},
                    )
                    self.records.artifact(
                        run_id,
                        state["code_agent_run_id"],
                        "preview_log",
                        self.runtime.redact(log).encode(),
                        "text/plain",
                        {},
                    )
                    for data, metadata in shots:
                        artifact = self.records.artifact(
                            run_id,
                            state["code_agent_run_id"],
                            "screenshot",
                            data,
                            "image/png",
                            metadata
                            | {
                                "code_agent_run_id": state["code_agent_run_id"],
                                "diff_artifact_id": code_diff["id"],
                                "captured_at": now(),
                            },
                        )
                        artifacts.append(artifact["id"])
                        self._emit(
                            run_id,
                            state,
                            "artifact.created",
                            {"artifactId": artifact["id"]},
                            progress,
                        )
                    state["artifacts"] = artifacts
                    state["phase"] = "review"
                except OperationCancelled:
                    raise
                except Exception as exc:
                    return self._unable(run_id, state, self.runtime.redact(str(exc)), progress)
            elif phase == "review":
                try:
                    review = self._review(run_id, state, progress)
                except OperationCancelled:
                    raise
                except Exception as exc:
                    return self._unable(run_id, state, self.runtime.redact(str(exc)), progress)
                state["handoff"] = dumps(review)
                if review["verdict"] == "passed":
                    state["phase"] = "summary"
                elif review["verdict"] == "unable_to_review":
                    return self._wait(
                        run_id,
                        state,
                        "review_unavailable",
                        "decision",
                        {"question": review["summary"], "actions": ["retry", "accept", "cancel"]},
                    )
                elif state["iteration"] >= state["snapshot"]["workflow"]["max_revisions"]:
                    return self._wait(
                        run_id,
                        state,
                        "review_limit",
                        "decision",
                        {
                            "question": "已达到自动返修上限，是否接受、取消或再返修一轮？",
                            "actions": ["accept", "cancel", "retry"],
                        },
                    )
                else:
                    state["iteration"] += 1
                    state["phase"] = "code"
            elif phase == "summary":
                config = self._step(state, "summary")["config"]
                config, inputs, _ = self._specialize(
                    state,
                    config,
                    "根据编写结果和最新审查记录汇总实际完成情况。\n"
                    + state["report"]
                    + "\n"
                    + state["handoff"],
                    include_tools=False,
                )
                agent_run = self._begin_agent(run_id, state, config, inputs, progress)
                response = self._model(config).invoke(self._messages(config, inputs))
                report = _message_text(response)
                if state.get("accepted_with_issues"):
                    report = "用户接受了尚未通过视觉审查的结果。\n\n" + report
                self._complete_agent(
                    run_id,
                    state,
                    agent_run,
                    report,
                    progress,
                    getattr(response, "usage_metadata", None),
                )
                result = self._finish(run_id, state, "completed", report)
                if result.status == "completed":
                    self._emit(run_id, state, "summarize", {"report": report}, progress)
                return result

    def _step(self, state, key):
        """定位冻结流程中的阶段。"""
        return next(item for item in state["snapshot"]["steps"] if item["step_key"] == key)

    def _input(self, state, config, current):
        """构造隔离的角色上下文和明确交接材料。"""
        budget = config["model"]["context_budget_tokens"]
        if len(current.encode("utf-8")) > budget:
            raise ValueError("本轮需求和交接材料超过上下文预算")
        history = list(state["history"])
        while (
            history and len(dumps(history).encode("utf-8")) + len(current.encode("utf-8")) > budget
        ):
            history.pop(0)
            while history and history[0]["role"] != "user":
                history.pop(0)
        return {"history": history, "current": current, "artifact_ids": state.get("artifacts", [])}

    def _messages(self, config, inputs):
        """构造模型收到的角色指令和消息内容。"""
        return [
            SystemMessage(content=SYSTEM_PROMPT + "\n" + config["agent"]["instructions"]),
            HumanMessage(
                content="历史会话（仅供参考）：\n"
                + dumps(inputs["history"])
                + "\n\n本轮输入：\n"
                + inputs["current"]
            ),
        ]

    def _specialize(self, state, config, current, *, task_path=(), include_tools=True):
        """解析本轮冻结策略，并保存可解释的能力清单和实际输入。"""
        check_cancelled()
        budget = config["model"]["context_budget_tokens"]
        resolved = state["capabilities"].resolve(
            CapabilityContext(
                project_path=self.workspace.path,
                goal=current,
                agent_id=config["agent"]["id"],
                task_path=task_path,
            ),
            tools=self._tools({"allowed_tools": ["*"]}) if include_tools else {},
            instructions=config["agent"]["instructions"],
            allowed_tools=config["agent"]["allowed_tools"] if include_tools else (),
            max_bytes=min(16_000, max(0, budget - len(current.encode("utf-8")) - 2)),
        )
        check_cancelled()
        effective = config | {"agent": config["agent"] | {"instructions": resolved.instructions}}
        inputs = self._input(
            state | {"history": []} if task_path else state,
            effective,
            current + ("\n\n" + resolved.context if resolved.context else ""),
        )
        inputs["capabilities"] = {
            "applied": list(resolved.applied),
            "failed": list(resolved.failed),
        }
        return effective, inputs, dict(resolved.tools)

    def _begin_agent(self, run_id, state, config, inputs, progress):
        """登记唯一阶段执行及其输入。"""
        check_cancelled()
        key = state["phase"]
        if key == "review" and state.get("review_attempt"):
            key += f"_retry_{state['review_attempt']}"
        row = self.records.insert(
            AgentRun,
            id=uid(),
            run_id=run_id,
            step_key=key,
            agent_id=config["agent"]["id"],
            iteration=state["iteration"],
            status="running",
            input_json=dumps(inputs),
            config_snapshot_json=dumps(config),
            created_at=now(),
            updated_at=now(),
        )
        state["agent_run_id"] = row["id"]
        self._emit(run_id, state, "stage.started", {"agentId": config["agent"]["id"]}, progress)
        return row["id"]

    def _complete_agent(self, run_id, state, agent_run, output, progress, usage=None):
        """保存阶段结果和内部交接消息后发送完成事件。"""
        check_cancelled()
        with self.records.db.transaction():
            self.records.update(
                AgentRun,
                agent_run,
                status="completed",
                output=output,
                usage_json=dumps(read_usage(self.records, agent_run, usage)),
                updated_at=now(),
            )
            self.records.message(
                self.id,
                "assistant",
                output,
                run_id=run_id,
                agent_run_id=agent_run,
                visibility="internal",
            )
        self._emit(run_id, state, "stage.completed", {"output": output}, progress)

    def _code(self, run_id, state, progress):
        """运行编写角色的工具图并持久化暂停点。"""
        from .graph_checkpoint import checkpoint_saver

        config = self._step(state, "code")["config"]
        if "graph" not in state:
            saved = state.pop("graph_restart", None)
            retry = state.get("retry_root") if state["iteration"] == 0 else None
            current = state["goal"] + (
                "\n请根据审查返修：\n" + state["handoff"] if state["handoff"] else ""
            )
            if retry:
                from .retry_plan import retry_context

                budget = (
                    config["model"]["context_budget_tokens"] - len(current.encode("utf-8")) - 256
                )
                current += "\n重试来源（仅供参考）：\n" + retry_context(retry, budget // 2)
            config, inputs, tools = self._specialize(
                state,
                config,
                current,
                include_tools=state["mode"] != "say",
                task_path=tuple(retry["path"]) if retry else (),
            )
            policy = get_effort_policy(retry["effort"]) if retry and retry.get("effort") else None
            maximum = state["task_budget"].limits.max_plan_tasks
            if policy:
                config = apply_effort(config, policy.effort)
                inputs["effort_policy"] = config["effort_policy"]
                maximum = min(maximum, policy.max_steps)
            if retry:
                state["role_path"] = tuple(retry["path"])
                inputs["retry"] = {
                    "source_agent_run_id": retry["source_agent_run_id"],
                    "retained_task_ids": [item["id"] for item in retry["retained"]],
                    "role_path": retry["path"],
                    "role_goal": retry["goal"],
                    "retained": retry["retained"],
                    "serial": retry["serial"],
                    "effort": retry.get("effort"),
                }
                inputs["artifact_ids"] = retry["artifact_ids"]
            agent_run = (
                saved["id"] if saved else self._begin_agent(run_id, state, config, inputs, progress)
            )
            state["code_agent_run_id"] = agent_run
            llm = self._model(config)
            from aurora.agent.capability.agent_registry import registry_tools

            configs = [
                c for c in state["snapshot"]["agents"] if c["agent"]["id"] != config["agent"]["id"]
            ]
            allowed = config["agent"]["allowed_tools"]
            tools.update(
                {
                    key: value
                    for key, value in registry_tools(configs).items()
                    if "*" in allowed or key in allowed
                }
            )
            planner: Any
            if retry:
                from .retry_plan import RetryPlanner, response_tool

                if any(task["tool"] == "agent_respond" for task in retry["tasks"]):
                    tools["agent_respond"] = response_tool(
                        self, llm, config, inputs, retry["artifact_ids"], run_id, agent_run
                    )
                planner = RetryPlanner(retry)
            else:
                planner = MeasuredPlanner(
                    self.records, agent_run, self.runtime._planner_factory(llm, tools)
                )
            state["planner"] = planner
            if hasattr(planner, "set_max_tasks"):
                planner.set_max_tasks(maximum)
            if hasattr(planner, "set_instructions"):
                from aurora.agent.capability.agent_registry import catalog

                instructions = config["agent"]["instructions"]
                if "call_agent" in tools:
                    instructions += "\n本轮可调用的 Agent：\n" + dumps(catalog(configs))
                if policy:
                    instructions += "\n\n执行强度策略：\n" + policy.instructions
                planner.set_instructions(instructions)
            goal = (
                "历史会话（仅供参考）：\n"
                + dumps(inputs["history"])
                + "\n本轮目标：\n"
                + inputs["current"]
            )
            if state["mode"] == "say":
                response = llm.invoke(self._messages(config, inputs))
                state["report"] = _message_text(response)
                self._complete_agent(
                    run_id,
                    state,
                    agent_run,
                    state["report"],
                    progress,
                    getattr(response, "usage_metadata", None),
                )
                return True
            if state["mode"] == "plan":
                state["tasks"] = validate_plan(
                    planner.plan(goal),
                    tools,
                    max_tasks=maximum,
                )
                state["task_budget"].reserve(agent_run, state["tasks"])
                state["report"] = dumps(state["tasks"])
                self._complete_agent(run_id, state, agent_run, state["report"], progress)
                return True
            safe_delegates = read_only_delegates(self, state)
            state["graph"] = build_delegation_graph(
                planner,
                tools,
                build_gate(
                    "interrupt"
                    if state["snapshot"]["approval_mode"] == "interactive"
                    else state["snapshot"]["approval_mode"]
                ),
                clarifier=NoClarifier() if retry else self.runtime._clarifier_factory(llm),
                max_clarification_rounds=policy.max_steps - 1 if policy else 3,
                checkpointer=checkpoint_saver(saved),
                task_observer=lambda task: start_task(self, run_id, state["task_ids"], task),
                is_parallel_safe=lambda task: task["args"].get("agent_id") in safe_delegates,
                collect_feedback=self.feedback_sink is not None,
                feedback_sink=self.feedback_sink,
                serial=retry["serial"] if retry else "call_agent" in tools,
                delegate=lambda task: self._delegate(run_id, state, task, configs, progress),
                cancellation=state["cancel_event"],
                tool_runner=lambda task, tool: self.runtime.traces.invoke(
                    run_id, agent_run, task["id"], tool, task["args"]
                ),
                max_plan_tasks=maximum,
                allow_empty_plan=bool(retry),
                plan_guard=lambda tasks: state["task_budget"].reserve(agent_run, tasks),
            )
            state["graph_input"] = {"goal": goal}
            state["task_ids"] = dict(saved["tasks"]) if saved else {}
        graph = state["graph"]
        agent_run = state["code_agent_run_id"]
        state["agent_run_id"] = agent_run
        self.records.update(AgentRun, agent_run, status="running", updated_at=now())
        graph_config = {
            "configurable": {"thread_id": agent_run},
            "recursion_limit": max(100, 2 * state["task_budget"].limits.max_plan_tasks + 64),
        }
        value = state.pop("resume", state["graph_input"])
        for chunk in graph.stream(value, graph_config, stream_mode="updates"):
            for node, update in chunk.items():
                if not isinstance(update, Mapping):
                    continue
                if node == "plan":
                    ids, mapped = record_plan(self, agent_run, update["tasks"])
                    state["task_ids"] = ids
                    update = dict(update, tasks=mapped)
                    state["tasks"] = mapped
                if node in {"dispatch", "execute"} and update.get("results"):
                    results = [
                        record_result(self, run_id, state["task_ids"], item)
                        for item in update["results"]
                    ]
                    state["results"].extend(results)
                    update = dict(update, results=results)
                if node != "summarize":
                    self._emit(run_id, state, node, update, progress)
        snapshot = graph.get_state(graph_config)
        interruptions = [item for task in snapshot.tasks for item in task.interrupts]
        if interruptions:
            wait_for_input(self, run_id, agent_run)
            with self.records.db.transaction():
                for item in interruptions:
                    public_id = item.value.get("delegationInterruptId", item.id)
                    state.setdefault("interrupt_ids", {})[public_id] = item.id
                    if not self.records.db.first(
                        select(Interaction.id).where(
                            Interaction.run_id == run_id, Interaction.interrupt_id == public_id
                        )
                    ):
                        self.runtime.approvals.create(
                            run_id,
                            item.value.get("kind", "approval"),
                            item.value,
                            agent_run_id=item.value.get("agentRunId", agent_run),
                            interrupt_id=public_id,
                        )
                self.records.update(AgentRun, agent_run, status="waiting", updated_at=now())
                self.records.update(
                    Run,
                    run_id,
                    status="waiting",
                    updated_at=now(),
                    state_json=dumps(self._public_state(state)),
                )
                state["waiting_workspace"] = self.repository.finish_run(run_id, "running")
                self.runtime.checkpoints.save(self, run_id, state)
            return False
        if any(not result["ok"] for result in snapshot.values.get("results", [])):
            raise ValueError("编写阶段存在失败的工具任务，请检查执行记录")
        state["report"] = self.runtime.redact(snapshot.values.get("report", ""))
        state.pop("graph")
        self.repository.finish_run(run_id, "running")
        state["code_tree"] = self.repository.capture().tree
        self._complete_agent(
            run_id,
            state,
            agent_run,
            state["report"],
            progress,
            getattr(state.get("planner"), "usage", {}),
        )
        return True

    def _delegate(self, run_id, state, task, configs, progress):
        """在父运行内恢复独立子执行，不重建顶层会话。"""
        from aurora.agent.orchestration.delegation import execute_delegate

        return execute_delegate(self, run_id, state, task, configs, progress)

    def _review(self, run_id, state, progress):
        """向视觉模型传递真实图片并验证审查证据。"""
        config = self._step(state, "review")["config"]
        if "image" not in config["model"]["input_types"]:
            raise ValueError("审查模型未声明支持图片输入")
        config, inputs, _ = self._specialize(
            state,
            config,
            state["goal"]
            + "\n编写结果：\n"
            + state["report"]
            + "\n上一轮审查：\n"
            + state["handoff"],
            include_tools=False,
        )
        agent_run = self._begin_agent(run_id, state, config, inputs, progress)
        state["review_agent_run_id"] = agent_run
        content: list[str | dict[str, Any]] = [
            {
                "type": "text",
                "text": "历史会话（仅供参考）：\n"
                + dumps(inputs["history"])
                + "\n"
                + inputs["current"]
                + "\n检查比例、布局、溢出和响应式问题。"
                "仅有建议时 passed；存在阻断问题时 changes_requested；证据不足时 unable_to_review。"
                "问题必须引用所附截图 ID。",
            }
        ]
        for artifact_id in state["artifacts"]:
            artifact, data = self.records.artifact_bytes(artifact_id)
            content.append(
                {
                    "type": "text",
                    "text": "截图 ID：" + artifact_id + "\n" + dumps(artifact["metadata"]),
                }
            )
            content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "data:image/png;base64," + base64.b64encode(data).decode()
                    },
                }
            )
        model = self._model(config).with_structured_output(
            ReviewResult, method="json_mode", include_raw=True
        )
        response = model.invoke(
            [
                SystemMessage(
                    content=config["agent"]["instructions"]
                    + "\n你只审查证据，不能执行代码或修改文件。"
                    + "只输出符合以下 JSON Schema 的 JSON 对象：\n"
                    + dumps(ReviewResult.model_json_schema())
                ),
                HumanMessage(content=content),
            ]
        )
        check_cancelled()
        usage = {}
        if isinstance(response, dict) and "parsed" in response:
            if response.get("parsing_error") or response["parsed"] is None:
                raise ValueError("视觉审查返回了不符合结构定义的结果")
            usage = getattr(response.get("raw"), "usage_metadata", None) or {}
            response = response["parsed"]
        result = (
            response
            if isinstance(response, ReviewResult)
            else ReviewResult.model_validate(response)
        )
        previous = {
            row["id"]
            for row in self.records.db.rows(
                select(ReviewFinding.id)
                .join(ReviewFinding.review)
                .join(Review.agent_run)
                .where(AgentRun.run_id == run_id)
            )
        }
        for finding in result.findings:
            if finding.artifact_id not in state["artifacts"]:
                raise ValueError("审查引用了非本轮截图")
            if finding.previous_finding_id and finding.previous_finding_id not in previous:
                raise ValueError("审查引用了未知的历史问题")
            if finding.bbox is not None and (
                len(finding.bbox) != 4 or any(not 0 <= v <= 1 for v in finding.bbox)
            ):
                raise ValueError("问题边界框必须是四个 0 到 1 的数值")
        if result.verdict == "changes_requested" and not any(
            finding.severity == "blocking" for finding in result.findings
        ):
            raise ValueError("审查要求返修但没有提供阻断问题及证据")
        if result.verdict != "unable_to_review":
            result.verdict = (
                "changes_requested"
                if any(finding.severity == "blocking" for finding in result.findings)
                else "passed"
            )
        with self.records.db.transaction():
            review = self.records.insert(
                Review,
                id=uid(),
                agent_run_id=agent_run,
                code_agent_run_id=state["code_agent_run_id"],
                iteration=state["iteration"],
                verdict=result.verdict,
                summary=result.summary,
                created_at=now(),
            )
            for artifact_id in state["artifacts"]:
                self.records.insert(ReviewArtifact, review_id=review["id"], artifact_id=artifact_id)
            findings = []
            for finding in result.findings:
                values = finding.model_dump(exclude={"bbox"})
                item = self.records.insert(
                    ReviewFinding,
                    id=uid(),
                    review_id=review["id"],
                    bbox_json=dumps(finding.bbox),
                    **values,
                )
                findings.append(item)
            output = self.runtime.redact(result.model_dump_json())
            self.records.update(
                AgentRun,
                agent_run,
                status="completed",
                output=output,
                usage_json=dumps(usage),
                updated_at=now(),
            )
            self.records.message(
                self.id,
                "assistant",
                output,
                run_id=run_id,
                agent_run_id=agent_run,
                visibility="internal",
            )
        self._emit(run_id, state, "stage.completed", {"output": output}, progress)
        self._emit(
            run_id,
            state,
            "review.completed",
            {"reviewId": review["id"], "verdict": result.verdict},
            progress,
        )
        state.pop("review_agent_run_id", None)
        return review | {"findings": findings}

    def _unable(self, run_id, state, reason, progress):
        """记录无法审查的证据缺口并等待用户处理。"""
        agent_run = state.pop("review_agent_run_id", None)
        if agent_run is None:
            previous_phase = state["phase"]
            state["phase"] = "review"
            agent_run = self._begin_agent(
                run_id,
                state,
                self._step(state, "review")["config"],
                {"error": reason, "artifact_ids": []},
                progress,
            )
            state["phase"] = previous_phase
        with self.records.db.transaction():
            self.records.update(
                AgentRun, agent_run, status="failed", error=reason, updated_at=now()
            )
            review = self.records.insert(
                Review,
                id=uid(),
                agent_run_id=agent_run,
                code_agent_run_id=state["code_agent_run_id"],
                iteration=state["iteration"],
                verdict="unable_to_review",
                summary=reason,
                created_at=now(),
            )
        state["handoff"] = dumps(review)
        self._emit(
            run_id,
            state,
            "review.completed",
            {"reviewId": review["id"], "verdict": "unable_to_review"},
            progress,
        )
        return self._wait(
            run_id,
            state,
            "review_unavailable",
            "decision",
            {"question": reason, "actions": ["retry", "accept", "cancel"]},
        )

    def _wait(self, run_id, state, manual, kind, request):
        """登记流程级人工输入点。"""
        check_cancelled()
        state["manual"] = manual
        with self.records.db.transaction():
            self.runtime.approvals.create(run_id, kind, {"kind": kind} | request)
            self.records.update(
                Run,
                run_id,
                status="waiting",
                updated_at=now(),
                state_json=dumps(self._public_state(state)),
            )
            state["waiting_workspace"] = self.repository.finish_run(run_id, "running")
            self.runtime.checkpoints.save(self, run_id, state)
        return self._result(run_id)

    def _public_state(self, state):
        """生成协议与重载共用的公开快照。"""
        if state.get("code_agent_run_id") and state["tasks"]:
            statuses = {
                row["id"]: row["status"]
                for row in self.records.db.rows(
                    select(Task.id, Task.status).where(
                        Task.agent_run_id == state["code_agent_run_id"]
                    )
                )
            }
            for task in state["tasks"]:
                if task["id"] in statuses:
                    task["status"] = statuses[task["id"]]
        return {
            "goal": state["goal"],
            "report": state.get("report", ""),
            "tasks": state["tasks"],
            "results": state["results"],
            "phase": state["phase"],
            "iteration": state["iteration"],
            **({"error_code": state["error_code"]} if state.get("error_code") else {}),
        }

    def _finish(self, run_id, state, status, report):
        """原子结束运行并提交最终公开消息。"""
        with self._control_lock:
            run = self.records.get(Run, run_id)
            if run["status"] not in {"queued", "running", "waiting"}:
                return self._result(run_id)
            if state["cancel_event"].is_set():
                status = state.get("stop_status", "cancelled")
                report = state.get("stop_report", "用户取消了运行")
            if status == "cancelled":
                tasks = self.records.db.rows(
                    select(Task.id, Task.status).where(
                        Task.agent_run_id.in_(select(AgentRun.id).where(AgentRun.run_id == run_id))
                    )
                )
                statuses = {row["id"]: row["status"] for row in tasks}
                state["tasks"] = [
                    task | {"status": statuses.get(task["id"], "cancelled")}
                    for task in state["tasks"]
                ]
                for task in state["tasks"]:
                    if task["status"] in {"queued", "running", "waiting"}:
                        task["status"] = "cancelled"
            result = self._finish_locked(run_id, state, status, report)
        return result

    def _notify_timeout(self, state, run_id):
        """释放执行锁后才通知超时终态，接收器可立即开始下一轮。"""
        if not state.get("approval_timeout_notification"):
            return
        with self._control_lock:
            if state["executing"] or self._lock.locked():
                return
            state.pop("approval_timeout_notification", None)
            result = self._result(run_id)
        self.runtime.events.publish("run.failed", result.to_dict())

    def _finish_locked(self, run_id, state, status, report):
        """在执行控制锁内保存唯一终态并失效未解决输入。"""
        state["report"] = report
        with self.records.db.transaction() as connection:
            self.records.delete_checkpoint(run_id)
            connection.execute(
                orm_update(Task)
                .where(
                    Task.agent_run_id.in_(select(AgentRun.id).where(AgentRun.run_id == run_id)),
                    Task.status.in_(("queued", "running", "waiting")),
                )
                .values(status=status, updated_at=now())
            )
            public_state = self._public_state(state)
            self.records.update(
                Run,
                run_id,
                status=status,
                ended_at=now(),
                updated_at=now(),
                error=report if status == "failed" else "",
                state_json=dumps(public_state),
            )
            self.records.update(ConversationSession, self.id, updated_at=now())
            resolutions = self.runtime.approvals.reject_pending(
                run_id, "timeout" if state.get("stop_status") else status
            )
            self.records.event(run_id, "run." + status, public_state)
            self.records.message(
                self.id,
                "assistant",
                report,
                run_id=run_id,
                agent_run_id=state.get("agent_run_id"),
                kind="message"
                if status == "cancelled"
                else "report"
                if status == "completed"
                else "error",
                status="cancelled"
                if status == "cancelled"
                else "completed"
                if status == "completed"
                else "failed",
            )
            connection.execute(
                orm_update(Interaction)
                .where(Interaction.run_id == run_id, Interaction.status == "pending")
                .values(status="expired", updated_at=now())
            )
            connection.execute(
                orm_update(AgentRun)
                .where(AgentRun.run_id == run_id, AgentRun.status.in_(("running", "waiting")))
                .values(status=status, error=report if status == "failed" else "", updated_at=now())
            )
        self.runtime.checkpoints.discard_files(run_id)
        self.runtime.approvals.disarm(run_id)
        self.runtime.approvals.publish(resolutions)
        if run_id in self._run_ids:
            try:
                self.repository.finish_run(
                    run_id,
                    status,
                    snapshot=state.get("waiting_workspace") or state.get("restored_workspace"),
                )
            except ValueError:
                pass
        self._active.pop(run_id, None)
        return self._result(run_id)

    def _result(self, run_id):
        """读取已提交的运行状态及有效交互。"""
        run = self.records.get(Run, run_id)
        pending = [
            decode(row)
            for row in self.records.db.rows(
                select(Interaction).where(
                    Interaction.run_id == run_id, Interaction.status == "pending"
                )
            )
        ]
        return RunUpdate(
            self.id,
            run_id,
            run["status"],
            run["state"] | {"goal": run["goal"]},
            tuple(
                RuntimeInterrupt(row["interrupt_id"], self.runtime.approvals.request(row))
                for row in pending
            ),
            retry_of_run_id=run["retry_of_run_id"],
            retry_task_id=run["retry_task_id"],
        )

    def close(self, *, preserve_waiting=False):
        """关闭会话，运行时退出时保留已持久化的等待点。"""
        if not self._lock.acquire(blocking=False):
            raise ValueError("会话正在执行，请等待当前阶段结束后关闭")
        try:
            for run_id, state in list(self._active.items()):
                if preserve_waiting and self.records.load_checkpoint(run_id) is not None:
                    continue
                self._finish(run_id, state, "interrupted", "会话已关闭")
            self.repository.close()
        finally:
            self._lock.release()
