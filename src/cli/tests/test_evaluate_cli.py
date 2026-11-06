"""从 CLI 验收离线退出码与本地兼容服务上的文本对照实验。"""

import json
import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from aurora.cli.main import main
from aurora.eval import offline
from aurora.eval.fixtures import tasks_include


def test_offline_default_and_case_selection_work_without_credentials(capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AGENT_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert main(["eval", "--list", "--json"]) == 0
    ids = json.loads(capsys.readouterr().out)["ids"]
    assert len(ids) >= 14
    assert main(["eval", "--case", ids[0], "--json"]) == 0
    selected = json.loads(capsys.readouterr().out)
    assert selected["passed"] and selected["total"] == 1
    assert selected["cases"][0]["id"] == ids[0]
    assert main(["eval"]) == 0
    report = capsys.readouterr().out
    assert f"{len(ids)}/{len(ids)} 通过" in report
    assert not list(tmp_path.iterdir())


def test_failing_assertion_reports_actual_observation_and_nonzero_exit(monkeypatch, capsys):
    case = replace(offline.FIXTURES[0], assertions=(tasks_include("unexpected_task"),))
    run = offline.run_offline
    monkeypatch.setattr(offline, "run_offline", lambda cases: run(cases, fixtures=(case,)))
    assert main(["eval", "--json"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert not result["passed"] and result["passed_count"] == 0
    check = result["cases"][0]["checks"][0]
    assert "unexpected_task" in check["expected"] and "unexpected_task" not in check["actual"]


@pytest.mark.parametrize(
    "flags",
    [
        ["--task", "summarize_readme"],
        ["--tiers", "low"],
        ["--repeat", "2"],
        ["--live", "--case", "sample"],
        ["--case", "unknown"],
    ],
)
def test_invalid_flags_never_start_paid_experiment(flags, capsys):
    assert main(["eval", *flags, "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["error"]


def test_live_text_trials_send_tier_caps_and_measure_worker_and_judge(capsys, monkeypatch):
    calls = []

    class Provider(BaseHTTPRequestHandler):
        """提供只监听本机的确定性聊天补全响应用于传输测试。"""

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append(request)
            reviewing = "completeness" in str(request["messages"][0]["content"])
            content = (
                '{"completeness":4,"accuracy":5,"conciseness":3}'
                if reviewing
                else "松针日志读取 JSONL 并使用本机 SQLite 过滤事件。"
            )
            prompt = 40 if reviewing else 20
            payload = json.dumps(
                {
                    "id": "local",
                    "object": "chat.completion",
                    "created": 1,
                    "model": "loopback",
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "stop",
                            "message": {"role": "assistant", "content": content},
                        }
                    ],
                    "usage": {
                        "prompt_tokens": prompt,
                        "completion_tokens": 5,
                        "total_tokens": prompt + 5,
                    },
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args):
            pass

    monkeypatch.setenv("AGENT_MODEL", "loopback")
    monkeypatch.setenv("AGENT_API_KEY", "local-only-eval")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
    with ThreadingHTTPServer(("127.0.0.1", 0), Provider) as server:
        monkeypatch.setenv("AGENT_BASE_URL", f"http://127.0.0.1:{server.server_port}/v1")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            code = main(
                ["eval", "--live", "--task", "summarize_readme", "--tiers", "low,high", "--json"]
            )
        finally:
            server.shutdown()
            thread.join(timeout=3)
    report = json.loads(capsys.readouterr().out)
    assert code == 0 and len(calls) == 4
    assert [calls[i].get("max_completion_tokens", calls[i].get("max_tokens")) for i in (0, 2)] == [
        256,
        2048,
    ]
    assert calls[0]["messages"][-1] == calls[2]["messages"][-1]
    assert calls[0]["messages"][0] != calls[2]["messages"][0]
    assert [trial["score"] for trial in report["trials"]] == [4, 4]
    for trial in report["trials"]:
        assert trial["worker"]["usage"]["total_tokens"] == 25
        assert trial["judge"]["usage"]["total_tokens"] == 45
