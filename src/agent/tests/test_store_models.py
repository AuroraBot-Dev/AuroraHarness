"""验证 ORM 实体、旧数据库兼容和工作单元事务。"""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError

from aurora.agent.store.database import Database, now, uid
from aurora.agent.store.model import (
    Agent,
    Base,
    ConversationSession,
    ModelConfig,
    ModelProvider,
    Project,
)
from aurora.agent.store.records import Records


def test_existing_sqlite_database_supports_tracked_entities(tmp_path):
    path = tmp_path / "legacy.db"
    stamp = now()
    with closing(sqlite3.connect(path)) as connection, connection:
        migration = Path(__file__).parents[1] / "src/aurora/agent/store/migrations/001_initial.sql"
        connection.executescript(migration.read_text())
        connection.execute(
            "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        connection.execute("INSERT INTO schema_migrations VALUES (1, ?)", (stamp,))
        connection.execute(
            "INSERT INTO projects (id,name,path,created_at,updated_at) VALUES (?,?,?,?,?)",
            ("legacy-project", "旧项目", str(tmp_path), stamp, stamp),
        )
    database = Database(path)
    with database.transaction() as session:
        project = session.get(Project, "legacy-project")
        assert project is not None
        assert project.preview_json == "{}"
        project.name = "已更新"
    database.close()
    reopened = Database(path)
    try:
        with reopened.transaction() as session:
            assert session.get(Project, "legacy-project").name == "已更新"
    finally:
        reopened.close()


def test_entity_relationships_and_nested_rollback(tmp_path):
    database = Database(tmp_path / "entities.db")
    stamp = now()
    provider_id, model_id, agent_id = uid(), uid(), uid()
    try:
        with database.transaction() as session:
            provider = ModelProvider(
                id=provider_id,
                name="供应商",
                base_url="https://example.test",
                credential_ref="env:TEST_KEY",
                created_at=stamp,
                updated_at=stamp,
            )
            model = ModelConfig(
                id=model_id,
                name="模型",
                model_name="test",
                provider=provider,
                created_at=stamp,
                updated_at=stamp,
            )
            session.add(
                Agent(
                    id=agent_id,
                    name="编写",
                    model_config=model,
                    created_at=stamp,
                    updated_at=stamp,
                )
            )
        with database.transaction() as session:
            agent = session.get(Agent, agent_id)
            assert agent.model_config.provider.id == provider_id
            assert agent.allowed_tools_json == '["*"]'
        with pytest.raises(RuntimeError, match="回滚"):
            with database.transaction() as outer:
                outer.get(Agent, agent_id).name = "不能提交"
                with database.transaction() as inner:
                    assert inner is outer
                    inner.get(ModelConfig, model_id).name = "也不能提交"
                    raise RuntimeError("回滚")
        with database.transaction() as session:
            assert session.get(Agent, agent_id).name == "编写"
            assert session.get(ModelConfig, model_id).name == "模型"
    finally:
        database.close()


def test_orm_metadata_matches_migrated_schema(tmp_path):
    database = Database(tmp_path / "schema.db")
    try:
        with database.transaction() as session:
            inspector = inspect(session.connection())
            assert set(inspector.get_table_names()) == set(Base.metadata.tables)
            for name, table in Base.metadata.tables.items():
                actual = {column["name"]: column for column in inspector.get_columns(name)}
                assert set(actual) == set(table.columns.keys())
                for column in table.columns:
                    assert column.type._type_affinity == actual[column.name]["type"]._type_affinity
                actual_unique = {
                    tuple(c["column_names"]) for c in inspector.get_unique_constraints(name)
                }
                expected_unique = {
                    tuple(c.name for c in constraint.columns)
                    for constraint in table.constraints
                    if constraint.__class__.__name__ == "UniqueConstraint"
                }
                assert actual_unique == expected_unique
                assert {i["name"] for i in inspector.get_indexes(name)} == {
                    i.name for i in table.indexes
                }
                assert len(inspector.get_foreign_keys(name)) == len(table.foreign_key_constraints)
    finally:
        database.close()


def test_failed_flush_rolls_back_and_session_can_be_reused(tmp_path):
    database = Database(tmp_path / "rollback.db")
    store = Records(database)
    stamp = now()
    try:
        with pytest.raises(IntegrityError):
            with database.transaction():
                store.insert(
                    Project,
                    id="valid",
                    name="项目",
                    path=str(tmp_path),
                    created_at=stamp,
                    updated_at=stamp,
                )
                store.insert(
                    ConversationSession,
                    id=uid(),
                    project_id="missing",
                    title="失败",
                    created_at=stamp,
                    updated_at=stamp,
                )
        assert database.rows(select(Project)) == []
        project = store.project(tmp_path)
        assert store.get(Project, project["id"])["path"] == str(tmp_path)
    finally:
        store.close()


def test_concurrent_message_sequences_are_unique(tmp_path):
    database = Database(tmp_path / "concurrent.db")
    store = Records(database)
    stamp = now()
    try:
        project = store.project(tmp_path)
        store.insert(
            ConversationSession,
            id="session",
            project_id=project["id"],
            title="并发",
            created_at=stamp,
            updated_at=stamp,
        )
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(
                executor.map(lambda i: store.message("session", "user", str(i)), range(20))
            )
        assert sorted(row["seq"] for row in results) == list(range(1, 21))
    finally:
        store.close()
