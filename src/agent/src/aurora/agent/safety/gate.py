"""确认门：按工具风险分级放行或拒绝执行。"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from contextvars import ContextVar
from typing import Any, Protocol

from langgraph.types import interrupt
from rich.console import Console

from aurora.common.cancellation import check_cancelled
from aurora.common.logging import get_logger

from ..tools.base import RiskLevel, Tool

log = get_logger(__name__)
_TASK_ID: ContextVar[str | None] = ContextVar("approval_task_id", default=None)


class ToolDeniedError(RuntimeError):
    """工具执行被确认门拒绝。"""


class Approver(Protocol):
    """确认策略协议。"""

    def approve(self, tool: Tool, args: Mapping[str, Any]) -> bool: ...


class DenyApprover:
    """只放行只读工具，写与执行一律拒绝。"""

    def approve(self, tool: Tool, args: Mapping[str, Any]) -> bool:
        return tool.risk == RiskLevel.READ


class AutoApprover:
    """放行全部工具（显式危险开关）。"""

    def approve(self, tool: Tool, args: Mapping[str, Any]) -> bool:
        return True


class InteractiveApprover:
    """只读自动放行，写与执行在终端交互确认。"""

    def __init__(self, console: Console | None = None) -> None:
        self._console = console or Console(stderr=True)
        self._lock = threading.Lock()

    def approve(self, tool: Tool, args: Mapping[str, Any]) -> bool:
        if tool.risk == RiskLevel.READ:
            return True
        with self._lock:
            log.info(
                "确认门 即将执行 %s（风险 %s）\n参数: %s",
                tool.name,
                tool.risk.value,
                args,
            )
            answer = self._console.input("允许执行？(y/n) ").strip().lower()
        return answer in {"y", "yes"}


class InterruptApprover:
    """通过图中断把高风险工具审批交给上层应用。"""

    def approve(self, tool: Tool, args: Mapping[str, Any]) -> bool:
        if tool.risk == RiskLevel.READ:
            return True
        response = interrupt(
            {
                "kind": "approval",
                "tool": tool.name,
                "risk": tool.risk.value,
                "args": dict(args),
                **({"taskId": _TASK_ID.get()} if _TASK_ID.get() else {}),
            }
        )
        if isinstance(response, Mapping):
            return response.get("approved") is True
        return response is True


class ConfirmationGate:
    """工具执行前的确认门，拒绝时抛出 ToolDeniedError。"""

    def __init__(self, approver: Approver | None = None) -> None:
        self._approver = approver or DenyApprover()

    def invoke(
        self,
        tool: Tool,
        args: Mapping[str, Any],
        *,
        run_tool: Callable[[], str] | None = None,
        task_id: str | None = None,
    ) -> str:
        check_cancelled()
        token = _TASK_ID.set(task_id)
        try:
            if not self._approver.approve(tool, args):
                raise ToolDeniedError(
                    f"工具 {tool.name}（风险 {tool.risk.value}）需要用户确认，已拒绝执行"
                )
        finally:
            _TASK_ID.reset(token)
        check_cancelled()
        return run_tool() if run_tool else tool.run(**dict(args))


def build_gate(mode: str = "interactive") -> ConfirmationGate:
    """按名称构建确认门。"""
    approvers: dict[str, Approver] = {
        "interactive": InteractiveApprover(),
        "interrupt": InterruptApprover(),
        "always": AutoApprover(),
        "never": DenyApprover(),
    }
    if mode not in approvers:
        raise ValueError(f"未知确认策略: {mode}，可选 {', '.join(approvers)}")
    return ConfirmationGate(approvers[mode])
