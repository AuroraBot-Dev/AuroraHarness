"""运行离线评估或显式选择的模型强度对照实验。"""

from __future__ import annotations

import argparse
import json
import sys


def register(subparsers: argparse._SubParsersAction) -> None:
    """注册用例选择、结构化报告与模型对照参数。"""
    parser = subparsers.add_parser("eval", help="评估任务规划和执行效果")
    parser.add_argument(
        "--live", action="store_true", help="调用配置的模型进行对照评分，会消耗 token"
    )
    parser.add_argument(
        "--list", action="store_true", dest="list_only", help="列出可用的用例或实验任务"
    )
    parser.add_argument("--case", action="append", dest="cases", help="选择离线用例，可重复")
    parser.add_argument("--task", action="append", dest="tasks", help="选择模型对照任务，可重复")
    parser.add_argument("--tiers", help="对照档位，以逗号分隔，默认 low,medium,high")
    parser.add_argument("--repeat", type=int, help="对照实验每个任务和档位的运行次数，默认 1")
    parser.add_argument("--json", action="store_true", dest="as_json", help="输出机器可读的 JSON")
    parser.set_defaults(handler=_run)


def _offline_text(report) -> str:
    """展示逐项实际观测、断言与通过数。"""
    lines = [f"离线评估（{report.total} 项）"]
    for case in report.cases:
        lines.append(f"[{'PASS' if case.passed else 'FAIL'}] {case.id}")
        for check in case.checks:
            expected = json.dumps(check.expected, ensure_ascii=False)
            actual = json.dumps(check.actual, ensure_ascii=False)
            label = " ".join(filter(None, (check.kind, check.task)))
            outcome = "PASS" if check.passed else "FAIL"
            lines.append(f"  {outcome} {label}  期望 {expected}  实际 {actual}")
        if case.error:
            lines.append("  " + case.error)
    lines.append(f"{report.passed_count}/{report.total} 通过")
    return "\n".join(lines)


def _show_ids(mode, ids, as_json) -> int:
    """列出可选标识，不启动评估。"""
    print(json.dumps({"mode": mode, "ids": ids}, ensure_ascii=False) if as_json else "\n".join(ids))
    return 0


def _offline(args: argparse.Namespace) -> int:
    from aurora.eval.offline import FIXTURES, run_offline

    if args.list_only:
        return _show_ids("offline", [case.id for case in FIXTURES], args.as_json)
    report = run_offline(args.cases)
    print(
        json.dumps(report.to_dict(), ensure_ascii=False, allow_nan=False)
        if args.as_json
        else _offline_text(report)
    )
    return 0 if report.passed else 1


def _live(args: argparse.Namespace) -> int:
    from aurora.eval.experiment import EXPERIMENT_TASKS, run_experiment

    if args.list_only:
        return _show_ids("live", list(EXPERIMENT_TASKS), args.as_json)
    tiers = (
        [item.strip() for item in args.tiers.split(",")]
        if args.tiers is not None
        else ["low", "medium", "high"]
    )
    repeat = args.repeat if args.repeat is not None else 1
    experiment = run_experiment(tasks=args.tasks, tiers=tiers, repeat=repeat)
    print(
        json.dumps(experiment.to_dict(), ensure_ascii=False, allow_nan=False)
        if args.as_json
        else experiment.format_text()
    )
    return 0 if experiment.ok else 1


def _run(args: argparse.Namespace) -> int:
    """区分参数错误、验收失败和成功。"""
    live_options = bool(args.tasks) or args.tiers is not None or args.repeat is not None
    try:
        if not args.live and live_options:
            raise ValueError("--task、--tiers 和 --repeat 适用于 --live 对照实验")
        if args.live and args.cases:
            raise ValueError("--case 适用于离线评估，模型对照实验使用 --task")
        if args.list_only and (args.cases or live_options):
            raise ValueError("--list 单独列出该模式的全部项目")
        return _live(args) if args.live else _offline(args)
    except ValueError as exc:
        if args.as_json:
            print(
                json.dumps(
                    {"mode": "live" if args.live else "offline", "error": str(exc)},
                    ensure_ascii=False,
                )
            )
        else:
            print(str(exc), file=sys.stderr)
        return 2
