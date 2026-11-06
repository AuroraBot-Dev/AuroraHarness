"""验证强度改变真实请求、任务边界和恢复后的角色归属。"""

import json
from copy import deepcopy
from types import SimpleNamespace

import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from test_retries import history
from test_worker_effort import worker_runtime

from aurora.agent.core import Effort, LLMPlanner, NoClarifier
from aurora.agent.core.effort_policy import (
    apply_effort,
    current_effort,
    effort_scope,
    get_effort_policy,
    invoke_with_effort,
)
from aurora.agent.model_access.client_factory import build_configured_llm


@pytest.mark.parametrize("tier,cap", [("low", 256), ("medium", 1024), ("high", 2048)])
def test_effort_reaches_real_openai_request_and_preserves_usage(monkeypatch, tier, cap):
    monkeypatch.setenv("EFFORT_TEST_KEY", "local-effort-credential")
    requests = []

    def transport(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "test",
                "model": "effort-test",
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "完成"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 11, "completion_tokens": 2, "total_tokens": 13},
            },
        )

    config = {
        "provider": {"credential_ref": "env:EFFORT_TEST_KEY", "base_url": "http://effort.test/v1"},
        "model": {"model_name": "effort-test", "parameters": {"max_tokens": 4096}},
        "agent": {"instructions": "处理当前任务"},
    }
    original = deepcopy(config)
    effective = apply_effort(config, tier)
    assert config == original
    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        effective["model"]["parameters"]["http_client"] = client
        model = build_configured_llm(effective)
        response = invoke_with_effort(
            model, [SystemMessage(content="只处理当前任务"), HumanMessage(content="执行")], tier
        )
    assert len(requests) == 1
    assert requests[0]["max_completion_tokens"] == cap
    assert get_effort_policy(tier).instructions in requests[0]["messages"][0]["content"]
    assert response.content == "完成"
    assert response.usage_metadata["output_tokens"] == 2


@pytest.mark.parametrize("key", ["max_tokens", "max_completion_tokens", "max_output_tokens"])
def test_lower_existing_limits_and_single_argument_injection_are_preserved(key):
    config = {"model": {"parameters": {key: 96}}, "agent": {"instructions": "职责"}}
    effective = apply_effort(config, Effort.HIGH)
    assert effective["model"]["parameters"] == {"max_tokens": 96}
    calls = []
    result = AIMessage(content="ok")
    model = SimpleNamespace(invoke=lambda messages: calls.append(messages) or result)
    assert invoke_with_effort(model, [], "low") is result
    assert get_effort_policy("low").instructions in calls[0][0].content
    assert config["agent"]["instructions"] == "职责"


def test_injected_keyword_limits_are_clamped_and_invoke_errors_are_not_replayed():
    calls = []

    class Model:
        max_tokens = 80

        def invoke(self, messages, **kwargs):
            calls.append((messages, kwargs))
            raise TypeError("model failed after request")

    with pytest.raises(TypeError, match="after request"):
        invoke_with_effort(Model(), [], "high")
    assert len(calls) == 1 and calls[0][1] == {"max_tokens": 80}
    with effort_scope("high"):
        assert current_effort("low") == Effort.LOW
    assert current_effort() == Effort.HIGH


def test_no_argument_model_factory_caps_actual_structured_planner_requests(tmp_path, monkeypatch):
    runtime, workspace, child, _, _ = worker_runtime(tmp_path, monkeypatch, "low")
    monkeypatch.setenv("EFFORT_TEST_KEY", "local-effort-key")
    requests = []

    def transport(request):
        value = json.loads(request.content)
        requests.append(value)
        task = (
            dict(
                id="child",
                description="委派",
                effort="low",
                tool="call_agent",
                args={"agent_id": child["id"], "task": "写入目标"},
            )
            if len(requests) == 1
            else dict(
                id="write",
                description="写入",
                effort="low",
                tool="write_file",
                args={"path": "actual.txt", "content": "done"},
            )
        )
        return httpx.Response(
            200,
            json={
                "id": "test",
                "model": "local",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": json.dumps({"tasks": [task]})},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 20, "completion_tokens": 40, "total_tokens": 60},
            },
        )

    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        model = build_configured_llm(
            {
                "provider": {
                    "credential_ref": "env:EFFORT_TEST_KEY",
                    "base_url": "http://effort.test/v1",
                },
                "model": {
                    "model_name": "local",
                    "parameters": {"max_tokens": 4096, "http_client": client},
                },
            }
        )
        runtime._configured_llm_factory = None
        runtime._custom_llm = True
        runtime._llm_factory = lambda: model
        runtime._planner_factory = LLMPlanner
        runtime._clarifier_factory = lambda model: NoClarifier()
        try:
            result = runtime.create_session(str(workspace), approval_mode="always").start(
                "实际规划并写入"
            )
            assert result.status == "completed"
            assert (workspace / "actual.txt").read_text(encoding="utf-8") == "done"
            assert [request["max_completion_tokens"] for request in requests] == [4096, 256]
            assert model.max_tokens == 4096
            for role in history(runtime, result.run_id)["agentRuns"]:
                assert role["usage"]["total_tokens"] == 60
                assert role["usage"]["sources"]["planning"]["operations"] == 1
                assert role["usage"]["observed_totals"]["total_tokens"] == 60
            assert get_effort_policy("low").instructions in requests[1]["messages"][1]["content"]
        finally:
            runtime.close()
