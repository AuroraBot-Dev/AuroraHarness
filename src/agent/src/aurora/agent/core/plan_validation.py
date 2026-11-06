"""在派发任何任务前完整验证计划及工具参数。"""

from __future__ import annotations

import json
from collections.abc import Collection, Mapping
from graphlib import CycleError, TopologicalSorter
from typing import Any, cast

from jsonschema.validators import validator_for
from referencing import Registry
from referencing.exceptions import NoSuchResource

from aurora.agent.tools.base import Tool

from .state import Effort, Task


class PlanValidationError(ValueError):
    """为不可信计划携带稳定拒绝原因。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _local_only(uri):
    """禁止工具声明在验证时访问外部 schema。"""
    raise NoSuchResource(ref=uri)


def _task_id(value: object) -> str:
    """规范化任务引用，兼容旧规划器的整数 ID。"""
    if type(value) is int:
        value = str(value)
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > 128
        or any(ord(char) < 32 for char in value)
    ):
        raise ValueError("任务标识无效")
    return value


def validate_dependencies(
    task_ids: Collection[str], declared: Mapping[str, Any]
) -> dict[str, list[str]]:
    """校验同一计划中的前置引用，保留显式声明及其顺序。"""
    dependencies: dict[str, list[str]] = {}
    for key, value in declared.items():
        if not isinstance(value, list):
            raise PlanValidationError("INVALID_TASK_DEPENDENCY", f"任务 {key} 的前置必须是列表")
        try:
            refs = [_task_id(item) for item in value]
        except ValueError:
            raise PlanValidationError(
                "INVALID_TASK_DEPENDENCY", f"任务 {key} 的前置 ID 无效"
            ) from None
        if key in refs or len(refs) != len(set(refs)) or any(ref not in task_ids for ref in refs):
            raise PlanValidationError(
                "INVALID_TASK_DEPENDENCY", f"任务 {key} 包含自引用、重复或未知的前置任务"
            )
        dependencies[key] = refs
    try:
        TopologicalSorter(dependencies).prepare()
    except CycleError:
        raise PlanValidationError("CYCLIC_TASK_DEPENDENCY", "任务之间存在循环依赖") from None
    return dependencies


def validate_plan(
    value: Any,
    tools: Mapping[str, Tool],
    *,
    max_tasks: int = 8,
    allow_empty: bool = False,
) -> list[Task]:
    """验证全部任务后返回独立副本，不执行工具或隐式忽略字段。"""
    if type(max_tasks) is not int or max_tasks < 1:
        raise ValueError("计划任务上限必须是正整数")
    if not isinstance(value, list):
        raise PlanValidationError("INVALID_PLAN", "规划结果必须是任务列表")
    if not value and not allow_empty:
        raise PlanValidationError("EMPTY_PLAN", "规划没有产生可执行任务")
    if len(value) > max_tasks:
        raise PlanValidationError("PLAN_TASK_LIMIT", f"单份计划最多允许 {max_tasks} 个任务")
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode("utf-8")) > 1_048_576:
            raise PlanValidationError("PLAN_SIZE_LIMIT", "计划内容超过 1 MiB 上限")
        copied = json.loads(encoded)
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, PlanValidationError):
            raise
        raise PlanValidationError("INVALID_PLAN", "计划必须包含有效的 JSON 数据") from None
    result, seen, declared = [], set(), {}
    for index, item in enumerate(copied, 1):
        if not isinstance(item, dict) or set(item) - {
            "id",
            "description",
            "effort",
            "tool",
            "args",
            "source_task_id",
            "depends_on",
        }:
            raise PlanValidationError("INVALID_TASK", f"任务 {index} 的结构或字段无效")
        try:
            task_id = _task_id(item.get("id"))
        except ValueError:
            raise PlanValidationError("INVALID_TASK_ID", f"任务 {index} 的 ID 无效") from None
        if task_id in seen:
            raise PlanValidationError("DUPLICATE_TASK_ID", f"计划包含重复任务 ID：{task_id}")
        seen.add(task_id)
        if "depends_on" in item:
            declared[task_id] = value[index - 1]["depends_on"]
        description, name, args = item.get("description"), item.get("tool"), item.get("args")
        if not isinstance(description, str) or not description.strip() or len(description) > 4096:
            raise PlanValidationError("INVALID_TASK", f"任务 {task_id} 缺少有效描述")
        if not isinstance(name, str) or name not in tools:
            raise PlanValidationError("TOOL_NOT_ALLOWED", f"任务 {task_id} 的工具不存在或未获授权")
        try:
            effort = Effort(item.get("effort"))
        except (ValueError, TypeError):
            raise PlanValidationError("INVALID_EFFORT", f"任务 {task_id} 的推理强度无效") from None
        if not isinstance(args, dict):
            raise PlanValidationError("INVALID_TOOL_ARGUMENTS", f"任务 {task_id} 的参数必须是对象")
        schema = tools[name].params_schema
        try:
            validator = validator_for(schema)
            validator.check_schema(schema)
            failure = next(
                validator(schema, registry=Registry(retrieve=_local_only)).iter_errors(args), None
            )
        except Exception:
            raise PlanValidationError(
                "INVALID_TOOL_SCHEMA", f"工具 {name} 的参数声明无效或引用不可用"
            ) from None
        if failure is not None:
            path = ".".join(map(str, failure.absolute_path)) or "参数对象"
            raise PlanValidationError(
                "INVALID_TOOL_ARGUMENTS",
                f"任务 {task_id} 的 {path} 不符合工具约束（{failure.validator}）",
            )
        try:
            tools[name].prepare_arguments(args)
        except (TypeError, ValueError):
            raise PlanValidationError(
                "INVALID_TOOL_ARGUMENTS", f"任务 {task_id} 的参数与工具签名不匹配"
            ) from None
        if "source_task_id" in item and (
            not isinstance(item["source_task_id"], str) or not item["source_task_id"]
        ):
            raise PlanValidationError("INVALID_TASK", f"任务 {task_id} 的重试来源无效")
        result.append(cast(Task, dict(item, id=task_id, effort=effort)))
    if declared:
        dependencies = validate_dependencies(seen, declared)
        for task in result:
            if task["id"] in dependencies:
                task["depends_on"] = dependencies[task["id"]]
    return result
