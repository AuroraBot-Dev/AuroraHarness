"""持久化业务实体、配置快照与会话历史。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .database import Database, dumps, now, uid

TABLES = {
    "project": ("projects", {"name", "path", "preview_json"}),
    "provider": ("model_providers", {"name", "adapter", "base_url", "credential_ref", "enabled"}),
    "model": (
        "model_configs",
        {
            "provider_id",
            "name",
            "model_name",
            "parameters_json",
            "context_budget_tokens",
            "input_types_json",
            "enabled",
        },
    ),
    "agent": (
        "agents",
        {"name", "instructions", "model_config_id", "allowed_tools_json", "enabled"},
    ),
    "workflow": ("workflows", {"name", "kind", "max_revisions", "enabled"}),
}


def decode(row: Mapping[str, Any]) -> dict[str, Any]:
    """展开数据库中的 JSON 列。"""
    return {
        key.removesuffix("_json"): json.loads(value) if value is not None else None
        for key, value in row.items()
        if key.endswith("_json")
    } | {key: value for key, value in row.items() if not key.endswith("_json")}


def safe_data(value):
    """拒绝非敏感配置中夹带凭据。"""
    if isinstance(value, dict):
        for key, item in value.items():
            if re.sub("[^a-z]", "", key.lower()) in {
                "apikey",
                "authorization",
                "password",
                "secret",
                "token",
                "headers",
                "defaultheaders",
                "extrabody",
            }:
                raise ValueError("敏感字段必须使用凭据引用")
            safe_data(item)
    elif isinstance(value, list):
        for item in value:
            safe_data(item)


class Records:
    """封装数据库中的业务读写规则。"""

    def __init__(self, db: Database):
        self.db = db
        self.ownership = None
        self._temporary = (
            tempfile.TemporaryDirectory(prefix="aurora-artifacts-")
            if db.path == ":memory:"
            else None
        )
        self.artifact_root = (
            Path(self._temporary.name)
            if self._temporary
            else Path(db.path).resolve().parent / "artifacts"
        )
        self.artifact_root.mkdir(parents=True, exist_ok=True)

    def insert(self, table, **values):
        """写入由内部代码指定的业务记录。"""
        with self.db.transaction() as connection:
            connection.execute(
                f"INSERT INTO {table} ({','.join(values)}) VALUES "
                f"({','.join('?' for _ in values)})",
                tuple(values.values()),
            )
        return values

    def update(self, table, record_id, **values):
        """更新由内部代码指定的业务记录。"""
        with self.db.transaction() as connection:
            connection.execute(
                f"UPDATE {table} SET {','.join(key + '=?' for key in values)} WHERE id=?",
                (*values.values(), record_id),
            )

    def get(self, table, record_id) -> dict[str, Any]:
        """按内部表名读取必需记录。"""
        row = self.db.one(f"SELECT * FROM {table} WHERE id=?", (record_id,))
        if row is None:
            raise ValueError("记录不存在")
        return decode(row)

    def setting(self, key, default=None):
        """读取设置。"""
        row = self.db.one("SELECT value_json FROM settings WHERE key=?", (key,))
        return json.loads(row["value_json"]) if row else default

    def set_setting(self, key, value):
        """保存设置。"""
        with self.db.transaction() as connection:
            connection.execute(
                "INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE "
                "SET value_json=excluded.value_json",
                (key, dumps(value)),
            )

    def acquire_runtime(self):
        """在恢复运行之前取得独占运行权。"""
        from .ownership import RuntimeOwnership

        self.ownership = RuntimeOwnership(self.db.path)

    def bootstrap(self):
        """仅在配置为空时导入环境默认值。"""
        with self.db.transaction():
            if self.db.one("SELECT id FROM model_providers LIMIT 1"):
                return
            provider = self.save(
                "provider",
                {
                    "name": "默认供应商",
                    "base_url": os.getenv("AGENT_BASE_URL", "https://api.openai.com/v1"),
                    "credential_ref": "env:AGENT_API_KEY"
                    if os.getenv("AGENT_API_KEY")
                    else "env:OPENAI_API_KEY",
                },
            )
            model = self.save(
                "model",
                {
                    "name": "默认模型",
                    "provider_id": provider["id"],
                    "model_name": os.getenv("AGENT_MODEL", "unconfigured"),
                },
            )
            agent = self.save("agent", {"name": "Aurora", "model_config_id": model["id"]})
            workflow = self.save(
                "workflow", {"name": "单 Agent", "kind": "single", "code_agent_id": agent["id"]}
            )
            self.set_setting("default_workflow_id", workflow["id"])

    def recover(self):
        """把上一进程遗留的活动运行标记为中断。"""
        stamp = now()
        with self.db.transaction() as connection:
            connection.execute(
                "UPDATE agent_runs SET status='interrupted', updated_at=? "
                "WHERE status IN ('queued','running','waiting')",
                (stamp,),
            )
            connection.execute(
                "UPDATE tasks SET status='interrupted', updated_at=? "
                "WHERE status IN ('queued','running','waiting')",
                (stamp,),
            )
            connection.execute(
                "UPDATE interactions SET status='expired', updated_at=? WHERE status='pending'",
                (stamp,),
            )
            connection.execute("UPDATE messages SET status='failed' WHERE status='streaming'")
            connection.execute(
                "UPDATE runs SET status='interrupted', error='运行时已重启', "
                "updated_at=?, ended_at=? WHERE status IN "
                "('queued','running','waiting')",
                (stamp, stamp),
            )

    def list_configs(self, kind):
        """列出配置及流程阶段。"""
        table = TABLES[kind][0]
        records = [decode(row) for row in self.db.all(f"SELECT * FROM {table} ORDER BY created_at")]
        if kind == "workflow":
            for row in records:
                row["steps"] = self.steps(row["id"])
        return records

    def steps(self, workflow_id):
        """读取预设流程的阶段绑定。"""
        return self.db.all(
            "SELECT * FROM workflow_steps WHERE workflow_id=? ORDER BY position", (workflow_id,)
        )

    def save(self, kind, data, record_id=None):
        """校验并保存公开配置，流程只接受预设角色绑定。"""
        safe_data(data)
        table, allowed = TABLES[kind]
        values = {}
        extra = {"code_agent_id", "review_agent_id"} if kind == "workflow" else set()
        for key, value in data.items():
            if key.endswith("_json"):
                raise ValueError("请使用结构化配置字段")
            column = key if key in allowed else key + "_json"
            if column in allowed:
                values[column] = dumps(value) if column.endswith("_json") else value
            elif key not in extra:
                raise ValueError(f"不支持的配置字段: {key}")
        old = self.get(table, record_id) if record_id else {}
        merged = old | data
        for name in ("name",):
            if not isinstance(merged.get(name), str) or not merged[name].strip():
                raise ValueError(f"{name} 不能为空")
        if kind == "project":
            path = Path(merged["path"]).expanduser().resolve()
            if not path.is_dir():
                raise ValueError("工作区目录不存在")
            values["path"] = str(path)
        if kind == "provider":
            from urllib.parse import urlsplit

            url = urlsplit(merged.get("base_url", ""))
            if (
                url.scheme not in {"http", "https"}
                or not url.hostname
                or url.username
                or url.password
                or url.query
                or url.fragment
            ):
                raise ValueError("供应商端点必须是无凭据的 HTTP(S) URL")
            ref = merged.get("credential_ref", "")
            if not re.fullmatch(r"(env:[A-Za-z_][A-Za-z0-9_]*|keyring:[a-zA-Z0-9-]+)", ref):
                raise ValueError("凭据引用应为 env:变量名 或 keyring:供应商ID")
        if kind == "model":
            if not merged.get("model_name"):
                raise ValueError("模型名称不能为空")
            params = merged.get("parameters", {})
            if not isinstance(params, dict) or set(params) - {
                "temperature",
                "max_tokens",
                "top_p",
                "reasoning_effort",
                "timeout",
            }:
                raise ValueError("不支持的模型参数")
            types = merged.get("input_types", ["text"])
            if not isinstance(types, list) or "text" not in types or set(types) - {"text", "image"}:
                raise ValueError("模型输入类型必须包含 text，可选 image")
            if (
                not isinstance(merged.get("context_budget_tokens", 8000), int)
                or merged.get("context_budget_tokens", 8000) <= 0
            ):
                raise ValueError("上下文预算必须为正整数")
        if kind == "agent":
            tools = merged.get("allowed_tools", ["*"])
            if not isinstance(tools, list) or any(not isinstance(item, str) for item in tools):
                raise ValueError("工具权限必须是名称列表")
        if (
            kind == "workflow"
            and record_id
            and data.get("enabled") is False
            and self.setting("default_workflow_id") == record_id
        ):
            raise ValueError("请先切换默认流程")
        stamp = now()
        with self.db.transaction():
            if record_id:
                if kind in {"agent", "workflow"}:
                    values["revision"] = old["revision"] + 1
                self.update(table, record_id, **values, updated_at=stamp)
            else:
                record_id = uid()
                self.insert(table, id=record_id, **values, created_at=stamp, updated_at=stamp)
            if kind == "workflow":
                steps = self.steps(record_id)
                bindings = {step["step_key"]: step["agent_id"] for step in steps}
                code = data.get("code_agent_id", bindings.get("code"))
                review = data.get("review_agent_id", bindings.get("review"))
                workflow_kind = merged["kind"]
                if not code or (workflow_kind == "frontend-review" and not review):
                    raise ValueError("流程缺少编写或审查 Agent")
                self.db.connection.execute(
                    "DELETE FROM workflow_steps WHERE workflow_id=?", (record_id,)
                )
                definitions = (
                    [("code", code)]
                    if workflow_kind == "single"
                    else [("code", code), ("capture", None), ("review", review), ("summary", code)]
                )
                for position, (step, agent) in enumerate(definitions):
                    self.insert(
                        "workflow_steps",
                        id=uid(),
                        workflow_id=record_id,
                        step_key=step,
                        position=position,
                        kind=step,
                        agent_id=agent,
                    )
        result = self.get(table, record_id)
        if kind == "workflow":
            result["steps"] = self.steps(record_id)
        return result

    def project(self, path):
        """按规范路径查找或创建项目。"""
        canonical = str(Path(path).expanduser().resolve())
        row = self.db.one("SELECT * FROM projects WHERE path=?", (canonical,))
        return (
            decode(row)
            if row
            else self.save("project", {"path": canonical, "name": Path(canonical).name})
        )

    def snapshot(self, workflow_id, sandbox_mode, approval_mode):
        """解析并冻结一轮实际使用的流程、角色和模型。"""
        workflow = self.get("workflows", workflow_id)
        if not workflow["enabled"]:
            raise ValueError("流程已停用")
        steps = self.steps(workflow_id)
        for step in steps:
            if not step["agent_id"]:
                continue
            agent = self.get("agents", step["agent_id"])
            model = self.get("model_configs", agent["model_config_id"])
            provider = self.get("model_providers", model["provider_id"])
            if not all(item["enabled"] for item in (agent, model, provider)):
                raise ValueError("流程引用了已停用的 Agent、模型或供应商")
            step["config"] = {"agent": agent, "model": model, "provider": provider}
        return {
            "workflow": workflow,
            "steps": steps,
            "sandbox_mode": sandbox_mode,
            "approval_mode": approval_mode,
        }

    def message(
        self,
        session_id,
        role,
        content,
        *,
        run_id=None,
        agent_run_id=None,
        visibility="public",
        kind="message",
        status="completed",
        blocks=None,
    ):
        """事务内分配消息序号并保存消息。"""
        with self.db.transaction():
            seq = self.db.all(
                "SELECT COALESCE(MAX(seq),0)+1 AS seq FROM messages WHERE session_id=?",
                (session_id,),
            )[0]["seq"]
            return self.insert(
                "messages",
                id=uid(),
                session_id=session_id,
                run_id=run_id,
                agent_run_id=agent_run_id,
                seq=seq,
                role=role,
                content_text=content,
                content_json=dumps(blocks or []),
                visibility=visibility,
                kind=kind,
                status=status,
                created_at=now(),
            )

    def context(self, session_id, budget, current):
        """按完整公共轮次选取历史，保守估算 token 避免丢失本轮要求。"""
        if len(current.encode("utf-8")) > budget:
            raise ValueError("当前需求和交接材料超过上下文预算，请缩小任务或提高预算")
        session = self.get("sessions", session_id)
        rows = self.db.all(
            "SELECT * FROM messages WHERE session_id=? AND seq>? "
            "AND visibility='public' AND status='completed' ORDER BY seq",
            (session_id, session["context_start_seq"]),
        )
        groups = []
        for row in rows:
            if not groups or row["run_id"] != groups[-1][0]["run_id"]:
                groups.append([])
            groups[-1].append(row)
        selected, cost = [], len(current.encode("utf-8"))
        for group in reversed(groups):
            size = sum(len(row["content_text"].encode("utf-8")) + 16 for row in group)
            if cost + size > budget:
                break
            selected = group + selected
            cost += size
        return [
            {"message_id": row["id"], "role": row["role"], "content": row["content_text"]}
            for row in selected
        ]

    def clear_context(self, session_id):
        """推进上下文边界并保留原始历史。"""
        with self.db.transaction():
            self.require_idle(session_id)
            seq = self.db.all(
                "SELECT COALESCE(MAX(seq),0) AS seq FROM messages WHERE session_id=?", (session_id,)
            )[0]["seq"]
            self.update("sessions", session_id, context_start_seq=seq, updated_at=now())

    def require_idle(self, session_id):
        """拒绝修改正在运行的会话。"""
        self.get("sessions", session_id)
        if self.db.one(
            "SELECT id FROM runs WHERE session_id=? AND status IN ('queued','running','waiting')",
            (session_id,),
        ):
            raise ValueError("会话已有活动运行")

    def event(self, run_id, event_type, payload):
        """分配执行事件顺序并持久化。"""
        with self.db.transaction():
            seq = self.db.all(
                "SELECT COALESCE(MAX(seq),0)+1 AS seq FROM run_events WHERE run_id=?", (run_id,)
            )[0]["seq"]
            return self.insert(
                "run_events",
                id=uid(),
                run_id=run_id,
                seq=seq,
                type=event_type,
                payload_json=dumps(payload),
                created_at=now(),
            )

    def artifact(self, run_id, agent_run_id, kind, data, media_type, metadata):
        """原子保存产物文件和可验证的元数据。"""
        artifact_id = uid()
        folder = self.artifact_root / run_id
        folder.mkdir(exist_ok=True)
        path = folder / artifact_id
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(data)
        temporary.replace(path)
        try:
            return self.insert(
                "artifacts",
                id=artifact_id,
                run_id=run_id,
                agent_run_id=agent_run_id,
                kind=kind,
                path=str(path),
                sha256=hashlib.sha256(data).hexdigest(),
                size=len(data),
                media_type=media_type,
                metadata_json=dumps(metadata),
                created_at=now(),
            )
        except BaseException:
            path.unlink(missing_ok=True)
            raise

    def artifact_bytes(self, artifact_id):
        """读取并验证受管理产物文件。"""
        row = self.get("artifacts", artifact_id)
        path = Path(row["path"]).resolve()
        if not path.is_relative_to(self.artifact_root.resolve()):
            raise ValueError("产物路径无效")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != row["sha256"]:
            raise ValueError("产物文件已被修改")
        return row, data

    def delete_session(self, session_id):
        """提交会话删除后清理所属产物。"""
        with self.db.transaction() as connection:
            self.require_idle(session_id)
            paths = self.db.all(
                "SELECT path FROM artifacts WHERE run_id IN "
                "(SELECT id FROM runs WHERE session_id=?)",
                (session_id,),
            )
            connection.execute("DELETE FROM sessions WHERE id=?", (session_id,))
        for item in paths:
            path = Path(item["path"]).resolve()
            if path.is_relative_to(self.artifact_root.resolve()):
                path.unlink(missing_ok=True)

    def close(self):
        """释放数据库和测试产物目录。"""
        self.db.close()
        if self.ownership:
            self.ownership.close()
        if self._temporary:
            self._temporary.cleanup()
