"""分发不依赖请求生命周期的运行时通知。"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from typing import Any

from aurora.common.logging import get_logger

log = get_logger(__name__)


class RuntimeEvents:
    """向当前传输连接广播已提交的业务变化。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._listeners = set()

    def subscribe(self, listener: Callable[[str, Mapping[str, Any]], None]):
        """注册接收器并返回幂等注销函数。"""
        with self._lock:
            self._listeners.add(listener)

        def unsubscribe():
            with self._lock:
                self._listeners.discard(listener)

        return unsubscribe

    def publish(self, name, payload):
        """隔离通知失败，已提交的状态不受连接影响。"""
        with self._lock:
            listeners = tuple(self._listeners)
        for listener in listeners:
            try:
                listener(name, payload)
            except Exception:
                log.warning("运行时通知接收器已失效")
