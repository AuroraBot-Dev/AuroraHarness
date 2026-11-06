"""冻结一轮委派的结构上限并原子分配任务预算。"""

from __future__ import annotations

import threading
from dataclasses import asdict, dataclass


class ExecutionLimitError(ValueError):
    """携带明确资源拒绝原因。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ExecutionLimits:
    """根角色深度为零，每份计划和整轮共享任务数量上限。"""

    max_depth: int = 4
    max_plan_tasks: int = 8
    max_total_tasks: int = 128

    def __post_init__(self):
        for key, lower, upper in (
            ("max_depth", 0, 16),
            ("max_plan_tasks", 1, 256),
            ("max_total_tasks", 1, 4096),
        ):
            value = getattr(self, key)
            if type(value) is not int or not lower <= value <= upper:
                raise ValueError(f"{key} 必须是 {lower} 到 {upper} 之间的整数")

    def to_dict(self):
        """为持久快照生成独立配置。"""
        return asdict(self)


class TaskBudget:
    """重规划替换同角色预留，后代共用整轮的线程安全预算。"""

    def __init__(self, limits: ExecutionLimits):
        self.limits = limits
        self._plans = {}
        self._lock = threading.Lock()

    def check_depth(self, depth: int):
        """在构造子模型和子计划之前拒绝过深的委派。"""
        if depth > self.limits.max_depth:
            raise ExecutionLimitError(
                "DELEGATION_DEPTH_LIMIT", f"委派深度超过上限 {self.limits.max_depth}"
            )

    def snapshot(self):
        """读取每个角色已预留的计划容量。"""
        with self._lock:
            return dict(self._plans)

    @classmethod
    def restore(cls, limits, plans):
        """从已校验的结构上限恢复整轮预算。"""
        if (
            not isinstance(plans, dict)
            or any(
                not isinstance(key, str)
                or not key
                or type(count) is not int
                or not 0 <= count <= limits.max_plan_tasks
                for key, count in plans.items()
            )
            or sum(plans.values()) > limits.max_total_tasks
        ):
            raise ValueError("检查点任务预算无效")
        budget = cls(limits)
        budget._plans = dict(plans)
        return budget

    def reserve(self, role_id: str, tasks):
        """完整计划验证后一次预留节点，失败不会部分消耗预算。"""
        with self._lock:
            total = sum(self._plans.values()) - self._plans.get(role_id, 0) + len(tasks)
            if total > self.limits.max_total_tasks:
                raise ExecutionLimitError(
                    "RUN_TASK_LIMIT", f"整轮任务数量超过上限 {self.limits.max_total_tasks}"
                )
            self._plans[role_id] = len(tasks)
