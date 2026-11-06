"""验证回答用量持久计量、并发和未知字段。"""

from types import SimpleNamespace

from test_retries import history
from test_worker_effort import worker_runtime
from test_workflow_checkpoints import pending_input, release_process_resources


def test_answers_before_checkpoint_are_not_lost_or_counted_twice(tmp_path, monkeypatch):
    runtime, workspace, child, _, calls = worker_runtime(
        tmp_path, monkeypatch, "low", count=2, pause=True
    )
    create = runtime._planner_factory

    def planner(model, tools):
        original = create(model, tools)
        plan = original.plan
        original.plan = lambda goal: (
            plan(goal) if original.root else (lambda tasks: tasks[1:] + tasks[:1])(plan(goal))
        )
        return original

    runtime._planner_factory = planner
    session = runtime.create_session(str(workspace))
    paused = session.start("回答两次后确认写入")
    assert paused.status == "waiting" and len(calls) == 2
    before = next(
        row["usage"]
        for row in history(runtime, paused.run_id)["agentRuns"]
        if row["agentId"] == child["id"]
    )
    assert before["sources"]["agent_respond"]["operations"] == 2
    assert before["observed_totals"] == {"input_tokens": 8, "output_tokens": 4, "total_tokens": 12}
    release_process_resources(runtime, session)
    restarted, _, _, _, calls = worker_runtime(tmp_path, monkeypatch, "low", count=2, pause=True)
    try:
        session = restarted.get_session(session.id)
        request = pending_input(restarted, session.id, paused.run_id)
        result = session.resume(paused.run_id, {"approved": True}, request["interruptId"])
        assert result.status == "completed" and not calls
        saved = next(
            row["usage"]
            for row in history(restarted, result.run_id)["agentRuns"]
            if row["agentId"] == child["id"]
        )
        assert saved == before
        task = next(
            row
            for row in history(restarted, result.run_id)["tasks"]
            if row["tool"] == "agent_respond"
        )
        retry = session.retry(result.run_id, task["id"])
        fresh = history(restarted, retry.run_id)["agentRuns"][0]["usage"]
        assert fresh["sources"]["agent_respond"]["operations"] == 1
        assert fresh["observed_totals"]["total_tokens"] == 6
        assert (
            next(
                row["usage"]
                for row in history(restarted, result.run_id)["agentRuns"]
                if row["agentId"] == child["id"]
            )
            == before
        )
    finally:
        restarted.close()


def test_parallel_answers_save_all_usage_without_aggregating_descendants(tmp_path, monkeypatch):
    runtime, workspace, child, _, calls = worker_runtime(tmp_path, monkeypatch, "low", count=3)
    create = runtime._planner_factory

    def planner(model, tools):
        original = create(model, tools)
        plan = original.plan
        original.plan = lambda goal: [task | {"depends_on": []} for task in plan(goal)]
        return original

    runtime._planner_factory = planner
    try:
        result = runtime.create_session(str(workspace), approval_mode="always").start("并发回答")
        assert result.status == "completed" and len(calls) == 3
        roles = history(runtime, result.run_id)["agentRuns"]
        usage = next(row["usage"] for row in roles if row["agentId"] == child["id"])
        assert usage["sources"]["agent_respond"]["operations"] == 3
        assert usage["observed_totals"]["total_tokens"] == 18
        assert not next(row["usage"] for row in roles if row["agentId"] != child["id"])[
            "observed_totals"
        ]
    finally:
        runtime.close()


def test_partial_raw_answer_usage_does_not_inherit_adapter_zeroes(tmp_path, monkeypatch):
    runtime, workspace, child, _, _ = worker_runtime(tmp_path, monkeypatch, "low")
    create = runtime._configured_llm_factory

    def model(config):
        result = create(config)
        result.invoke = lambda messages, **kwargs: SimpleNamespace(
            content="答案",
            usage_metadata={"input_tokens": 0, "output_tokens": 6, "total_tokens": 6},
            response_metadata={"token_usage": {"completion_tokens": 6}},
        )
        return result

    runtime._configured_llm_factory = model
    try:
        result = runtime.create_session(str(workspace), approval_mode="always").start("部分计数")
        usage = next(
            row["usage"]
            for row in history(runtime, result.run_id)["agentRuns"]
            if row["agentId"] == child["id"]
        )
        assert usage["observed_totals"] == {"output_tokens": 6}
        assert usage["sources"]["agent_respond"]["unknown_operations"] == 1
        assert "input_tokens" not in usage["sources"]["agent_respond"]
        assert "total_tokens" not in usage["sources"]["agent_respond"]
        assert usage["measurement"]["complete"] is False
        assert "clarification" in usage["measurement"]["excluded"]
    finally:
        runtime.close()
