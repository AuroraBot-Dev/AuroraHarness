"""防止多个运行时同时接管同一本地数据库。"""

from __future__ import annotations

import os


class RuntimeOwnership:
    """持有进程级数据库运行权并在关闭时释放。"""

    def __init__(self, path):
        self.file = None
        if path == ":memory:":
            return
        self.file = open(path + ".runtime.lock", "a+b")
        try:
            if os.name == "nt":
                import msvcrt

                self.file.seek(0)
                self.file.write(b"0")
                self.file.flush()
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            self.file = None
            raise ValueError("数据库已由另一个 Aurora 运行时使用") from None

    def close(self):
        """释放系统文件锁。"""
        if self.file:
            if os.name == "nt":
                import msvcrt

                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
            self.file.close()
            self.file = None
