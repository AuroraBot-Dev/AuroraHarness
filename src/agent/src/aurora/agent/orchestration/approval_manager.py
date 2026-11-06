"""持久化审批结果并调度未解决审批的截止时间。"""

from __future__ import annotations

import math
import threading
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from aurora.agent.store.database import dumps, uid
from aurora.agent.store.model import Interaction, Run
from aurora.agent.store.records import decode
from aurora.common.logging import get_logger

log = get_logger(__name__)


class ApprovalManager:
    """用单个后台线程处理审批超时及唯一解决记录。"""

    def __init__(self, runtime, timeout: float = 300, clock=None):
        if isinstance(timeout, bool) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("审批超时必须是正有限秒数")
        self.runtime = runtime
        self.timeout = timeout
        self.clock = clock or (lambda: datetime.now(UTC))
        self._condition = threading.Condition()
        self._deadlines = {}
        self._thread = None
        self._closed = False

    def create(
        self, run_id, kind, request, *, interrupt_id=None, agent_run_id=None, expires_at=None
    ):
        """登记人工输入，危险操作同时冻结 UTC 截止时间。"""
        stamp = self.clock()
        expires = (
            (stamp + timedelta(seconds=self.timeout)).isoformat() if kind == "approval" else None
        )
        row = self.runtime.records.insert(
            Interaction,
            id=uid(),
            run_id=run_id,
            interrupt_id=interrupt_id or uid(),
            agent_run_id=agent_run_id,
            kind=kind,
            request_json=dumps(request),
            expires_at=expires_at if expires_at is not None else expires,
            created_at=stamp.isoformat(),
            updated_at=stamp.isoformat(),
        )
        if kind == "approval":
            self.runtime.records.event(run_id, "approval.requested", self.request(row))
        return row

    def request(self, row):
        """生成历史快照与通知共用的审批标识和期限。"""
        row = decode(row) if "request_json" in row else row
        return dict(row["request"]) | {
            "approval_id": row["id"],
            "interruptId": row["interrupt_id"],
            "expires_at": row["expires_at"],
        }

    def resolve(self, row, approved, *, resolution=None):
        """在调用方事务内保存一次审批解决及审计事件。"""
        instant = self.clock()
        if (
            resolution is None
            and row["expires_at"]
            and datetime.fromisoformat(row["expires_at"]) <= instant
        ):
            from .runtime import RunControlError

            raise RunControlError("APPROVAL_EXPIRED", "审批已过期")
        resolution = resolution or ("approved" if approved else "rejected")
        stamp = instant.isoformat()
        self.runtime.records.update(
            Interaction,
            row["id"],
            status="resolved" if resolution in {"approved", "rejected"} else "expired",
            response_json=dumps({"approved": approved}),
            resolved_at=stamp,
            resolution=resolution,
            updated_at=stamp,
        )
        run = self.runtime.records.get(Run, row["run_id"])
        payload = {
            "session_id": run["session_id"],
            "run_id": run["id"],
            "approval_id": row["id"],
            "interrupt_id": row["interrupt_id"],
            "approved": approved,
            "resolved_at": stamp,
            "resolution": resolution,
        }
        self.runtime.records.event(run["id"], "approval.resolved", payload)
        return payload

    def reject_pending(self, run_id, resolution):
        """收束终止运行中仍未解决的危险操作。"""
        rows = self.runtime.records.db.rows(
            select(Interaction).where(
                Interaction.run_id == run_id,
                Interaction.kind == "approval",
                Interaction.status == "pending",
            )
        )
        return [self.resolve(row, False, resolution=resolution) for row in rows]

    def publish(self, payloads):
        """在事务提交后广播审批结果。"""
        for payload in payloads:
            self.runtime.events.publish("approval.resolved", payload)

    def arm(self, session, run_id):
        """按最早有效审批调度该运行，已解决审批不延长其他期限。"""
        row = self.runtime.records.db.first(
            select(Interaction.expires_at)
            .where(
                Interaction.run_id == run_id,
                Interaction.kind == "approval",
                Interaction.status == "pending",
                Interaction.expires_at.is_not(None),
            )
            .order_by(Interaction.expires_at)
        )
        with self._condition:
            if self._closed:
                return
            if row:
                self._deadlines[run_id] = (session, datetime.fromisoformat(row["expires_at"]))
                if self._thread is None:
                    self._thread = threading.Thread(
                        target=self._work, daemon=True, name="aurora-approvals"
                    )
                    self._thread.start()
            else:
                self._deadlines.pop(run_id, None)
            self._condition.notify_all()

    def disarm(self, run_id):
        """移除已经结束运行的截止时间。"""
        with self._condition:
            self._deadlines.pop(run_id, None)
            self._condition.notify_all()

    def _work(self):
        """等待最近期限，回调时不占用调度锁。"""
        while True:
            with self._condition:
                if self._closed:
                    return
                if not self._deadlines:
                    self._condition.wait()
                    continue
                run_id, (session, deadline) = min(
                    self._deadlines.items(), key=lambda item: item[1][1]
                )
                delay = (deadline - self.clock()).total_seconds()
                if delay > 0:
                    self._condition.wait(timeout=min(delay, 1))
                    continue
                self._deadlines.pop(run_id, None)
            try:
                session.expire_approvals(run_id)
            except Exception:
                log.warning("审批到期处理失败")
                with self._condition:
                    if not self._closed:
                        self._deadlines[run_id] = (session, self.clock() + timedelta(seconds=1))

    def close(self):
        """停止到期调度并等待现有回调退出。"""
        with self._condition:
            self._closed = True
            self._deadlines.clear()
            self._condition.notify_all()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join()
