"""管理本地 SQLite、事务和版本化迁移。"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


def now() -> str:
    """返回 UTC 时间。"""
    return datetime.now(UTC).isoformat()


def uid() -> str:
    """生成业务标识。"""
    return str(uuid4())


def dumps(value) -> str:
    """编码持久化 JSON。"""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def database_path() -> Path:
    """确定各入口共用的数据库路径。"""
    if value := os.getenv("AURORA_DATABASE_PATH"):
        return Path(value).expanduser()
    if sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support"
    elif sys.platform == "win32":
        root = Path(os.getenv("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
    else:
        root = Path(os.getenv("XDG_DATA_HOME", str(Path.home() / ".local" / "share")))
    return root / "Aurora" / "aurora.db"


class Database:
    """提供串行短事务及幂等迁移。"""

    def __init__(self, path: str | Path | None = None):
        self.path = str(path if path is not None else database_path())
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.connection = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA busy_timeout=5000")
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.migrate()

    def migrate(self):
        """原子应用尚未执行的 SQL 迁移。"""
        with self.transaction():
            self.connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            for file in sorted((Path(__file__).parent / "migrations").glob("*.sql")):
                version = int(file.name.split("_")[0])
                if self.one("SELECT version FROM schema_migrations WHERE version=?", (version,)):
                    continue
                for statement in file.read_text(encoding="utf-8").split(";"):
                    if statement.strip():
                        self.connection.execute(statement)
                self.connection.execute(
                    "INSERT INTO schema_migrations VALUES (?,?)", (version, now())
                )

    @contextmanager
    def transaction(self):
        """在独占连接锁内提交或回滚短事务，支持嵌套。"""
        with self.lock:
            nested = self.connection.in_transaction
            if not nested:
                self.connection.execute("BEGIN IMMEDIATE")
            try:
                yield self.connection
                if not nested:
                    self.connection.execute("COMMIT")
            except BaseException:
                if not nested:
                    self.connection.execute("ROLLBACK")
                raise

    def all(self, sql, args=()):
        """读取记录列表。"""
        with self.lock:
            return [dict(row) for row in self.connection.execute(sql, args).fetchall()]

    def one(self, sql, args=()):
        """读取可空单条记录。"""
        rows = self.all(sql, args)
        return rows[0] if rows else None

    def close(self):
        """关闭数据库连接。"""
        with self.lock:
            self.connection.close()
