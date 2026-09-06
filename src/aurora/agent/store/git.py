"""工作区 Git 状态、差异快照与安全回滚。"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from ..tools.base import RiskLevel, Tool

GitView = Literal["run", "workspace"]
DIFF_LIMIT = 256 * 1024


class GitError(ValueError):
    """表示可向用户展示的 Git 操作错误。"""


@dataclass(frozen=True)
class GitSnapshot:
    """记录某一时刻的工作树、索引与引用状态。"""

    tree: str
    head: str
    branch: str
    index_hash: str
    index_data: bytes | None


@dataclass
class RunCheckpoint:
    """记录一轮 Agent 运行前后的 Git 状态。"""

    run_id: str
    before: GitSnapshot
    after: GitSnapshot | None = None
    status: str = "running"
    reverted: bool = False


class GitRepository:
    """提供限定在选定工作区内的 Git 能力。"""

    def __init__(self, workspace: str | Path) -> None:
        self.workspace = Path(workspace).expanduser().resolve()
        self._temporary = tempfile.TemporaryDirectory(prefix="aurora-git-")
        self._temp_root = Path(self._temporary.name)
        self._objects = self._temp_root / "objects"
        self._objects.mkdir()
        self.root = self._discover_root()
        self.scope = self.workspace.relative_to(self.root)
        self._git_dir = Path(
            self._run(["rev-parse", "--path-format=absolute", "--git-dir"]).stdout.strip()
        )
        common_dir = Path(
            self._run(["rev-parse", "--path-format=absolute", "--git-common-dir"]).stdout.strip()
        )
        self._real_objects = common_dir / "objects"
        self._index_path = Path(
            self._run(["rev-parse", "--path-format=absolute", "--git-path", "index"]).stdout.strip()
        )
        self._checkpoints: dict[str, RunCheckpoint] = {}

    @classmethod
    def ensure(cls, workspace: str | Path) -> GitRepository:
        """复用父级仓库，或在工作区根目录初始化仓库。"""
        path = Path(workspace).expanduser().resolve()
        if shutil.which("git") is None:
            raise GitError("系统未安装 Git，无法启用改动查看")
        probe = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=False,
        )
        if probe.returncode != 0:
            initialized = subprocess.run(
                ["git", "-C", str(path), "init"],
                capture_output=True,
                text=True,
                check=False,
            )
            if initialized.returncode != 0:
                raise GitError(initialized.stderr.strip() or "Git 仓库初始化失败")
        return cls(path)

    @classmethod
    def is_repository(cls, workspace: str | Path) -> bool:
        """判断目录是否位于 Git 工作树中。"""
        path = Path(workspace).expanduser().resolve()
        if shutil.which("git") is None:
            return (path / ".git").exists()
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            check=False,
        )
        return result.returncode == 0 or (path / ".git").exists()

    def close(self) -> None:
        """清理会话持有的临时 Git 对象。"""
        self._temporary.cleanup()

    def begin_run(self, run_id: str) -> None:
        """在 Agent 执行前创建基线快照。"""
        if run_id in self._checkpoints:
            raise GitError(f"运行已存在 Git 快照: {run_id}")
        self._checkpoints[run_id] = RunCheckpoint(run_id, self.capture())

    def finish_run(self, run_id: str, status: str) -> None:
        """更新一轮运行结束时的快照和状态。"""
        checkpoint = self._checkpoint(run_id)
        checkpoint.after = self.capture()
        checkpoint.status = status

    def capture(self) -> GitSnapshot:
        """把当前工作树写入会话专用的临时对象库。"""
        index = self._temp_root / f"index-{uuid4().hex}"
        env = self._snapshot_env(index)
        head = self._optional(["rev-parse", "--verify", "HEAD"])
        self._run(["read-tree", head] if head else ["read-tree", "--empty"], env=env)
        self._run(["add", "-A", "--", self._scope_arg()], env=env)
        tree = self._run(["write-tree"], env=env).stdout.strip()
        index.unlink(missing_ok=True)
        index_data = self._index_path.read_bytes() if self._index_path.exists() else None
        return GitSnapshot(
            tree=tree,
            head=head,
            branch=self._optional(["symbolic-ref", "--short", "-q", "HEAD"]),
            index_hash=self._digest(index_data),
            index_data=index_data,
        )

    def status(self, view: GitView = "workspace", run_id: str | None = None) -> dict[str, Any]:
        """返回工作区或指定运行的结构化改动摘要。"""
        before, after, checkpoint = self._comparison(view, run_id)
        current_status = self._porcelain_status()
        files = self._changed_files(before.tree, after.tree, current_status, view)
        latest = self._latest_revertible()
        eligible = bool(
            checkpoint
            and checkpoint.after
            and checkpoint.status in {"completed", "failed"}
            and latest is checkpoint
            and not checkpoint.reverted
        )
        current_matches = bool(
            eligible
            and checkpoint
            and checkpoint.after
            and self._same_state(self.capture(), checkpoint.after)
        )
        can_rollback = eligible and current_matches
        reason = self._rollback_reason(checkpoint, latest)
        if eligible and not current_matches:
            reason = "运行结束后项目已发生变化，不能安全回滚"
        return {
            "view": view,
            "runId": checkpoint.run_id if checkpoint else None,
            "branch": after.branch or "HEAD detached",
            "head": after.head or None,
            "unborn": not bool(after.head),
            "files": files,
            "canRollback": can_rollback,
            "rollbackReason": "" if can_rollback else reason,
        }

    def diff(
        self,
        path: str,
        view: GitView = "workspace",
        run_id: str | None = None,
    ) -> dict[str, Any]:
        """按文件返回 unified diff。"""
        before, after, _ = self._comparison(view, run_id)
        relative = self._validate_changed_path(path, before.tree, after.tree)
        result = self._run(
            [
                "diff",
                "--no-color",
                "--find-renames",
                "--unified=3",
                before.tree,
                after.tree,
                "--",
                relative,
            ],
            env=self._object_env(),
        )
        raw = result.stdout.encode("utf-8", errors="replace")
        truncated = len(raw) > DIFF_LIMIT
        content = raw[:DIFF_LIMIT].decode("utf-8", errors="replace")
        binary = "Binary files " in content or "GIT binary patch" in content
        return {"path": path, "content": content, "binary": binary, "truncated": truncated}

    def rollback(self, run_id: str) -> dict[str, Any]:
        """安全恢复最近一轮运行开始前的工作树和索引。"""
        checkpoint = self._checkpoint(run_id)
        latest = self._latest_revertible()
        if checkpoint is not latest or checkpoint.reverted:
            raise GitError("只能回滚最近一个尚未回滚的已结束运行")
        if checkpoint.status not in {"completed", "failed"} or checkpoint.after is None:
            raise GitError("运行尚未结束，不能回滚")
        current = self.capture()
        if not self._same_state(current, checkpoint.after):
            raise GitError("运行结束后工作区、索引或分支已发生变化，已拒绝覆盖新内容")
        safety = current
        try:
            self._restore(checkpoint.before, current)
        except Exception as exc:
            try:
                self._restore(safety, self.capture())
            except Exception:
                pass
            raise GitError(f"回滚失败，已尝试恢复安全快照: {exc}") from exc
        checkpoint.reverted = True
        return {"runId": run_id, "reverted": True, "files": self.status("workspace")["files"]}

    def status_text(self) -> str:
        """把工作区状态渲染为适合模型读取的文本。"""
        return json.dumps(self.status("workspace"), ensure_ascii=False, indent=2)

    def diff_text(self, path: str) -> str:
        """把单文件工作区差异渲染为适合模型读取的文本。"""
        result = self.diff(path, "workspace")
        suffix = "\n（输出过长已截断）" if result["truncated"] else ""
        return str(result["content"]) + suffix

    def _comparison(
        self, view: GitView, run_id: str | None
    ) -> tuple[GitSnapshot, GitSnapshot, RunCheckpoint | None]:
        """解析视图对应的前后快照。"""
        if view not in {"run", "workspace"}:
            raise GitError("view 必须是 run 或 workspace")
        if view == "run":
            if not run_id:
                raise GitError("run 视图必须提供 runId")
            checkpoint = self._checkpoint(run_id)
            after = checkpoint.after or self.capture()
            return checkpoint.before, after, checkpoint
        after = self.capture()
        base_tree = self._head_tree() or self._empty_tree()
        before = GitSnapshot(base_tree, after.head, after.branch, "", None)
        return before, after, None

    def _changed_files(
        self,
        before: str,
        after: str,
        current_status: dict[str, tuple[bool, bool]],
        view: GitView,
    ) -> list[dict[str, Any]]:
        """解析 tree diff 并补充行数和暂存状态。"""
        output = self._run(
            [
                "diff",
                "--name-status",
                "-z",
                "--find-renames",
                before,
                after,
                "--",
                self._scope_arg(),
            ],
            env=self._object_env(),
        ).stdout
        fields = output.split("\0")
        files: list[dict[str, Any]] = []
        index = 0
        while index < len(fields) and fields[index]:
            code = fields[index]
            index += 1
            old_path = None
            if code.startswith(("R", "C")):
                old_path, repo_path = fields[index], fields[index + 1]
                index += 2
            else:
                repo_path = fields[index]
                index += 1
            display_path = self._display_path(repo_path)
            staged, unstaged = current_status.get(repo_path, (False, False))
            additions, deletions, binary = self._numstat(before, after, repo_path)
            files.append(
                {
                    "path": display_path,
                    "oldPath": self._display_path(old_path) if old_path else None,
                    "status": code[0],
                    "staged": staged if view == "workspace" else False,
                    "unstaged": unstaged if view == "workspace" else False,
                    "untracked": (
                        view == "workspace"
                        and repo_path in current_status
                        and not staged
                        and unstaged
                        and code[0] == "A"
                    ),
                    "binary": binary,
                    "additions": additions,
                    "deletions": deletions,
                }
            )
        return files

    def _porcelain_status(self) -> dict[str, tuple[bool, bool]]:
        """读取当前 index 与工作树状态。"""
        output = self._run(
            ["status", "--porcelain=v1", "-z", "--untracked-files=all", "--", self._scope_arg()]
        ).stdout
        fields = output.split("\0")
        statuses: dict[str, tuple[bool, bool]] = {}
        index = 0
        while index < len(fields) and fields[index]:
            entry = fields[index]
            index += 1
            xy, path = entry[:2], entry[3:]
            if xy[0] in {"R", "C"} and index < len(fields):
                index += 1
            statuses[path] = (xy[0] not in {" ", "?"}, xy[1] != " " or xy == "??")
        return statuses

    def _numstat(self, before: str, after: str, path: str) -> tuple[int, int, bool]:
        """读取单文件增删行数。"""
        output = self._run(
            ["diff", "--numstat", before, after, "--", path], env=self._object_env()
        ).stdout.strip()
        if not output:
            return 0, 0, False
        added, deleted, *_ = output.split("\t")
        if added == "-" or deleted == "-":
            return 0, 0, True
        return int(added), int(deleted), False

    def _restore(self, target: GitSnapshot, current: GitSnapshot) -> None:
        """恢复目标快照中与当前快照不同的路径。"""
        output = self._run(
            ["diff", "--name-only", "-z", target.tree, current.tree, "--", self._scope_arg()],
            env=self._object_env(),
        ).stdout
        for path in filter(None, output.split("\0")):
            exists = (
                self._run(
                    ["cat-file", "-e", f"{target.tree}:{path}"],
                    env=self._object_env(),
                    check=False,
                ).returncode
                == 0
            )
            if exists:
                self._run(["checkout", target.tree, "--", path], env=self._object_env())
            else:
                destination = (self.root / path).resolve()
                if not destination.is_relative_to(self.workspace):
                    raise GitError(f"回滚路径越界: {path}")
                if destination.is_dir() and not destination.is_symlink():
                    shutil.rmtree(destination)
                else:
                    destination.unlink(missing_ok=True)
        if target.index_data is None:
            self._index_path.unlink(missing_ok=True)
        else:
            self._index_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._temp_root / "restore-index"
            temporary.write_bytes(target.index_data)
            os.replace(temporary, self._index_path)

    def _validate_changed_path(self, path: str, before: str, after: str) -> str:
        """把前端相对路径限定到当前工作区的改动集合。"""
        raw = Path(path)
        if not path or raw.is_absolute() or ".." in raw.parts:
            raise GitError("diff 路径无效")
        resolved = (self.workspace / raw).resolve()
        if not resolved.is_relative_to(self.workspace):
            raise GitError("diff 路径越界")
        relative = resolved.relative_to(self.root).as_posix()
        changed = self._run(
            ["diff", "--name-only", "-z", before, after, "--", relative], env=self._object_env()
        ).stdout
        if relative not in changed.split("\0"):
            raise GitError("文件不在当前改动中")
        return relative

    def _checkpoint(self, run_id: str) -> RunCheckpoint:
        """返回指定运行快照。"""
        try:
            return self._checkpoints[run_id]
        except KeyError as exc:
            raise GitError(f"运行没有可用的 Git 快照: {run_id}") from exc

    def _latest_revertible(self) -> RunCheckpoint | None:
        """返回最近一个未回滚的已结束运行。"""
        candidates = [
            item
            for item in self._checkpoints.values()
            if item.status in {"completed", "failed"} and not item.reverted
        ]
        return candidates[-1] if candidates else None

    def _rollback_reason(
        self, checkpoint: RunCheckpoint | None, latest: RunCheckpoint | None
    ) -> str:
        """解释当前运行不能回滚的原因。"""
        if checkpoint is None:
            return "工作区视图不支持按轮回滚"
        if checkpoint.reverted:
            return "该轮改动已回滚"
        if checkpoint.status not in {"completed", "failed"}:
            return "运行尚未结束"
        if checkpoint is not latest:
            return "只能回滚最近一个尚未回滚的运行"
        return "当前状态不可回滚"

    def _same_state(self, left: GitSnapshot, right: GitSnapshot) -> bool:
        """比较回滚所需的全部状态标识。"""
        return (
            left.tree,
            left.head,
            left.branch,
            left.index_hash,
        ) == (right.tree, right.head, right.branch, right.index_hash)

    def _discover_root(self) -> Path:
        """返回包含工作区的 Git 根目录。"""
        result = self._run(["rev-parse", "--show-toplevel"], cwd=self.workspace)
        return Path(result.stdout.strip()).resolve()

    def _head_tree(self) -> str:
        """返回 HEAD tree，未提交仓库返回空字符串。"""
        return self._optional(["rev-parse", "--verify", "HEAD^{tree}"])

    def _empty_tree(self) -> str:
        """在临时对象库创建空 tree。"""
        return self._run(["mktree"], env=self._object_env(), stdin="").stdout.strip()

    def _scope_arg(self) -> str:
        """返回相对仓库根目录的 pathspec。"""
        return self.scope.as_posix() if self.scope.parts else "."

    def _display_path(self, path: str) -> str:
        """把仓库路径转换成工作区相对路径。"""
        if not self.scope.parts:
            return path
        return str(Path(path).relative_to(self.scope)).replace(os.sep, "/")

    def _snapshot_env(self, index: Path) -> dict[str, str]:
        """构造不会写入项目 Git 对象库的快照环境。"""
        return {**self._object_env(), "GIT_INDEX_FILE": str(index)}

    def _object_env(self) -> dict[str, str]:
        """构造临时对象库环境。"""
        return {
            "GIT_OBJECT_DIRECTORY": str(self._objects),
            "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(self._real_objects),
        }

    def _optional(self, args: list[str]) -> str:
        """执行允许缺失结果的 Git 查询。"""
        result = self._run(args, check=False)
        return result.stdout.strip() if result.returncode == 0 else ""

    def _run(
        self,
        args: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        stdin: str | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        """以 argv 形式执行 Git 并统一转换错误。"""
        result = subprocess.run(
            ["git", "-C", str(cwd or getattr(self, "root", self.workspace)), *args],
            capture_output=True,
            text=True,
            input=stdin,
            env={**os.environ, **(env or {})},
            check=False,
        )
        if check and result.returncode != 0:
            raise GitError(result.stderr.strip() or result.stdout.strip() or "Git 命令执行失败")
        return result

    @staticmethod
    def _digest(value: bytes | None) -> str:
        """生成索引状态摘要。"""
        return hashlib.sha256(value if value is not None else b"<missing>").hexdigest()


def build_git_tools(repository: GitRepository) -> dict[str, Tool]:
    """创建绑定到会话工作区的只读 Git 工具。"""

    def git_status() -> str:
        """查看当前分支及工作区未提交改动。"""
        return repository.status_text()

    def git_diff(path: str) -> str:
        """查看一个已改动文件相对 HEAD 的 unified diff。"""
        return repository.diff_text(path)

    return {
        "git_status": Tool(
            "git_status", "查看当前分支及工作区未提交改动", git_status, RiskLevel.READ
        ),
        "git_diff": Tool(
            "git_diff",
            "查看指定已改动文件相对 HEAD 的 unified diff",
            git_diff,
            RiskLevel.READ,
        ),
    }
