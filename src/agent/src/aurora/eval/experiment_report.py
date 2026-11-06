"""汇总实测评估评分、模型用量和耗时。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from statistics import fmean
from typing import Any, Literal


@dataclass(frozen=True)
class TokenUsage:
    """保留供应商返回的输入、输出和总 token 数。"""

    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None

    @classmethod
    def from_response(cls, response: Any) -> TokenUsage:
        """读取 LangChain 标准用量或兼容端点的原始统计。"""
        metadata = getattr(response, "response_metadata", {}) or {}
        raw = metadata.get("token_usage") if isinstance(metadata, dict) else None
        usage = getattr(response, "usage_metadata", {}) or {}
        source = raw if isinstance(raw, dict) else usage

        def count(*names: str) -> int | None:
            if isinstance(source, dict):
                for name in names:
                    value = source.get(name)
                    if type(value) is int and value >= 0:
                        return value
            return None

        prompt = count("input_tokens", "prompt_tokens")
        completion = count("output_tokens", "completion_tokens")
        total = count("total_tokens")
        if total is None and prompt is not None and completion is not None:
            total = prompt + completion
        return cls(prompt, completion, total)


@dataclass
class ModelCall:
    """记录一次已发起的模型请求，失败请求也保留耗时。"""

    seconds: float = 0.0
    usage: TokenUsage = field(default_factory=TokenUsage)
    model: str | None = None


@dataclass
class Trial:
    """记录单个任务、档位与重复编号的观测结果。"""

    task: str
    tier: str
    repetition: int
    status: Literal["completed", "failed"] = "failed"
    dimensions: dict[str, int] | None = None
    answer: str | None = None
    input_sha256: str = ""
    error: str | None = None
    worker: ModelCall | None = None
    judge: ModelCall | None = None
    seconds: float = 0.0

    @property
    def score(self) -> float | None:
        return fmean(self.dimensions.values()) if self.dimensions else None


def _mean_tokens(calls: list[ModelCall], name: str) -> float | None:
    """缺失任一已发请求的统计时保留未知值。"""
    values = [getattr(call.usage, name) for call in calls]
    return fmean(values) if values and None not in values else None


def _summary(trials: list[Trial]) -> dict[str, Any]:
    """按成功次数汇总评分，按实际请求次数分别汇总模型用量。"""
    scores = [trial.score for trial in trials if trial.score is not None]
    summary = {
        "trials": len(trials),
        "scored": len(scores),
        "failed": sum(trial.status == "failed" for trial in trials),
        "mean_score": fmean(scores) if scores else None,
        "mean_seconds": fmean(trial.seconds for trial in trials),
    }
    for role in ("worker", "judge"):
        calls = [call for trial in trials if (call := getattr(trial, role)) is not None]
        summary[role] = {
            "calls": len(calls),
            "mean_seconds": fmean(call.seconds for call in calls) if calls else None,
            **{
                f"mean_{name}": _mean_tokens(calls, name)
                for name in ("input_tokens", "output_tokens", "total_tokens")
            },
        }
    return summary


@dataclass
class ExperimentReport:
    """提供逐次记录和任务档位、跨任务档位两类对照。"""

    trials: list[Trial]

    @property
    def ok(self) -> bool:
        return bool(self.trials) and all(trial.status == "completed" for trial in self.trials)

    def to_dict(self) -> dict[str, Any]:
        """生成可序列化的实验报告。"""
        groups: dict[tuple[str, str], list[Trial]] = {}
        tiers: dict[str, list[Trial]] = {}
        for trial in self.trials:
            groups.setdefault((trial.task, trial.tier), []).append(trial)
            tiers.setdefault(trial.tier, []).append(trial)
        return {
            "mode": "live",
            "ok": self.ok,
            "trials": [asdict(trial) | {"score": trial.score} for trial in self.trials],
            "per_task": [
                dict(task=task, tier=tier, **_summary(rows))
                for (task, tier), rows in groups.items()
            ],
            "per_tier": [dict(tier=tier, **_summary(rows)) for tier, rows in tiers.items()],
        }

    def format_text(self) -> str:
        """展示逐次评分、工作与评审用量以及汇总。"""

        def number(value: float | int | None) -> str:
            return "未知" if value is None else f"{value:.2f}".removesuffix(".00")

        lines = ["逐次调用用量（输入/输出/总 token）"]
        for trial in self.trials:

            def usage(call: ModelCall | None) -> str:
                if call is None:
                    return "未调用"
                return "/".join(
                    number(count)
                    for count in (
                        call.usage.input_tokens,
                        call.usage.output_tokens,
                        call.usage.total_tokens,
                    )
                )

            lines.append(
                f"[{trial.status.upper()}] {trial.task} {trial.tier} #{trial.repetition}"
                f"  得分 {number(trial.score)}  worker {usage(trial.worker)}"
                f"  judge {usage(trial.judge)}  {trial.seconds:.2f}s"
            )
            if trial.error:
                lines.append(f"  {trial.error}")
            if trial.dimensions:
                lines.append(
                    f"  完整性 {trial.dimensions['completeness']}"
                    f"  准确性 {trial.dimensions['accuracy']}"
                    f"  简洁性 {trial.dimensions['conciseness']}"
                )
        report = self.to_dict()
        for name, title in (("per_task", "任务与档位"), ("per_tier", "跨任务档位")):
            lines.append(title + "（用量为每次请求的平均总 token）")
            for row in report[name]:
                label = " ".join(str(row[k]) for k in ("task", "tier") if k in row)
                lines.append(
                    f"  {label}  已评分 {row['scored']}/{row['trials']}"
                    f"  平均分 {number(row['mean_score'])}"
                    f"  worker {number(row['worker']['mean_total_tokens'])}"
                    f"  judge {number(row['judge']['mean_total_tokens'])}"
                    f"  平均 {row['mean_seconds']:.2f}s"
                )
        return "\n".join(lines)
