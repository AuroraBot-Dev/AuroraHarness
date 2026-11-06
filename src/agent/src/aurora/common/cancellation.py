"""在线程和执行图节点间传递协作取消信号。"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

CANCEL_EVENT: ContextVar[threading.Event | None] = ContextVar("aurora_cancel", default=None)


class OperationCancelled(Exception):
    """当前操作已收到取消请求。"""


def check_cancelled() -> None:
    """在安全点拒绝继续已经取消的操作。"""
    event = CANCEL_EVENT.get()
    if event is not None and event.is_set():
        raise OperationCancelled("用户取消了运行")


@contextmanager
def cancellation_scope(event: threading.Event | None) -> Iterator[None]:
    """临时绑定取消信号，并在退出时恢复调用方上下文。"""
    token = CANCEL_EVENT.set(event if event is not None else CANCEL_EVENT.get())
    try:
        yield
    finally:
        CANCEL_EVENT.reset(token)
