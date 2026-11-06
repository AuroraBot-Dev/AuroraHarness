"""用注入模型验证分档对照、盲评、失败路径和成本统计。"""

import json
from types import SimpleNamespace

import pytest

from aurora.eval.experiment import EXPERIMENT_TASKS, run_experiment


class ScriptedModel:
    """记录传入模型的真实消息和参数，并依次返回指定响应。"""

    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def invoke(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


def message(content, usage=None, metadata=None):
    return SimpleNamespace(content=content, usage_metadata=usage, response_metadata=metadata or {})


def grade(a: object = 4, b: object = 5, c: object = 3):
    return json.dumps(dict(completeness=a, accuracy=b, conciseness=c))


def evaluate(worker, judge, **kwargs):
    return run_experiment(worker_factory=lambda: worker, judge_factory=lambda: judge, **kwargs)


def test_blind_scores_have_identical_inputs_per_task_and_separate_measured_usage(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "README.md").write_text("private caller file", encoding="utf-8")
    worker = ScriptedModel([message("同一候选答案", {"input_tokens": 10, "output_tokens": 7})] * 12)
    judge = ScriptedModel(
        [
            message(
                grade(2 if index % 2 == 0 else 4, 3, 4),
                metadata={
                    "token_usage": {
                        "prompt_tokens": 11,
                        "completion_tokens": 2,
                        "total_tokens": 13,
                    },
                    "model_name": "judge-stub",
                },
            )
            for index in range(12)
        ]
    )
    report = evaluate(worker, judge, repeat=2)
    data = report.to_dict()
    assert report.ok and len(data["trials"]) == 12
    assert len(data["per_task"]) == 6 and len(data["per_tier"]) == 3
    assert set(EXPERIMENT_TASKS) == {"summarize_readme", "compare_architecture"}
    assert [call[1]["max_tokens"] for call in worker.calls] == [
        256,
        256,
        1024,
        1024,
        2048,
        2048,
    ] * 2
    assert len({call[0][0].content for call in worker.calls}) == 3
    for offset in (0, 6):
        inputs = {call[0][-1].content for call in worker.calls[offset : offset + 6]}
        assert len(inputs) == 1 and "private caller file" not in next(iter(inputs))
        judges = {call[0][-1].content for call in judge.calls[offset : offset + 6]}
        assert len(judges) == 1 and "tier" not in json.loads(next(iter(judges)))
    assert len({trial.input_sha256 for trial in report.trials}) == 2
    summary = data["per_task"][0]
    assert summary["mean_score"] == pytest.approx(10 / 3)
    assert summary["worker"]["mean_total_tokens"] == 17
    assert summary["judge"]["mean_total_tokens"] == 13
    assert data["per_tier"][0]["scored"] == data["per_tier"][0]["worker"]["calls"] == 4
    assert all(
        t["seconds"] >= t["worker"]["seconds"] + t["judge"]["seconds"] for t in data["trials"]
    )
    assert all(t["judge"]["model"] == "judge-stub" for t in data["trials"])
    assert {path.name for path in tmp_path.iterdir()} == {"README.md"}
    assert "平均分" in report.format_text()


@pytest.mark.parametrize(
    "invalid",
    [
        grade(True),
        grade(6),
        grade(0),
        grade(3.5),
        '{"completeness":5,"accuracy":5}',
        '{"completeness":5,"accuracy":5,"conciseness":5,"note":"ok"}',
        '{"completeness":5,"accuracy":5,"accuracy":1,"conciseness":5}',
        "good",
    ],
)
def test_invalid_judge_cannot_score_and_retains_measured_judge_tokens(invalid):
    worker = ScriptedModel([message("答案")])
    judge = ScriptedModel([message(invalid, {"total_tokens": 12})])
    report = evaluate(worker, judge, tasks=["summarize_readme"], tiers=["low"])
    (trial,) = report.trials
    group = report.to_dict()["per_task"][0]
    assert not report.ok and trial.error and trial.error.startswith("judge")
    assert trial.score is None and trial.dimensions is None
    assert group["scored"] == 0 and group["failed"] == 1 and group["mean_score"] is None
    assert group["worker"]["mean_total_tokens"] is None
    assert group["judge"]["mean_total_tokens"] == 12


def test_failed_case_is_visible_while_next_repeat_still_runs(monkeypatch):
    key = "secret-not-for-reports"
    monkeypatch.setenv("AGENT_API_KEY", key)
    worker = ScriptedModel(
        [
            OSError("Authorization: " + key),
            message("有效回答 " + key, {"total_tokens": 22}),
            message([{"type": "reasoning", "reasoning": "private reasoning"}]),
        ]
    )
    judge = ScriptedModel([message(grade(5, 4, 3), {"input_tokens": 6})])
    report = evaluate(worker, judge, tasks=["compare_architecture"], tiers=["high"], repeat=3)
    data = report.to_dict()
    assert not report.ok
    assert [t["status"] for t in data["trials"]] == ["failed", "completed", "failed"]
    assert data["trials"][0]["judge"] is data["trials"][2]["judge"] is None
    assert data["trials"][1]["score"] == 4
    assert key not in json.dumps(data, ensure_ascii=False)
    assert key not in judge.calls[0][0][-1].content
    assert data["per_tier"][0]["worker"]["mean_total_tokens"] is None
    assert data["per_tier"][0]["judge"]["mean_input_tokens"] == 6
    assert data["per_tier"][0]["judge"]["mean_total_tokens"] is None
    assert "已评分 1/3" in report.format_text()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"tasks": []},
        {"tasks": ["no-task"]},
        {"tiers": ["low", "low"]},
        {"tiers": []},
        {"tiers": ["unknown"]},
        {"repeat": True},
        {"repeat": 0},
        {"tasks": "summarize_readme"},
    ],
)
def test_invalid_selection_does_not_initialize_models(kwargs):
    def fail():
        pytest.fail("不应创建模型")

    with pytest.raises(ValueError):
        run_experiment(worker_factory=fail, judge_factory=fail, **kwargs)


def test_bad_configuration_has_no_model_calls_or_credentials_in_error():
    worker = ScriptedModel([])

    def bad_factory():
        raise RuntimeError("secret-config-value")

    with pytest.raises(ValueError, match="judge 模型配置") as caught:
        run_experiment(worker_factory=lambda: worker, judge_factory=bad_factory)
    assert worker.calls == [] and "secret-config-value" not in str(caught.value)


def test_openai_compatible_transport_preserves_worker_limits_and_judge_usage():
    import httpx
    from langchain_openai import ChatOpenAI
    from pydantic import SecretStr

    requests = []

    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        judging = "盲评" in body["messages"][0]["content"]
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-fixture",
                "object": "chat.completion",
                "created": 1,
                "model": "local-test-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": grade(5, 3, 4) if judging else "Python 离线日志检查器",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 8 if judging else 3,
                    "completion_tokens": 2,
                    "total_tokens": 10 if judging else 5,
                },
            },
        )

    with httpx.Client(transport=httpx.MockTransport(respond), trust_env=False) as client:

        def model():
            return ChatOpenAI(
                model="fixture",
                api_key=SecretStr("offline-test-key"),
                base_url="https://unused.invalid/v1",
                http_client=client,
                max_retries=0,
            )

        report = run_experiment(
            ["summarize_readme"], ["low", "high"], worker_factory=model, judge_factory=model
        )
    assert report.ok and len(requests) == 4
    worker_requests = requests[::2]
    assert [r.get("max_completion_tokens", r.get("max_tokens")) for r in worker_requests] == [
        256,
        2048,
    ]
    assert worker_requests[0]["messages"][-1] == worker_requests[1]["messages"][-1]
    assert [trial.score for trial in report.trials] == [4, 4]
    assert [
        (row["worker"]["usage"]["total_tokens"], row["judge"]["usage"]["total_tokens"])
        for row in report.to_dict()["trials"]
    ] == [(5, 10), (5, 10)]


def test_partial_provider_usage_is_not_completed_with_adapter_zeroes():
    worker = ScriptedModel(
        [
            message(
                "答案",
                {"input_tokens": 0, "output_tokens": 6, "total_tokens": 6},
                {"token_usage": {"completion_tokens": 6}},
            )
        ]
    )
    judge = ScriptedModel([message(grade())])
    data = evaluate(worker, judge, tasks=["summarize_readme"], tiers=["low"]).to_dict()
    assert data["trials"][0]["worker"]["usage"] == {
        "input_tokens": None,
        "output_tokens": 6,
        "total_tokens": None,
    }
