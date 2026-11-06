"""读取实际模型统计，保留兼容端点未报告的字段。"""

from typing import Any


def reported_token_usage(response: Any) -> dict[str, int]:
    """优先使用原始计数，仅在输入和输出都已知时合计总量。"""
    metadata = getattr(response, "response_metadata", {}) or {}
    raw = metadata.get("token_usage") if isinstance(metadata, dict) else None
    source = raw if isinstance(raw, dict) else getattr(response, "usage_metadata", {}) or {}
    if not isinstance(source, dict):
        return {}
    result = {}
    for target, aliases in (
        ("input_tokens", ("input_tokens", "prompt_tokens")),
        ("output_tokens", ("output_tokens", "completion_tokens")),
        ("total_tokens", ("total_tokens",)),
    ):
        for alias in aliases:
            value = source.get(alias)
            if type(value) is int and value >= 0:
                result[target] = value
                break
    if "total_tokens" not in result and all(
        key in result for key in ("input_tokens", "output_tokens")
    ):
        result["total_tokens"] = result["input_tokens"] + result["output_tokens"]
    return result
