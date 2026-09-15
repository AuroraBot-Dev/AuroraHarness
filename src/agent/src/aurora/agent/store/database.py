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

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from .model import Base


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
        self._session: Session | None = None
        try:
            self.migrate()
        except BaseException:
            self.connection.close()
            raise
        self.engine = create_engine(
            "sqlite://", creator=lambda: self.connection, poolclass=StaticPool, hide_parameters=True
        )
        event.listen(self.engine, "begin", self._begin)

    @staticmethod
    def _begin(connection):
        """让 SQLite 事务覆盖读取及写入，避免隐式提交。"""
        connection.exec_driver_sql("BEGIN IMMEDIATE")

    def migrate(self):
        """原子应用尚未执行的 SQL 迁移。"""
        with self.lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                self.connection.execute(
                    "CREATE TABLE IF NOT EXISTS schema_migrations "
                    "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
                )
                for file in sorted((Path(__file__).parent / "migrations").glob("*.sql")):
                    version = int(file.name.split("_")[0])
                    if self.one(
                        "SELECT version FROM schema_migrations WHERE version=?", (version,)
                    ):
                        continue
                    for statement in file.read_text(encoding="utf-8").split(";"):
                        if statement.strip():
                            self.connection.execute(statement)
                    self.connection.execute(
                        "INSERT INTO schema_migrations VALUES (?,?)", (version, now())
                    )
                self.connection.commit()
            except BaseException:
                self.connection.rollback()
                raise

    @contextmanager
    def transaction(self):
        """在连接锁内共享一轮短事务的 ORM Session。"""
        with self.lock:
            if self._session is not None:
                yield self._session
                return
            with Session(self.engine, expire_on_commit=False) as session:
                self._session = session
                try:
                    with session.begin():
                        session.connection()
                        yield session
                        session.flush()
                finally:
                    self._session = None

    def execute(self, statement):
        """在当前工作单元执行模型批量变更。"""
        with self.transaction() as session:
            return session.execute(statement)

    def rows(self, statement):
        """执行模型查询并在 Session 内序列化结果。"""
        with self.transaction() as session:
            result = []
            for row in session.execute(statement):
                values = {}
                for key, value in row._mapping.items():
                    if isinstance(value, Base):
                        values.update(value.to_record())
                    else:
                        values[key] = value
                result.append(values)
            return result

    def first(self, statement):
        """读取模型查询的首条结果。"""
        rows = self.rows(statement.limit(1))
        return rows[0] if rows else None

    def all(self, sql, args=()):
        """提供迁移及测试诊断用的原始记录读取。"""
        with self.lock:
            return [dict(row) for row in self.connection.execute(sql, args).fetchall()]

    def one(self, sql, args=()):
        """提供迁移及测试诊断用的单条读取。"""
        rows = self.all(sql, args)
        return rows[0] if rows else None

    def close(self):
        """关闭数据库连接。"""
        with self.lock:
            self.engine.dispose()
            self.connection.close()
