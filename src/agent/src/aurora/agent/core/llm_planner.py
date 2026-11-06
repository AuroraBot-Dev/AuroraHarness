"""基于 LLM 的规划器实现：用结构化输出把目标拆成任务列表。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.prompts import ChatPromptTemplate
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    ValidationError,
    field_validator,
)

from aurora.agent.model_access.token_usage import reported_token_usage
from aurora.common.logging import get_logger
from aurora.common.text import sanitize_text

from ..tools.base import Tool, format_tools_for_llm
from .plan_validation import PlanValidationError, validate_plan
from .planner import ClarificationDecision
from .state import Clarification, Task

log = get_logger(__name__)


class PlannedTask(BaseModel):
    """LLM 规划出的单个任务。"""

    model_config = ConfigDict(extra="forbid")

    id: str
    description: str = Field(min_length=1, max_length=4096)
    effort: Literal["low", "medium", "high"]
    tool: str
    args: dict = Field(default_factory=dict)
    depends_on: list[StrictStr | StrictInt] = Field(default_factory=list, strict=True)

    @field_validator("id", mode="before")
    @classmethod
    def _coerce_id(cls, value: object) -> str:
        """仅兼容整数 ID，不将空值和布尔值伪造成标识符。"""
        if isinstance(value, str) or type(value) is int:
            return str(value)
        raise ValueError("任务 ID 必须是字符串或整数")


class Plan(BaseModel):
    """LLM 规划出的任务列表。"""

    model_config = ConfigDict(extra="forbid")

    tasks: list[PlannedTask] = Field(min_length=1, max_length=256)


class ClarificationAssessment(BaseModel):
    """LLM 对当前计划完整性的判断。"""

    needed: bool
    question: str = ""
    reason: str = ""


PLANNER_SYSTEM = """
你是项目级 AI Agent 的规划器。把用户目标拆解为可直接执行的最小子任务。

可用工具（tool 字段只能从下面选）：
{tools}

推理强度 effort 三档：low（简单查询/读取）、medium（需要分析理解）、high（需要设计/实现）。

必须只输出一个 JSON 对象，不要输出任何解释文字。JSON 结构如下：

{{"tasks": [{{"id": "字符串", "description": "任务描述", "effort": "low",
"depends_on": [], "tool": "工具名", "args": {{"参数名": "参数值"}}}}]}}

字段要求：
- id：字符串，任务唯一标识
- description：字符串，必须写清楚任务做什么
- effort：low / medium / high
- tool：只能取上面列出的工具名
- args：传给工具的参数对象，字段名与类型必须和上面「参数」标注一致
- depends_on：同一计划内必须先成功的任务 ID 列表。若测试依赖生成的文件，让测试依赖生成任务。
  新计划为每个任务填写此字段，独立任务填写 []。避免循环和无关的先后约束。

示例：
{{"tasks": [{{"id": "t1", "description": "列出项目文件结构", "effort": "low",
"depends_on": [], "tool": "list_files", "args": {{"path": "."}}}}]}}

最多 {max_tasks} 个任务，任务之间尽量独立以便并行。参数必须符合工具声明，不能添加未知字段。
"""

CLARIFIER_SYSTEM = """
你负责判断执行计划是否缺少必须由用户决定的信息。

只有缺少的信息会显著改变执行目标、范围或结果，且无法安全采用合理默认值时，才需要澄清。
不要询问可从工具获取的信息，不要重复已经问过的问题，每轮最多提出一个简短问题。

必须只输出 JSON：
{{"needed": true, "question": "需要询问的问题", "reason": "判断理由"}}
不需要澄清时输出：
{{"needed": false, "question": "", "reason": "判断理由"}}
"""


class LLMPlanner:
    """基于 LLM 的规划器：用结构化输出把目标拆成任务列表。"""

    def __init__(self, llm, tools: Mapping[str, Tool]) -> None:
        tool_desc = format_tools_for_llm(tools)
        self._tool_names = list(tools.keys())
        self._tools = dict(tools)
        self._max_tasks = 8
        self._llm = llm.with_structured_output(Plan, method="json_mode", include_raw=True)
        self._prompt = ChatPromptTemplate.from_messages(
            [
                ("system", PLANNER_SYSTEM),
                ("human", "{goal}"),
            ]
        )
        self._tool_desc = tool_desc
        self.usage = {}

    def set_max_tasks(self, maximum: int) -> None:
        """将冻结的任务上限同步到规划提示和输出校验。"""
        self._max_tasks = maximum

    def set_instructions(self, instructions: str) -> None:
        """将角色职责加入规划器系统消息。"""
        self._prompt = ChatPromptTemplate.from_messages(
            [("system", PLANNER_SYSTEM), SystemMessage(content=instructions), ("human", "{goal}")]
        )

    def set_images(self, images: list[str | dict]) -> None:
        """把真实图片作为消息加入子任务规划上下文。"""
        self._prompt = ChatPromptTemplate.from_messages(
            [*self._prompt.messages, HumanMessage(content=images)]
        )

    def plan(self, goal: str) -> list[Task]:
        """解析模型输出并在返回前拒绝无效任务或参数。"""
        goal = sanitize_text(goal)
        log.info("LLM 规划开始，目标长度：%d", len(goal))
        log.info("可用工具：%s", ", ".join(self._tool_names))

        chain = self._prompt | self._llm
        result = chain.invoke(
            {"goal": goal, "tools": self._tool_desc, "max_tasks": self._max_tasks}
        )

        if isinstance(result, dict):
            parsed = result.get("parsed")
            raw = result.get("raw")
        else:
            parsed = result
            raw = None

        try:
            plan = Plan.model_validate(parsed)
        except ValidationError:
            raise PlanValidationError(
                "INVALID_PLANNER_RESPONSE", "模型没有返回有效的结构化计划"
            ) from None

        usage = reported_token_usage(raw)
        for key, value in usage.items():
            if isinstance(value, int):
                self.usage[key] = self.usage.get(key, 0) + value
        if usage:
            log.info(
                "token 用量：输入 %s / 输出 %s / 总计 %s",
                usage.get("input_tokens"),
                usage.get("output_tokens"),
                usage.get("total_tokens"),
            )

        log.info("LLM 规划出 %d 个子任务：", len(plan.tasks))
        for i, t in enumerate(plan.tasks, 1):
            log.info("  [%d] [%s] %s", i, t.effort, t.description)
            log.info("      tool=%s", t.tool)

        return validate_plan(
            [task.model_dump(exclude_unset=True) | {"args": task.args} for task in plan.tasks],
            self._tools,
            max_tasks=self._max_tasks,
        )


class LLMClarifier:
    """使用结构化输出判断计划是否需要用户澄清。"""

    def __init__(self, llm) -> None:
        self._llm = llm.with_structured_output(
            ClarificationAssessment,
            method="json_mode",
        )
        self._prompt = ChatPromptTemplate.from_messages(
            [
                ("system", CLARIFIER_SYSTEM),
                (
                    "human",
                    "用户目标：\n{goal}\n\n当前计划：\n{tasks}\n\n已有澄清：\n{clarifications}",
                ),
            ]
        )

    def assess(
        self,
        goal: str,
        tasks: Sequence[Task],
        clarifications: Sequence[Clarification],
    ) -> ClarificationDecision:
        goal = sanitize_text(goal)
        result = (self._prompt | self._llm).invoke(
            {
                "goal": goal,
                "tasks": tasks,
                "clarifications": clarifications,
            }
        )
        return ClarificationDecision(
            needed=result.needed,
            question=result.question.strip(),
            reason=result.reason.strip(),
        )
