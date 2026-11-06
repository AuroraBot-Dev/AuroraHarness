"""将任务强度映射为真实模型提示、输出与规划上限。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass
from inspect import Parameter, signature
from typing import Any, cast

from langchain_core.messages import BaseMessage, SystemMessage

from .state import Effort

_TOKEN_KEYS = ("max_tokens", "max_completion_tokens", "max_output_tokens")
_MARKER = "\n\n执行强度策略：\n"
_CURRENT: ContextVar[Effort | None] = ContextVar("aurora_task_effort", default=None)
_TASK_KEY: ContextVar[str | None] = ContextVar("aurora_effort_task_key", default=None)


@dataclass(frozen=True)
class EffortPolicy:
    """分别限制每次输出、单份计划与含澄清的规划次数。"""

    effort: Effort
    max_output_tokens: int
    max_steps: int
    instructions: str

    def to_dict(self, *, max_output_tokens: int | None = None):
        """记录实际生效的独立预算，避免混称为总调用次数。"""
        return {
            "effort": str(self.effort),
            "max_output_tokens": max_output_tokens or self.max_output_tokens,
            "max_plan_tasks": self.max_steps,
            "max_planning_rounds": self.max_steps,
        }


_POLICIES = {
    Effort.LOW: EffortPolicy(
        Effort.LOW,
        256,
        3,
        "low：直接完成明确的小任务，只做必要读取与核对，避免额外分解；回答简洁。",
    ),
    Effort.MEDIUM: EffortPolicy(
        Effort.MEDIUM,
        1024,
        6,
        "medium：先核对目标与约束，做必要分解，检查关键结果，再给出依据充分的答案。",
    ),
    Effort.HIGH: EffortPolicy(
        Effort.HIGH,
        2048,
        6,
        "high：系统分析复杂约束和备选方案，检查边界与失败路径，完成关键验证后交付答案。",
    ),
}


def get_effort_policy(effort: Effort | str) -> EffortPolicy:
    """严格解析三个可执行档位。"""
    return _POLICIES[Effort(effort)]


def _cap(values: Mapping, maximum: int) -> int:
    """保留模型已经配置的更低输出上限。"""
    for key in _TOKEN_KEYS:
        value = values.get(key)
        if value is None:
            continue
        if type(value) is not int or value < 1:
            raise ValueError(f"{key} 必须是正整数")
        maximum = min(maximum, value)
    return maximum


def _instructions(value: str, policy: EffortPolicy) -> str:
    """替换本模块生成的尾部策略，保持原角色职责。"""
    return value.split(_MARKER, 1)[0] + _MARKER + policy.instructions


def apply_effort(config: dict, effort: Effort | str) -> dict:
    """复制角色配置并将预算送入实际模型构造参数。"""
    policy = get_effort_policy(effort)
    effective = deepcopy(config)
    parameters = effective["model"].setdefault("parameters", {})
    maximum = _cap(parameters, policy.max_output_tokens)
    for key in _TOKEN_KEYS:
        parameters.pop(key, None)
    parameters["max_tokens"] = maximum
    effective["effort_policy"] = policy.to_dict(max_output_tokens=maximum)
    return effective


@contextmanager
def effort_scope(effort: Effort | str, *, task_key: str | None = None):
    """将当前任务档位隔离到执行上下文。"""
    token = _CURRENT.set(Effort(effort))
    task_token = _TASK_KEY.set(task_key)
    try:
        yield
    finally:
        _CURRENT.reset(token)
        _TASK_KEY.reset(task_token)


def current_task_key() -> str | None:
    """读取计量所归属的当前角色局部任务键。"""
    return _TASK_KEY.get()


def current_effort(maximum: Effort | str = Effort.HIGH) -> Effort:
    """读取当前任务强度，禁止超出所属 worker 的档位。"""
    maximum = Effort(maximum)
    selected = _CURRENT.get() or maximum
    levels = list(Effort)
    return min((selected, maximum), key=levels.index)


def _model_cap(model: Any, maximum: int) -> int:
    """读取客户端及已有 Runnable 绑定中的更低输出限制。"""
    seen = set()
    while model is not None and id(model) not in seen:
        seen.add(id(model))
        maximum = _cap({key: getattr(model, key, None) for key in _TOKEN_KEYS}, maximum)
        for field in ("kwargs", "model_kwargs"):
            values = getattr(model, field, None)
            if isinstance(values, Mapping):
                maximum = _cap(values, maximum)
        model = getattr(model, "bound", None)
    return maximum


def limit_model_output(model: Any, maximum: int) -> Any:
    """复制生产模型及绑定链，使结构化规划也保留输出预算。"""
    maximum = _model_cap(model, maximum)
    fields = getattr(type(model), "model_fields", {})
    copy = getattr(model, "model_copy", None)
    if not callable(copy):
        return model
    update: dict[str, Any] = {key: maximum for key in _TOKEN_KEYS if key in fields}
    if "bound" in fields:
        update["bound"] = limit_model_output(model.bound, maximum)
    if "kwargs" in fields and isinstance(model.kwargs, dict):
        values = dict(model.kwargs)
        for key in _TOKEN_KEYS:
            values.pop(key, None)
        update["kwargs"] = values | {"max_tokens": maximum}
    return copy(update=update) if update else model


def invoke_with_effort(
    model: Any,
    messages: Sequence[BaseMessage],
    effort: Effort | str,
    *,
    max_output_tokens: int | None = None,
) -> Any:
    """执行真实模型调用，保留原响应与用量并兼容单参数注入模型。"""
    policy = get_effort_policy(effort)
    maximum = _cap({"max_tokens": max_output_tokens}, policy.max_output_tokens)
    maximum = _model_cap(model, maximum)
    supplied = list(messages)
    for index, message in enumerate(supplied):
        if isinstance(message, SystemMessage) and isinstance(message.content, str):
            supplied[index] = message.model_copy(
                update={"content": _instructions(message.content, policy)}
            )
            break
    else:
        supplied.insert(0, SystemMessage(content=policy.instructions))
    bind = getattr(model, "bind", None)
    if callable(bind):
        return cast(Any, bind(max_tokens=maximum)).invoke(supplied)
    invoke = model.invoke
    try:
        parameters = signature(invoke).parameters.values()
    except (TypeError, ValueError):
        return invoke(supplied, max_tokens=maximum)
    if any(
        parameter.kind == Parameter.VAR_KEYWORD
        or parameter.name == "max_tokens"
        and parameter.kind != Parameter.POSITIONAL_ONLY
        for parameter in parameters
    ):
        return invoke(supplied, max_tokens=maximum)
    return invoke(supplied)
