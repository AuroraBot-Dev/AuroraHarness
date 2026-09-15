"""提供实体映射和协议边界序列化。"""

from sqlalchemy import inspect
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """所有持久化实体的声明式基类。"""

    def to_record(self) -> dict:
        """返回数据库列值，不触发关联加载。"""
        return {column.key: getattr(self, column.key) for column in inspect(type(self)).columns}
