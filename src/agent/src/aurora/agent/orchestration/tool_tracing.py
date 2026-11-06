"""记录通过审批后的真实工具调用及耗时。"""

from __future__ import annotations

import json
import time

from langgraph.errors import GraphInterrupt
from sqlalchemy import select

from aurora.agent.store.database import dumps, now, uid
from aurora.agent.store.model import Run, Task, ToolCall
from aurora.common.cancellation import CANCEL_EVENT, OperationCancelled, check_cancelled
from aurora.common.text import sanitize_value

SENSITIVE_KEYS = frozenset(
    {
        "apikey",
        "authorization",
        "password",
        "secret",
        "token",
        "accesskey",
        "credential",
        "credentials",
        "clientsecret",
        "refreshtoken",
        "accesstoken",
        "secretkey",
    }
)


class ToolTracing:
    """保存独立于任务派发状态的调用记录。"""

    def __init__(self, runtime):
        self.runtime = runtime

    def clean(self, value):
        """移除已登记凭据及结构化输入输出中的敏感字段。"""
        value = json.loads(self.runtime.redact(dumps(sanitize_value(value))))

        def scrub(item):
            if isinstance(item, dict):
                return {
                    key: "[redacted]"
                    if "".join(char for char in key.lower() if char.isalnum()) in SENSITIVE_KEYS
                    else scrub(entry)
                    for key, entry in item.items()
                }
            if isinstance(item, list):
                return [scrub(entry) for entry in item]
            if isinstance(item, str) and item.lstrip().startswith(("{", "[")):
                try:
                    return dumps(scrub(json.loads(item)))
                except json.JSONDecodeError:
                    return item
            return item

        return scrub(value)

    def invoke(self, run_id, agent_run_id, task_key, tool, args):
        """在真正执行工具前后记录状态，审批等待不会产生假调用。"""
        call_id = uid()
        started = time.perf_counter()
        with self.runtime.records.db.transaction():
            self.runtime.records.insert(
                ToolCall,
                id=call_id,
                run_id=run_id,
                agent_run_id=agent_run_id,
                task_key=str(task_key),
                tool_name=tool.name,
                input_json=dumps(self.clean(dict(args))),
                status="running",
                started_at=now(),
            )
            self._record(call_id, "tool.started")
        self._publish(call_id, "tool.started")
        try:
            check_cancelled()
            value = tool.run(**dict(args))
        except BaseException as exc:
            cancelled = CANCEL_EVENT.get()
            status = (
                "cancelled"
                if isinstance(exc, OperationCancelled) or (cancelled and cancelled.is_set())
                else "interrupted"
                if isinstance(exc, GraphInterrupt)
                else "failed"
            )
            error = self.clean(str(exc))
            self._finish(call_id, status, "", error, started)
            raise
        self._finish(call_id, "completed", self.clean(str(value)), "", started)
        return value

    def _finish(self, call_id, status, output, error, started):
        """提交一次调用的唯一终态后推送变化。"""
        with self.runtime.records.db.transaction():
            self.runtime.records.update(
                ToolCall,
                call_id,
                status=status,
                output=output,
                error=error,
                ended_at=now(),
                duration_ms=max(0, int((time.perf_counter() - started) * 1000)),
            )
            self._record(call_id, "tool." + status)
        self._publish(call_id, "tool." + status)

    def _payload(self, call_id):
        """使用稳定调用 ID 关联角色、任务与运行。"""
        row = self.runtime.records.get(ToolCall, call_id)
        run = self.runtime.records.get(Run, row["run_id"])
        task = self.runtime.records.db.first(
            select(Task.id).where(
                Task.agent_run_id == row["agent_run_id"], Task.task_key == row["task_key"]
            )
        )
        return row | {
            "tool_call_id": row["id"],
            "session_id": run["session_id"],
            "task_id": task["id"] if task else None,
        }

    def _record(self, call_id, name):
        """将与界面相同的调用事件写入审计序列。"""
        payload = self._payload(call_id)
        self.runtime.records.event(payload["run_id"], name, payload)

    def _publish(self, call_id, name):
        """向所有已连接传输发送已提交的调用记录。"""
        self.runtime.events.publish(name, self._payload(call_id))
