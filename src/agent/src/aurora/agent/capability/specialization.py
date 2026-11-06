"""按作用域组合上下文、提示、工具和检索策略。"""

from __future__ import annotations

import threading
from collections.abc import Callable, Collection, Mapping
from copy import deepcopy
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import MappingProxyType
from typing import Literal

from aurora.agent.tools.base import Tool
from aurora.common.logging import get_logger

log = get_logger(__name__)
RESERVED_TOOLS = frozenset({"call_agent", "list_agents", "agent_respond"})


@dataclass(frozen=True)
class CapabilityContext:
    """描述当前项目、角色和委派路径。"""

    project_path: Path
    goal: str
    agent_id: str = ""
    task_path: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "project_path", Path(self.project_path).resolve())
        object.__setattr__(self, "task_path", tuple(self.task_path))


@dataclass(frozen=True)
class CapabilityScope:
    """限制策略在全局、项目、子树或指定任务内生效。"""

    kind: Literal["global", "project", "subtree", "task"] = "global"
    project_path: Path | None = None
    task_path: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.kind not in {"global", "project", "subtree", "task"}:
            raise ValueError("未知能力作用域")
        if self.kind == "global" and (self.project_path is not None or self.task_path):
            raise ValueError("全局作用域不能指定项目或任务")
        if self.kind != "global" and self.project_path is None:
            raise ValueError("项目、子树和任务作用域必须指定项目路径")
        if self.kind == "project" and self.task_path:
            raise ValueError("项目作用域不能指定任务")
        if self.kind == "subtree" and not self.task_path:
            raise ValueError("子树作用域必须指定委派路径")
        if any(not isinstance(part, str) or not part for part in self.task_path):
            raise ValueError("委派路径必须由非空任务 ID 组成")
        if self.project_path is not None:
            object.__setattr__(self, "project_path", Path(self.project_path).resolve())
        object.__setattr__(self, "task_path", tuple(self.task_path))

    def matches(self, context: CapabilityContext) -> bool:
        """使用完整路径段匹配作用域，避免相似项目或任务名串用。"""
        if self.kind == "global":
            return True
        if self.project_path != context.project_path:
            return False
        if self.kind == "project":
            return True
        if self.kind == "task":
            return self.task_path == context.task_path
        return context.task_path[: len(self.task_path)] == self.task_path


@dataclass(frozen=True)
class Capability:
    """返回一项策略的上下文、附加指令、工具及可选检索器。"""

    context: str = ""
    instructions: str = ""
    tools: tuple[Tool, ...] = ()
    retrieve: Callable[[CapabilityContext], str] | None = None


@dataclass(frozen=True)
class CapabilityPolicy:
    """为可信 Python 策略绑定唯一名称、优先级和作用域。"""

    name: str
    apply: Callable[[CapabilityContext], Capability]
    scope: CapabilityScope = field(default_factory=CapabilityScope)
    priority: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip() or len(self.name) > 100:
            raise ValueError("能力策略名称应为 1 到 100 个字符")
        if not callable(self.apply) or not isinstance(self.scope, CapabilityScope):
            raise ValueError("能力策略必须提供回调和有效作用域")
        if type(self.priority) is not int:
            raise ValueError("能力策略优先级必须是整数")


@dataclass(frozen=True)
class ResolvedCapabilities:
    """保存一次原子组合的内容和策略执行状态。"""

    context: str
    instructions: str
    tools: Mapping[str, Tool]
    applied: tuple[str, ...]
    failed: tuple[str, ...]


class CapabilityRegistry:
    """注册可插拔策略，并为每轮运行冻结注册清单。"""

    def __init__(self) -> None:
        self._policies: dict[str, CapabilityPolicy] = {}
        self._lock = threading.RLock()

    def register(self, policy: CapabilityPolicy) -> None:
        """注册唯一命名的策略，重复名称明确失败。"""
        with self._lock:
            if policy.name in self._policies:
                raise ValueError(f"能力策略重复注册：{policy.name}")
            self._policies[policy.name] = policy

    def unregister(self, name: str) -> None:
        """移除策略，已冻结的运行继续使用原注册清单。"""
        with self._lock:
            self._policies.pop(name, None)

    def freeze(self) -> CapabilityResolver:
        """按优先级、作用域和名称生成确定顺序的运行快照。"""
        order = {"global": 0, "project": 1, "subtree": 2, "task": 3}
        with self._lock:
            policies = tuple(
                sorted(
                    self._policies.values(),
                    key=lambda p: (p.priority, order[p.scope.kind], len(p.scope.task_path), p.name),
                )
            )
        return CapabilityResolver(policies)


@dataclass(frozen=True)
class CapabilityResolver:
    """组合冻结策略，单个策略失败时保留其他策略和默认能力。"""

    policies: tuple[CapabilityPolicy, ...]

    def resolve(
        self,
        context: CapabilityContext,
        *,
        tools: Mapping[str, Tool] | None = None,
        instructions: str = "",
        allowed_tools: Collection[str] = ("*",),
        max_bytes: int = 16_000,
    ) -> ResolvedCapabilities:
        """校验后合并策略，工具白名单始终在注入完成后应用。"""
        if max_bytes < 0:
            raise ValueError("能力内容预算不能小于 0")
        resolved_tools = dict(tools or {})
        base_names = set(resolved_tools) | RESERVED_TOOLS
        contexts, prompts, applied, failed = [], [], [], []
        used = 0
        for policy in self.policies:
            if not policy.scope.matches(context):
                continue
            try:
                value = policy.apply(context)
                if not isinstance(value, Capability):
                    raise ValueError("策略必须返回 Capability")
                retrieved = value.retrieve(context) if value.retrieve is not None else ""
                if any(
                    not isinstance(text, str)
                    for text in (value.context, value.instructions, retrieved)
                ):
                    raise ValueError("能力上下文、指令和检索结果必须是文本")
                additions = {}
                for tool in value.tools:
                    if (
                        not isinstance(tool, Tool)
                        or not tool.name
                        or tool.name in base_names
                        or tool.name in additions
                    ):
                        raise ValueError("能力工具无效或与内置工具重名")
                    additions[tool.name] = replace(tool, params_schema=deepcopy(tool.params_schema))
                text = "\n\n".join(part for part in (value.context, retrieved) if part)
                extra_context = f"[能力上下文：{policy.name}]\n{text}" if text else ""
                extra_prompt = (
                    f"[能力指令：{policy.name}]\n{value.instructions}" if value.instructions else ""
                )
                size = (
                    len((extra_context + extra_prompt).encode("utf-8"))
                    + (2 if extra_context and contexts else 0)
                    + (2 if extra_prompt and prompts else 0)
                )
                if used + size > max_bytes:
                    raise ValueError("能力内容超过预算")
            except Exception:
                failed.append(policy.name)
                log.warning("能力策略 %s 未能应用，保留默认能力", policy.name)
                continue
            used += size
            if extra_context:
                contexts.append(extra_context)
            if extra_prompt:
                prompts.append(extra_prompt)
            resolved_tools.update(additions)
            applied.append(policy.name)
        return ResolvedCapabilities(
            context="\n\n".join(contexts),
            instructions="\n\n".join(part for part in (instructions, *prompts) if part),
            tools=MappingProxyType(
                {
                    name: tool
                    for name, tool in resolved_tools.items()
                    if "*" in allowed_tools or name in allowed_tools
                }
            ),
            applied=tuple(applied),
            failed=tuple(failed),
        )
