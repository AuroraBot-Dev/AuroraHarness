"""在人工输入边界保存和恢复同一轮工作流。"""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import select

from aurora.agent.core.execution_limits import ExecutionLimits, TaskBudget
from aurora.agent.orchestration.tool_tracing import SENSITIVE_KEYS
from aurora.agent.store.database import now
from aurora.agent.store.git import GitRepository
from aurora.agent.store.model import ConversationSession, Interaction, Project, Run
from aurora.common.logging import get_logger

log = get_logger(__name__)
_STATE_KEYS = frozenset(
    {
        "history",
        "iteration",
        "phase",
        "handoff",
        "tasks",
        "results",
        "manual",
        "report",
        "code_tree",
        "artifacts",
        "review_attempt",
        "agent_run_id",
        "code_agent_run_id",
        "review_agent_run_id",
        "interrupt_ids",
        "retry_root",
        "retry_nodes",
        "accepted_with_issues",
    }
)
_MANUAL_PHASES = {
    "preview": {"capture"},
    "capture_approval": {"capture"},
    "review_limit": {"review"},
    "review_unavailable": {"capture", "review"},
}


def _digest(value):
    """对 JSON 内容计算与字典顺序无关的摘要。"""
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def _sensitive(value):
    """识别结构化参数及内嵌 JSON 中的凭据字段。"""
    if isinstance(value, dict):
        return any(
            "".join(c for c in key.lower() if c.isalnum()) in SENSITIVE_KEYS or _sensitive(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_sensitive(item) for item in value)
    if isinstance(value, str) and value.lstrip().startswith(("{", "[")):
        try:
            return _sensitive(json.loads(value))
        except json.JSONDecodeError:
            pass
    return False


class WorkflowCheckpoints:
    """仅允许恢复能够核对工作区、执行策略与人工输入的检查点。"""

    def __init__(self, runtime):
        self.runtime = runtime
        self.records = runtime.records
        path = self.records.db.path
        self.root = None if path == ":memory:" else Path(str(Path(path).resolve()) + ".checkpoints")
        self.external = Path.home() / ".aurora" / "checkpoints" / _digest(str(self.root))

    def directory(self, run_id: str, root: Path):
        """限定文件包在运行时专用目录内。"""
        if (
            root not in (self.root, self.external)
            or str(UUID(run_id)) != run_id
            or root.is_symlink()
        ):
            raise ValueError("检查点路径无效")
        directory = root / run_id
        if directory.is_symlink():
            raise ValueError("检查点目录不能是符号链接")
        return directory

    def _pending(self, run_id):
        """按稳定 ID 读取仍待处理的输入。"""
        return self.records.db.rows(
            select(Interaction)
            .where(Interaction.run_id == run_id, Interaction.status == "pending")
            .order_by(Interaction.id)
        )

    def _owned_packs(self, directory):
        """读取本运行实际创建的归档清单，未知目录不参与清理。"""
        marker = directory / ".aurora-owner"
        if marker.is_symlink() or not marker.is_file() or marker.stat().st_size > 2 * 1024 * 1024:
            return None
        lines = marker.read_bytes().splitlines()
        if not lines or lines[0] != self._owner_token(directory):
            return None
        if any(
            len(line) != 64 or any(c not in b"0123456789abcdef" for c in line) for line in lines[1:]
        ):
            return None
        return {line.decode("ascii") for line in lines[1:]}

    def _owner_token(self, directory):
        """绑定标记到本数据库和运行目录。"""
        return _digest([str(self.root), str(directory)]).encode("ascii")

    def _policy(self, session, snapshot):
        """核对冻结配置与当前允许的模型、工具及工作流。"""
        current = session._snapshot(snapshot["workflow"]["id"])
        for key in ("sandbox_mode", "approval_mode", "preview", "execution_limits", "agents"):
            if current[key] != snapshot[key]:
                raise ValueError("运行配置已变化")
        if not snapshot.get("retry"):
            if any(current[key] != snapshot[key] for key in ("workflow", "steps")):
                raise ValueError("流程或权限已变化")
        else:
            for step in snapshot["steps"]:
                if (
                    step.get("config")
                    and self.records.agent_config(step["agent_id"]) != step["config"]
                ):
                    raise ValueError("重试角色配置已变化")
        if (
            self.runtime.capabilities.freeze().policies
            or self.runtime.list_connected_mcp_packages()
            or self.runtime._mcp_tools
        ):
            raise ValueError("扩展工具或动态能力需要重新发起运行")

    def save(self, session, run_id, state):
        """随等待状态提交可恢复执行位置，无法持久化时保留进程内交互。"""
        if not self.root:
            return
        try:
            if state["capabilities"].policies or session.feedback_sink or self.runtime._mcp_tools:
                raise ValueError("包含进程内回调")
            manual = state.get("manual")
            data = {key: state[key] for key in _STATE_KEYS if key in state}
            graph = None
            if manual is None:
                from .graph_checkpoint import export_graphs

                graph = export_graphs(state)
            elif state["phase"] not in _MANUAL_PHASES[manual]:
                raise ValueError("阶段不支持恢复")
            pending = self._pending(run_id)
            if not pending:
                raise ValueError("缺少待处理输入")
            payload = dict(
                session_id=session.id,
                run_id=run_id,
                state=data,
                graph=graph,
                budget=state["task_budget"].snapshot(),
                snapshot_hash=_digest(state["snapshot"]),
                interactions=[item["id"] for item in pending],
            )
            encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False)
            if (
                len(encoded.encode()) > 32 * 1024 * 1024
                or self.runtime.redact(encoded) != encoded
                or _sensitive(payload)
            ):
                raise ValueError("检查点过大或含已登记凭据")
            root = self.external if self.root.is_relative_to(session.repository.root) else self.root
            directory = self.directory(run_id, root)
            if directory.resolve().is_relative_to(session.repository.root):
                raise ValueError("检查点文件必须位于工作区外")
            root.mkdir(mode=0o700, parents=True, exist_ok=True)
            if not directory.exists():
                directory.mkdir(mode=0o700)
                with (directory / ".aurora-owner").open("xb") as marker:
                    marker.write(self._owner_token(directory) + b"\n")
            packs = self._owned_packs(directory)
            if packs is None:
                raise ValueError("检查点目录已有其他所有者")
            payload["git"] = session.repository.export_checkpoint(run_id, directory)
            pack = payload["git"]["pack"]
            if pack not in packs:
                with (directory / ".aurora-owner").open("ab") as marker:
                    marker.write(pack.encode("ascii") + b"\n")
            payload["checksum"] = _digest(payload)
            self.records.save_checkpoint(run_id, payload)
        except Exception as error:
            log.warning("运行 %s 的等待点仅在当前进程有效 (%s)", run_id, type(error).__name__)
            self.records.delete_checkpoint(run_id)
            self.discard_files(run_id)

    def discard_files(self, run_id):
        """删除已失效运行的工作区基线包。"""
        if not self.root:
            return
        try:
            for root in (self.root, self.external):
                directory = self.directory(run_id, root)
                packs = self._owned_packs(directory)
                if packs is None:
                    continue
                for digest in packs:
                    path = directory / f"{digest}.pack"
                    if path.is_file() and not path.is_symlink():
                        path.unlink()
                (directory / ".aurora-owner").unlink()
                if not any(directory.iterdir()):
                    directory.rmdir()
        except (OSError, ValueError):
            log.warning("运行 %s 的检查点文件待清理", run_id)

    def verify_resume(self, session, state):
        """再次检查重启后等待期间发生的权限与工作区变化。"""
        self._policy(session, state["snapshot"])
        if session.repository.capture() != state["restored_workspace"]:
            raise ValueError("恢复后的工作区已变化")

    def _restore(self, run_id):
        """先完整验证检查点，再登记到活动会话。"""
        if self.root is None:
            raise ValueError("内存数据库不包含持久检查点")
        payload = self.records.load_checkpoint(run_id)
        run = self.records.get(Run, run_id)
        if (
            not payload
            or run["status"] != "waiting"
            or payload["run_id"] != run_id
            or payload["session_id"] != run["session_id"]
        ):
            raise ValueError("检查点归属无效")
        if payload.pop("checksum", None) != _digest(payload):
            raise ValueError("检查点内容校验失败")
        snapshot = run["config_snapshot"]
        if payload["snapshot_hash"] != _digest(snapshot):
            raise ValueError("检查点配置不一致")
        pending = self._pending(run_id)
        if not pending or [item["id"] for item in pending] != payload["interactions"]:
            raise ValueError("待处理输入已变化")
        record = self.records.get(ConversationSession, run["session_id"])
        workspace = self.records.get(Project, record["project_id"])["path"]
        if not GitRepository.is_repository(workspace):
            raise ValueError("原工作区仓库已不可用")
        session = self.runtime.get_session(run["session_id"])
        self._policy(session, snapshot)
        data = payload["state"]
        if set(data) - _STATE_KEYS or any(
            run["state"].get(key) != data[key] for key in ("tasks", "results", "phase", "iteration")
        ):
            raise ValueError("检查点状态无效")
        manual = data.get("manual")
        if manual is not None and (
            data["phase"] not in _MANUAL_PHASES[manual] or len(pending) != 1
        ):
            raise ValueError("检查点位置不符")
        for key in ("agent_run_id", "code_agent_run_id", "review_agent_run_id"):
            if data.get(key) and self.records.get("agent_runs", data[key])["run_id"] != run_id:
                raise ValueError("角色执行不属于此运行")
        state = data | dict(
            snapshot=snapshot,
            goal=run["goal"],
            mode=run["mode"],
            role_path=(),
            delegation_depth=0,
            cancel_event=threading.Event(),
            executing=False,
            capabilities=self.runtime.capabilities.freeze(),
            task_budget=TaskBudget.restore(
                ExecutionLimits(**snapshot["execution_limits"]), payload["budget"]
            ),
        )
        if manual is None:
            from .graph_checkpoint import restore_graphs

            if data["phase"] != "code" or not payload["graph"]:
                raise ValueError("缺少工具图检查点")
            restore_graphs(session, run_id, state, payload["graph"])
        root = self.external if self.root.is_relative_to(session.repository.root) else self.root
        directory = self.directory(run_id, root)
        if payload["git"]["pack"] not in (self._owned_packs(directory) or set()):
            raise ValueError("检查点目录归属不符")
        session.repository.restore_checkpoint(run_id, payload["git"], directory)
        state["restored_workspace"] = session.repository.capture()
        approvals = self.runtime.approvals
        if any(row["kind"] == "approval" and not row["expires_at"] for row in pending):
            raise ValueError("检查点审批缺少截止时间")
        overdue = any(
            row["kind"] == "approval"
            and (
                not row["expires_at"]
                or datetime.fromisoformat(row["expires_at"]) <= approvals.clock()
            )
            for row in pending
        )
        if not overdue:
            with self.records.db.transaction():
                mapping = {}
                ids = []
                for row in pending:
                    if row["kind"] == "approval":
                        approvals.resolve(row, False, resolution="runtime_restart")
                    else:
                        self.records.update(
                            Interaction, row["id"], status="expired", updated_at=now()
                        )
                    fresh = approvals.create(
                        run_id,
                        row["kind"],
                        json.loads(row["request_json"]),
                        agent_run_id=row["agent_run_id"],
                        expires_at=row["expires_at"],
                    )
                    ids.append(fresh["id"])
                    mapping[fresh["interrupt_id"]] = state.get("interrupt_ids", {}).get(
                        row["interrupt_id"], row["interrupt_id"]
                    )
                state["interrupt_ids"] = mapping
                data["interrupt_ids"] = mapping
                payload["interactions"] = sorted(ids)
                payload["checksum"] = _digest(payload)
                self.records.save_checkpoint(run_id, payload)
        session._active[run_id] = state
        session._run_ids.add(run_id)
        return session

    def recover(self):
        """重建有效等待点，其他遗留执行按中断收束。"""
        restored = {}
        for run_id in self.records.checkpoint_run_ids():
            try:
                restored[run_id] = self._restore(run_id)
            except Exception as error:
                log.warning("运行 %s 的旧检查点无法恢复 (%s)", run_id, type(error).__name__)
        self.records.recover(preserved_run_ids=restored)
        for root in (self.root, self.external):
            if not self.root or root is None or not root.is_dir() or root.is_symlink():
                continue
            try:
                for directory in root.iterdir():
                    if directory.is_dir() and directory.name not in restored:
                        self.discard_files(directory.name)
            except OSError:
                log.warning("旧检查点目录暂时无法清理")
        for run_id, session in restored.items():
            session.expire_approvals(run_id)
            self.runtime.approvals.arm(session, run_id)
