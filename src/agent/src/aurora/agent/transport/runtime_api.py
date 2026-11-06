"""处理跨传输共享的运行时请求、响应和事件。"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, TextIO
from uuid import uuid4

from aurora.agent.orchestration.runtime import (
    AgentRuntime,
    RunControlError,
    RunExecutionError,
    RunUpdate,
    validate_workspace,
)
from aurora.common.protocol import PROTOCOL_VERSION
from aurora.common.text import sanitize_text, sanitize_value

from ..mcp import McpClientError, McpServerConfig
from ..sandbox import SandboxMode
from ..store import GitRepository, GitView


class MethodNotFoundError(ValueError):
    """请求使用了运行时不支持的方法。"""


class RuntimeApi:
    """把 NDJSON 协议请求分发到 AgentRuntime。"""

    def __init__(self, runtime: AgentRuntime | None = None) -> None:
        self._runtime = runtime or AgentRuntime()
        from .persistence_api import PersistenceApi

        self._persistence = PersistenceApi(self._runtime)

    def subscribe(self, emit):
        """订阅请求结束后仍可能到达的运行时通知。"""
        return self._runtime.events.subscribe(
            lambda name, payload: emit(_wire_event(name, payload))
        )

    def handle(self, request: Mapping[str, Any]) -> list[dict[str, Any]]:
        """处理单个请求并返回响应与后续事件。"""
        request = sanitize_value(request)
        request_id = request.get("id")
        try:
            method = _required_string(request, "method")
            params = request.get("params", {})
            if not isinstance(params, Mapping):
                raise ValueError("params 必须是对象")
            result, events = self._dispatch(method, params)
            return [{"id": request_id, "result": result}, *events]
        except (RunControlError, RunExecutionError) as exc:
            return [_error(request_id, exc.code, str(exc))]
        except MethodNotFoundError as exc:
            return [_error(request_id, "method_not_found", self._runtime.redact(str(exc)))]
        except McpClientError as exc:
            return [_error(request_id, "mcp_error", self._runtime.redact(str(exc)))]
        except (TypeError, ValueError, KeyError) as exc:
            return [_error(request_id, "invalid_request", self._runtime.redact(str(exc)))]
        except Exception as exc:
            return [_error(request_id, "internal_error", self._runtime.redact(str(exc)))]

    def handle_wire(self, request: Mapping[str, Any]) -> list[dict[str, Any]]:
        """处理桌面端协议信封并返回响应与事件。"""
        request_id = request.get("request_id")
        if request.get("protocol_version") != PROTOCOL_VERSION:
            return [_wire_error(request_id, "invalid_request", "仅支持 protocol_version=1")]
        frames = self.handle(
            {
                "id": request_id,
                "method": request.get("method"),
                "params": request.get("params", {}),
            }
        )
        result: list[dict[str, Any]] = []
        for frame in frames:
            if "event" in frame:
                result.append(_wire_event(str(frame["event"]), frame.get("data", {})))
            elif "error" in frame:
                result.append(
                    _wire_error(
                        request_id,
                        str(frame["error"].get("code", "internal_error")),
                        str(frame["error"].get("message", "运行时请求失败")),
                    )
                )
            else:
                result.append(
                    {
                        "protocol_version": PROTOCOL_VERSION,
                        "request_id": request_id,
                        "ok": True,
                        "result": frame.get("result", {}),
                    }
                )
        return result

    def process_wire(
        self,
        request: Mapping[str, Any],
        emit: Callable[[dict[str, Any]], None],
    ) -> None:
        """处理桌面端请求并实时发送协议帧。"""
        request = sanitize_value(request)
        request_id = request.get("request_id")
        if request.get("protocol_version") != PROTOCOL_VERSION:
            emit(_wire_error(request_id, "invalid_request", "仅支持 protocol_version=1"))
            return
        try:
            method = _required_string(request, "method")
            params = request.get("params", {})
            if not isinstance(params, Mapping):
                raise ValueError("params 必须是对象")
            if method not in {"run.start", "run.resume", "run.retry", "approval.resolve"}:
                for frame in self.handle_wire(request):
                    emit(frame)
                return

            def acknowledge(result=None):
                emit(
                    {
                        "protocol_version": PROTOCOL_VERSION,
                        "request_id": request_id,
                        "ok": True,
                        "result": result if result is not None else {"accepted": True},
                    }
                )

            result, events = self._dispatch_stream(method, params, emit, acknowledge)
            if method not in {"approval.resolve", "run.retry"}:
                emit(
                    {
                        "protocol_version": PROTOCOL_VERSION,
                        "request_id": request_id,
                        "ok": True,
                        "result": result,
                    }
                )
            for event in events:
                emit(_wire_event(str(event["event"]), event.get("data", {})))
        except (RunControlError, RunExecutionError) as exc:
            emit(_wire_error(request_id, exc.code, str(exc)))
        except MethodNotFoundError as exc:
            emit(_wire_error(request_id, "method_not_found", self._runtime.redact(str(exc))))
        except McpClientError as exc:
            emit(_wire_error(request_id, "mcp_error", self._runtime.redact(str(exc))))
        except (TypeError, ValueError, KeyError) as exc:
            emit(_wire_error(request_id, "invalid_request", self._runtime.redact(str(exc))))
        except Exception as exc:
            emit(_wire_error(request_id, "internal_error", self._runtime.redact(str(exc))))

    def _dispatch_stream(
        self,
        method: str,
        params: Mapping[str, Any],
        emit: Callable[[dict[str, Any]], None],
        on_accepted=None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """执行运行方法并实时转换图节点事件。"""
        if method == "approval.resolve":
            session_id, run_id, approval_id, approved = self._approval_target(params)
        elif method == "run.retry":
            session_id = _identifier(params, "session_id", "sessionId")
            run_id = _identifier(params, "run_id", "runId")
            self._runtime.require_run(session_id, run_id)
        else:
            session_id = _required_string(params, "sessionId")
        session = self._runtime.get_session(session_id)
        message_id: str | None = None

        def progress(run_id: str, node: str, update: Mapping[str, Any]) -> None:
            nonlocal message_id
            base = {"sessionId": session_id, "runId": run_id}

            if node.startswith(("stage.", "artifact.", "review.")):
                emit(_wire_event(node, {**base, **dict(update)}))
                return
            if node in {"run.started", "run.resumed"}:
                emit(_wire_event(node, {**base, **dict(update)}))
                return
            if node == "plan":
                planned = [dict(item) for item in update.get("tasks", [])]
                emit(_wire_event("plan.created", {**base, "tasks": planned}))
                return
            if node == "summarize":
                report = str(update.get("report", ""))
                message_id = message_id or f"message_{run_id}"
                emit(_wire_event("message.started", {**base, "messageId": message_id}))
                for delta in _report_deltas(report):
                    emit(
                        _wire_event(
                            "message.delta",
                            {**base, "messageId": message_id, "delta": delta},
                        )
                    )
                emit(
                    _wire_event(
                        "message.completed",
                        {**base, "messageId": message_id, "content": report},
                    )
                )

        if method == "run.start":
            update = session.start(
                _required_string(params, "goal"),
                progress,
                workflow_id=_optional_string(params, "workflowId"),
                request_key=_optional_string(params, "requestKey"),
                mode=str(params.get("mode", "run")),
            )
        elif method == "run.retry":
            retried_id = None

            def created(new_id):
                nonlocal retried_id
                retried_id = new_id
                if on_accepted:
                    on_accepted({"run": self._retry_summary(new_id)})

            task_id = (
                _identifier(params, "task_id", "taskId")
                if "task_id" in params or "taskId" in params
                else None
            )
            try:
                update = session.retry(run_id, task_id, progress, created)
            except Exception:
                if retried_id is None:
                    raise
                update = session._result(retried_id)
                if update.status not in {"completed", "failed", "cancelled", "interrupted"}:
                    raise
        elif method == "approval.resolve":
            accepted = False

            def acknowledge_approval():
                nonlocal accepted
                accepted = True
                if on_accepted:
                    on_accepted()

            try:
                update = session.resume(
                    run_id,
                    {"approved": approved},
                    on_progress=progress,
                    approval_id=approval_id,
                    on_accepted=acknowledge_approval,
                )
            except Exception:
                if not accepted:
                    raise
                update = session._result(run_id)
                if update.status not in {"completed", "failed", "cancelled", "interrupted"}:
                    raise
        else:
            update = session.resume(
                _required_string(params, "runId"),
                params.get("response"),
                _optional_string(params, "interruptId"),
                progress,
            )
        return _frames_for_update(update)

    def _retry_summary(self, run_id):
        """返回重试创建时的规范运行摘要。"""
        from aurora.agent.store.model import Run

        row = self._runtime.records.get(Run, run_id)
        return {
            "id": row["id"],
            "session_id": row["session_id"],
            "objective": row["goal"],
            "status": row["status"],
            "retry_of_run_id": row["retry_of_run_id"],
            "retry_task_id": row["retry_task_id"],
            "error": row["error"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _approval_target(self, params):
        """校验规范审批参数及归属，重复和过期请求不重新执行。"""
        from sqlalchemy import select

        from aurora.agent.store.model import Interaction

        session_id = _identifier(params, "session_id", "sessionId")
        run_id = _identifier(params, "run_id", "runId")
        approval_id = _identifier(params, "approval_id", "approvalId")
        approved = params.get("approved")
        if not isinstance(approved, bool):
            raise ValueError("approved 必须是布尔值")
        self._runtime.require_run(session_id, run_id)
        row = self._runtime.records.db.first(
            select(Interaction).where(
                Interaction.id == approval_id,
                Interaction.run_id == run_id,
                Interaction.kind == "approval",
            )
        )
        if row is None:
            raise RunControlError("APPROVAL_NOT_FOUND", "审批不存在或不属于当前运行")
        if row["status"] != "pending":
            code = "APPROVAL_EXPIRED" if row["status"] == "expired" else "APPROVAL_ALREADY_RESOLVED"
            raise RunControlError(
                code, "审批已过期" if code == "APPROVAL_EXPIRED" else "审批已解决"
            )
        return session_id, run_id, approval_id, approved

    def _dispatch(
        self,
        method: str,
        params: Mapping[str, Any],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """执行一个协议方法。"""
        if method == "run.retry":
            result, events = self._dispatch_stream(method, params, lambda frame: None)
            return {"run": self._retry_summary(result["runId"])}, events
        if method == "approval.resolve":
            _, events = self._dispatch_stream(method, params, lambda frame: None)
            return {"accepted": True}, events
        if method == "run.cancel":
            session_id = _identifier(params, "session_id", "sessionId")
            run_id = _identifier(params, "run_id", "runId")
            accepted, update = self._runtime.cancel_run(session_id, run_id)
            events = _frames_for_update(update)[1] if update is not None else []
            return {"accepted": accepted}, events
        if method in self._persistence.capabilities:
            return self._persistence.dispatch(method, params), []
        if method == "runtime.initialize":
            return {
                "protocolVersion": str(PROTOCOL_VERSION),
                "databasePath": self._runtime.records.db.path,
                "defaultWorkflowId": self._runtime.records.setting("default_workflow_id"),
                "capabilities": [
                    *self._persistence.capabilities,
                    "workspace.validate",
                    "workspace.git.initialize",
                    "session.create",
                    "run.start",
                    "run.resume",
                    "run.cancel",
                    "run.retry",
                    "approval.resolve",
                    "session.close",
                    "git.status",
                    "git.diff",
                    "git.rollback",
                    "mcp.server.connect",
                    "mcp.server.list",
                    "mcp.server.disconnect",
                    "mcp.package.catalog",
                    "mcp.package.connect",
                    "mcp.package.list",
                    "mcp.package.disconnect",
                ],
            }, []
        if method == "workspace.validate":
            return validate_workspace(_required_string(params, "path")).to_dict(), []
        if method == "workspace.git.initialize":
            path = _required_string(params, "path")
            repository = GitRepository.ensure(path)
            repository.close()
            return validate_workspace(path).to_dict(), []
        if method == "mcp.server.connect":
            return self._runtime.connect_mcp_server(McpServerConfig.from_mapping(params)), []
        if method == "mcp.server.list":
            return {"servers": self._runtime.list_mcp_servers()}, []
        if method == "mcp.server.disconnect":
            name = _required_string(params, "name")
            self._runtime.disconnect_mcp_server(name)
            return {"name": name, "disconnected": True}, []
        if method == "mcp.package.catalog":
            return {
                "packages": self._runtime.catalog_mcp_packages(),
                "pluginErrors": self._runtime.mcp_package_plugin_errors(),
            }, []
        if method == "mcp.package.connect":
            package_id = _required_string(params, "packageId")
            instance_name = _optional_string(params, "instanceName") or package_id
            config = params.get("config", {})
            if not isinstance(config, Mapping):
                raise ValueError("config 必须是对象")
            return self._runtime.connect_mcp_package(
                package_id,
                instance_name,
                config,
            ), []
        if method == "mcp.package.list":
            return {"packages": self._runtime.list_connected_mcp_packages()}, []
        if method == "mcp.package.disconnect":
            instance_name = _required_string(params, "instanceName")
            self._runtime.disconnect_mcp_package(instance_name)
            return {"instanceName": instance_name, "disconnected": True}, []
        if method == "session.create":
            sandbox_mode = _sandbox_mode(params.get("sandboxMode", "workspace-write"))
            session = self._runtime.create_session(
                _required_string(params, "workspacePath"),
                sandbox_mode=sandbox_mode,
                approval_mode=str(params.get("approvalMode", "interactive")),
                workflow_id=_optional_string(params, "workflowId"),
                title=str(params.get("title", "新对话")),
            )
            return {
                "sessionId": session.id,
                "projectId": session.record["project_id"],
                "workflowId": session.record["workflow_id"],
                "workspace": session.workspace.to_dict(),
                "sandboxMode": sandbox_mode,
                "approvalMode": str(params.get("approvalMode", "interactive")),
            }, []
        if method == "run.start":
            session = self._runtime.get_session(_required_string(params, "sessionId"))
            return _frames_for_update(
                session.start(
                    _required_string(params, "goal"),
                    workflow_id=_optional_string(params, "workflowId"),
                    request_key=_optional_string(params, "requestKey"),
                    mode=str(params.get("mode", "run")),
                )
            )
        if method == "run.resume":
            session = self._runtime.get_session(_required_string(params, "sessionId"))
            update = session.resume(
                _required_string(params, "runId"),
                params.get("response"),
                _optional_string(params, "interruptId"),
            )
            return _frames_for_update(update)
        if method == "git.status":
            session = self._runtime.get_session(_required_string(params, "sessionId"))
            return session.git_status(
                _git_view(params.get("view", "workspace")),
                _optional_string(params, "runId"),
            ), []
        if method == "git.diff":
            session = self._runtime.get_session(_required_string(params, "sessionId"))
            return session.git_diff(
                _required_string(params, "path"),
                _git_view(params.get("view", "workspace")),
                _optional_string(params, "runId"),
            ), []
        if method == "git.rollback":
            session = self._runtime.get_session(_required_string(params, "sessionId"))
            return session.git_rollback(_required_string(params, "runId")), []
        if method == "session.close":
            session_id = _required_string(params, "sessionId")
            self._runtime.close_session(session_id)
            return {"sessionId": session_id, "closed": True}, []
        raise MethodNotFoundError(f"未知方法: {method}")

    def close(self) -> None:
        """关闭运行时持有的外部资源。"""
        self._runtime.close()


def serve_ndjson(api: RuntimeApi, input_stream: TextIO, output_stream: TextIO) -> None:
    """并行处理耗时运行，保持状态查询与配置请求可响应。"""
    import threading
    from concurrent.futures import ThreadPoolExecutor

    lock = threading.Lock()

    def emit(frame):
        """原子写出一个协议帧。"""
        with lock:
            _write_frame(output_stream, frame)

    unsubscribe = api.subscribe(emit)
    try:
        with ThreadPoolExecutor(max_workers=4) as workers:
            for line in input_stream:
                if not line.strip():
                    continue
                try:
                    request = json.loads(line)
                    if not isinstance(request, Mapping):
                        raise ValueError("请求必须是 JSON 对象")
                    if "request_id" in request or "protocol_version" in request:
                        if request.get("method") in {
                            "run.start",
                            "run.resume",
                            "run.retry",
                            "agent.test",
                            "approval.resolve",
                        }:
                            workers.submit(api.process_wire, request, emit)
                        else:
                            api.process_wire(request, emit)
                        continue
                    frames = api.handle(request)
                except (json.JSONDecodeError, ValueError) as exc:
                    frames = [_error(None, "invalid_json", str(exc))]
                for frame in frames:
                    emit(frame)
    finally:
        unsubscribe()


def _frames_for_update(update: RunUpdate) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """把运行快照转换为响应结果和广播事件。"""
    result = update.to_dict()
    if update.status == "completed":
        return result, [{"event": "run.completed", "data": result}]
    if update.status in {"failed", "interrupted", "cancelled"}:
        return result, [{"event": "run." + update.status, "data": result}]
    events: list[dict[str, Any]] = []
    names = {
        "approval": "approval.required",
        "clarification": "clarification.required",
        "evaluation": "evaluation.required",
        "decision": "run.input_required",
    }
    for pending in update.interruptions:
        data = {
            "sessionId": update.session_id,
            "runId": update.run_id,
            **pending.to_dict(),
        }
        events.append(
            {"event": names.get(str(pending.value.get("kind")), "run.input_required"), "data": data}
        )
        if pending.value.get("kind") == "approval":
            events.append(
                {
                    "event": "approval.requested",
                    "data": {
                        "session_id": update.session_id,
                        "run_id": update.run_id,
                        **pending.to_dict(),
                        "action": pending.value.get(
                            "action", pending.value.get("tool", "执行受保护操作")
                        ),
                    },
                }
            )
    return result, events


def _report_deltas(report: str, size: int = 80) -> list[str]:
    """把最终报告切分为稳定的流式文本片段。"""
    if not report:
        return []
    return [report[index : index + size] for index in range(0, len(report), size)]


def _write_frame(output_stream: TextIO, frame: Mapping[str, Any]) -> None:
    """写出并刷新单个 NDJSON 协议帧。"""
    output_stream.write(
        json.dumps(sanitize_value(frame), ensure_ascii=False, separators=(",", ":")) + "\n"
    )
    output_stream.flush()


def _identifier(values: Mapping[str, Any], name: str, legacy: str) -> str:
    """读取规范标识符并兼容旧字段，拒绝相互冲突的别名。"""
    if name in values:
        value = _required_string(values, name)
        if legacy in values and _required_string(values, legacy) != value:
            raise ValueError(f"{name} 与 {legacy} 不一致")
        return value
    return _required_string(values, legacy)


def _required_string(values: Mapping[str, Any], name: str) -> str:
    """读取必填非空字符串。"""
    value = values.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} 必须是非空字符串")
    return sanitize_text(value).strip()


def _optional_string(values: Mapping[str, Any], name: str) -> str | None:
    """读取可选字符串。"""
    value = values.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} 必须是非空字符串")
    return value.strip()


def _sandbox_mode(value: Any) -> SandboxMode:
    """校验并返回沙箱模式。"""
    if value not in {"read-only", "workspace-write", "danger-full-access"}:
        raise ValueError("sandboxMode 无效")
    return value


def _git_view(value: Any) -> GitView:
    """校验 Git 差异视图。"""
    if value not in {"run", "workspace"}:
        raise ValueError("view 必须是 run 或 workspace")
    return value


def _error(request_id: Any, code: str, message: str) -> dict[str, Any]:
    """构造稳定的协议错误帧。"""
    return {"id": request_id, "error": {"code": code, "message": message}}


def _wire_error(request_id: Any, code: str, message: str) -> dict[str, Any]:
    """构造桌面端协议错误信封。"""
    return {
        "protocol_version": PROTOCOL_VERSION,
        "request_id": request_id,
        "ok": False,
        "error": {"code": code, "message": message},
    }


def _wire_event(name: str, data: Any) -> dict[str, Any]:
    """构造桌面端协议事件信封。"""
    payload = dict(data) if isinstance(data, Mapping) else {"value": data}
    return {
        "protocol_version": PROTOCOL_VERSION,
        "event_id": f"evt_{uuid4().hex}",
        "type": name,
        "occurred_at": datetime.now(UTC).isoformat(),
        "session_id": payload.get("session_id") or payload.get("sessionId"),
        "run_id": payload.get("run_id") or payload.get("runId"),
        "task_id": payload.get("task_id") or payload.get("taskId"),
        "payload": payload,
    }
