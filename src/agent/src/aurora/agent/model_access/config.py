"""本地 API 管理层配置：读取环境变量并构建模型客户端。"""

from __future__ import annotations

import os

from dotenv import find_dotenv, load_dotenv
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from aurora.logging import register_secret

# 向上搜索并加载项目根目录的 .env，不依赖 uv run 是否自动加载
load_dotenv(find_dotenv())


def build_llm():
    """构建模型客户端（ChatOpenAI，OpenAI 兼容端点）。

    必需配置缺失时直接抛异常，不静默回退。
    """
    api_key = os.getenv("AGENT_API_KEY") or os.getenv("OPENAI_API_KEY")
    model = os.getenv("AGENT_MODEL")
    # 空值等价于未设置：`.env.example` 里 `AGENT_*=` 是空串，若按「有默认值」处理，
    # 会得到一个非法的 base_url 或空模型名。凡是从 .env 读取的可选配置都要用这种写法。
    base_url = os.getenv("AGENT_BASE_URL") or "https://api.openai.com/v1"

    missing = []
    if not api_key:
        missing.append("AGENT_API_KEY")
    if not model:
        missing.append("AGENT_MODEL")

    if missing:
        raise RuntimeError(
            f"模型配置不完整，缺少：{', '.join(missing)}。"
            "请在项目根目录 .env 中配置（参考 .env.example）。"
        )

    assert api_key is not None and model is not None
    register_secret(api_key)
    return ChatOpenAI(api_key=SecretStr(api_key), model=model, base_url=base_url, temperature=0)


def build_configured_llm(config):
    """使用冻结配置与外部凭据构建独立模型客户端。"""
    provider, model = config["provider"], config["model"]
    ref = provider["credential_ref"]
    if ref.startswith("env:"):
        secret = os.getenv(ref[4:])
    elif ref.startswith("keyring:"):
        import keyring

        secret = keyring.get_password("Aurora", ref[8:])
    else:
        raise ValueError("不支持的凭据引用")
    if not secret:
        raise ValueError("供应商凭据未配置")
    if model["model_name"] == "unconfigured":
        raise ValueError("请先配置模型名称")
    register_secret(secret)
    return ChatOpenAI(
        api_key=SecretStr(secret),
        model=model["model_name"],
        base_url=provider["base_url"],
        **({"temperature": 0} | model["parameters"]),
    )
