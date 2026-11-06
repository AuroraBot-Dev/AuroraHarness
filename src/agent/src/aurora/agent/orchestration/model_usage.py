"""保存角色已观测的规划与回答用量，不推算未知调用。"""

from copy import deepcopy

from aurora.agent.model_access.token_usage import reported_token_usage
from aurora.agent.store.database import dumps, uid
from aurora.agent.store.model import AgentRun

_TOKENS = ("input_tokens", "output_tokens", "total_tokens")


def _counts(value):
    """仅接受模型实际报告的非负整数计数。"""
    if not isinstance(value, dict):
        return {}
    return {key: count for key, count in value.items() if type(count) is int and count >= 0}


def record_usage(records, agent_run, source, operation, usage):
    """原子累加本角色的不同操作，重复恢复不重复计量。"""
    with records.db.transaction():
        saved = records.get(AgentRun, agent_run)["usage"]
        saved = deepcopy(saved or {})
        observations = saved.setdefault("observations", {})
        key = source + ":" + operation
        if key in observations:
            return
        values = _counts(usage)
        observations[key] = {"source": source, "reported": values}
        group = saved.setdefault("sources", {}).setdefault(
            source, {"operations": 0, "reported_operations": 0, "unknown_operations": 0}
        )
        group["operations"] += 1
        group["reported_operations"] += bool(values)
        group["unknown_operations"] += not all(field in values for field in _TOKENS)
        for field, count in values.items():
            group[field] = group.get(field, 0) + count
            if source == "planning":
                saved[field] = saved.get(field, 0) + count
        totals = saved.setdefault("observed_totals", {})
        for field in _TOKENS:
            if field in values:
                totals[field] = totals.get(field, 0) + values[field]
        saved["measurement"] = {
            "scope": "this_role_planning_and_agent_respond",
            "complete": all(not item["unknown_operations"] for item in saved["sources"].values()),
            "excluded": ["clarification", "other_workflow_stages", "descendant_roles"],
        }
        records.update(AgentRun, agent_run, usage_json=dumps(saved))


def read_usage(records, agent_run, fallback=None):
    """优先保留持久计量，兼容原有直接阶段的用量字段。"""
    saved = records.get(AgentRun, agent_run)["usage"]
    return saved if saved else fallback or {}


def invoke_with_usage(records, agent_run, operation, invoke):
    """保存回答响应的原始计数，失败或未报告时明确记未知。"""
    try:
        response = invoke()
    except BaseException:
        record_usage(records, agent_run, "agent_respond", operation, None)
        raise
    record_usage(records, agent_run, "agent_respond", operation, reported_token_usage(response))
    return response


class MeasuredPlanner:
    """将规划器本次报告的增量提交给其所属角色。"""

    def __init__(self, records, agent_run, planner):
        self.records, self.agent_run, self.planner = records, agent_run, planner

    def __getattr__(self, name):
        return getattr(self.planner, name)

    def plan(self, goal):
        """每次真实规划操作单独记账，不把检查点读取算作调用。"""
        before = _counts(getattr(self.planner, "usage", None))
        operation = uid()
        try:
            return self.planner.plan(goal)
        finally:
            after = _counts(getattr(self.planner, "usage", None))
            delta = {key: value - before.get(key, 0) for key, value in after.items()}
            record_usage(self.records, self.agent_run, "planning", operation, delta)
