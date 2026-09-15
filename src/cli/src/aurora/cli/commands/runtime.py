"""runtime 子命令：通过 stdio 提供桌面前端协议。"""

from __future__ import annotations

import argparse
import asyncio
import sys

from aurora.agent.transport import RuntimeApi, serve_ndjson, serve_websocket


def register(subparsers: argparse._SubParsersAction) -> None:
    """注册 runtime 子命令。"""
    parser = subparsers.add_parser(
        "runtime",
        help="启动运行时：默认用 stdin/stdout 交换 NDJSON，指定端口则改用 WebSocket",
    )
    transport = parser.add_mutually_exclusive_group()
    transport.add_argument(
        "--stdio",
        action="store_true",
        help="用标准输入输出交换逐行 JSON（默认行为，显式传入便于外部包装器调用）",
    )
    transport.add_argument("--host", default="127.0.0.1", help="WebSocket 监听地址")
    transport.add_argument("--port", type=int, default=None, help="启用浏览器开发用 WebSocket 服务")
    parser.set_defaults(handler=_run)


def _run(args: argparse.Namespace) -> int:
    """在标准输入输出或 WebSocket 上运行协议循环。"""
    api = RuntimeApi()
    try:
        if args.port is None:
            serve_ndjson(api, sys.stdin, sys.stdout)
        else:
            try:
                asyncio.run(serve_websocket(api, args.host, args.port))
            except KeyboardInterrupt:
                pass
    finally:
        api.close()
    return 0
