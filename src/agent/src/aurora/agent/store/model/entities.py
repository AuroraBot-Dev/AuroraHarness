"""声明数据库业务实体及其外键关系。"""

from __future__ import annotations

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class Project(Base):
    """映射 projects 记录。"""

    __tablename__ = "projects"
    __table_args__ = (UniqueConstraint("path"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    preview_json: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'{}'"))
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)


class ModelProvider(Base):
    """映射 model_providers 记录。"""

    __tablename__ = "model_providers"
    __table_args__ = (CheckConstraint("adapter='openai-compatible'"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    adapter: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'openai-compatible'")
    )
    base_url: Mapped[str] = mapped_column(Text, nullable=False)
    credential_ref: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)


class ModelConfig(Base):
    """映射 model_configs 记录。"""

    __tablename__ = "model_configs"
    __table_args__ = (CheckConstraint("context_budget_tokens>0"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    provider_id: Mapped[str] = mapped_column(
        Text, ForeignKey("model_providers.id", ondelete="NO ACTION"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    model_name: Mapped[str] = mapped_column(Text, nullable=False)
    parameters_json: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'{}'"))
    context_budget_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("8000")
    )
    input_types_json: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'[\"text\"]'")
    )
    enabled: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)
    model_type: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'text'"))
    provider: Mapped[ModelProvider] = relationship(foreign_keys=[provider_id])


class Agent(Base):
    """映射 agents 记录。"""

    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    callable: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    test_result_json: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'{}'"))
    instructions: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    model_config_id: Mapped[str] = mapped_column(
        Text, ForeignKey("model_configs.id", ondelete="NO ACTION"), nullable=False
    )
    allowed_tools_json: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'[\"*\"]'")
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    enabled: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)
    model_config: Mapped[ModelConfig] = relationship(foreign_keys=[model_config_id])


class Workflow(Base):
    """映射 workflows 记录。"""

    __tablename__ = "workflows"
    __table_args__ = (
        CheckConstraint("kind IN ('single','frontend-review')"),
        CheckConstraint("max_revisions BETWEEN 0 AND 2"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    max_revisions: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("2"))
    enabled: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)


class WorkflowStep(Base):
    """映射 workflow_steps 记录。"""

    __tablename__ = "workflow_steps"
    __table_args__ = (
        CheckConstraint("kind IN ('code','capture','review','summary')"),
        CheckConstraint(
            "(kind='capture' AND agent_id IS NULL) OR (kind!='capture' AND agent_id IS NOT NULL)"
        ),
        UniqueConstraint("workflow_id", "position"),
        UniqueConstraint("workflow_id", "step_key"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    workflow_id: Mapped[str] = mapped_column(
        Text, ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False
    )
    step_key: Mapped[str] = mapped_column(Text, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    agent_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("agents.id", ondelete="NO ACTION"), nullable=True
    )
    agent: Mapped[Agent | None] = relationship(foreign_keys=[agent_id])
    workflow: Mapped[Workflow] = relationship(foreign_keys=[workflow_id])


class ConversationSession(Base):
    """映射 sessions 记录。"""

    __tablename__ = "sessions"
    __table_args__ = (Index("sessions_updated", text("updated_at DESC"), unique=False),)

    id: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    project_id: Mapped[str] = mapped_column(
        Text, ForeignKey("projects.id", ondelete="NO ACTION"), nullable=False
    )
    workflow_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("workflows.id", ondelete="NO ACTION"), nullable=True
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    context_start_seq: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    sandbox_mode: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'workspace-write'")
    )
    approval_mode: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'interactive'")
    )
    archived_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)
    workflow: Mapped[Workflow | None] = relationship(foreign_keys=[workflow_id])
    project: Mapped[Project] = relationship(foreign_keys=[project_id])


class Run(Base):
    """映射 runs 记录。"""

    __tablename__ = "runs"
    __table_args__ = (
        Index("runs_status", "status", unique=False),
        Index("runs_session", "session_id", "created_at", unique=False),
        Index(
            "one_active_run",
            "session_id",
            unique=True,
            sqlite_where=text("status IN ('queued','running','waiting')"),
        ),
        UniqueConstraint("session_id", "request_key"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    session_id: Mapped[str] = mapped_column(
        Text, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    request_key: Mapped[str] = mapped_column(Text, nullable=False)
    goal: Mapped[str] = mapped_column(Text, nullable=False)
    mode: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'run'"))
    status: Mapped[str] = mapped_column(Text, nullable=False)
    config_snapshot_json: Mapped[str] = mapped_column(Text, nullable=False)
    state_json: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'{}'"))
    error: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)
    ended_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    session: Mapped[ConversationSession] = relationship(foreign_keys=[session_id])


class AgentRun(Base):
    """映射 agent_runs 记录。"""

    __tablename__ = "agent_runs"
    __table_args__ = (
        Index("agent_runs_agent_status", "agent_id", "status"),
        Index("agent_runs_run", "run_id", "created_at", unique=False),
        UniqueConstraint("run_id", "step_key", "iteration"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    run_id: Mapped[str] = mapped_column(
        Text, ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    step_key: Mapped[str] = mapped_column(Text, nullable=False)
    agent_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("agents.id", ondelete="NO ACTION"), nullable=True
    )
    iteration: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    input_json: Mapped[str] = mapped_column(Text, nullable=False)
    output: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    error: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    usage_json: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'{}'"))
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)
    parent_task_id: Mapped[str | None] = mapped_column(Text, ForeignKey("tasks.id"))
    config_snapshot_json: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'{}'")
    )
    agent: Mapped[Agent | None] = relationship(foreign_keys=[agent_id])
    run: Mapped[Run] = relationship(foreign_keys=[run_id])


class Message(Base):
    """映射 messages 记录。"""

    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint("role IN ('user','assistant','system','tool')"),
        CheckConstraint("visibility IN ('public','internal')"),
        UniqueConstraint("session_id", "seq"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    session_id: Mapped[str] = mapped_column(
        Text, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("runs.id", ondelete="CASCADE"), nullable=True
    )
    agent_run_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=True
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'message'"))
    content_text: Mapped[str] = mapped_column(Text, nullable=False)
    content_json: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'[]'"))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'completed'"))
    visibility: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'public'"))
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    agent_run: Mapped[AgentRun | None] = relationship(foreign_keys=[agent_run_id])
    run: Mapped[Run | None] = relationship(foreign_keys=[run_id])
    session: Mapped[ConversationSession] = relationship(foreign_keys=[session_id])


class Task(Base):
    """映射 tasks 记录。"""

    __tablename__ = "tasks"
    __table_args__ = (UniqueConstraint("agent_run_id", "task_key"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    agent_run_id: Mapped[str] = mapped_column(
        Text, ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False
    )
    task_key: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    tool: Mapped[str] = mapped_column(Text, nullable=False)
    args_json: Mapped[str] = mapped_column(Text, nullable=False)
    effort: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    output: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    error: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)
    agent_run: Mapped[AgentRun] = relationship(foreign_keys=[agent_run_id])


class Artifact(Base):
    """映射 artifacts 记录。"""

    __tablename__ = "artifacts"
    __table_args__ = (
        Index("artifacts_run", "run_id", unique=False),
        UniqueConstraint("path"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    run_id: Mapped[str] = mapped_column(
        Text, ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    agent_run_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=True
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(Text, nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    media_type: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'{}'"))
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    agent_run: Mapped[AgentRun | None] = relationship(foreign_keys=[agent_run_id])
    run: Mapped[Run] = relationship(foreign_keys=[run_id])


class Review(Base):
    """映射 reviews 记录。"""

    __tablename__ = "reviews"
    __table_args__ = (
        CheckConstraint("verdict IN ('passed','changes_requested','unable_to_review')"),
        UniqueConstraint("agent_run_id"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    agent_run_id: Mapped[str] = mapped_column(
        Text, ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False
    )
    code_agent_run_id: Mapped[str] = mapped_column(
        Text, ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False
    )
    iteration: Mapped[int] = mapped_column(Integer, nullable=False)
    verdict: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    code_agent_run: Mapped[AgentRun] = relationship(foreign_keys=[code_agent_run_id])
    agent_run: Mapped[AgentRun] = relationship(foreign_keys=[agent_run_id])


class ReviewArtifact(Base):
    """映射 review_artifacts 记录。"""

    __tablename__ = "review_artifacts"

    review_id: Mapped[str] = mapped_column(
        Text, ForeignKey("reviews.id", ondelete="CASCADE"), primary_key=True, nullable=False
    )
    artifact_id: Mapped[str] = mapped_column(
        Text, ForeignKey("artifacts.id", ondelete="CASCADE"), primary_key=True, nullable=False
    )
    artifact: Mapped[Artifact] = relationship(foreign_keys=[artifact_id])
    review: Mapped[Review] = relationship(foreign_keys=[review_id])


class ReviewFinding(Base):
    """映射 review_findings 记录。"""

    __tablename__ = "review_findings"
    __table_args__ = (CheckConstraint("severity IN ('blocking','suggestion')"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    review_id: Mapped[str] = mapped_column(
        Text, ForeignKey("reviews.id", ondelete="CASCADE"), nullable=False
    )
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    artifact_id: Mapped[str] = mapped_column(
        Text, ForeignKey("artifacts.id", ondelete="NO ACTION"), nullable=False
    )
    location: Mapped[str] = mapped_column(Text, nullable=False)
    suggestion: Mapped[str] = mapped_column(Text, nullable=False)
    bbox_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    previous_finding_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("review_findings.id", ondelete="SET NULL"), nullable=True
    )
    previous_finding: Mapped[ReviewFinding | None] = relationship(
        foreign_keys=[previous_finding_id], remote_side="ReviewFinding.id"
    )
    artifact: Mapped[Artifact] = relationship(foreign_keys=[artifact_id])
    review: Mapped[Review] = relationship(foreign_keys=[review_id])


class Interaction(Base):
    """映射 interactions 记录。"""

    __tablename__ = "interactions"
    __table_args__ = (
        Index("interactions_run", "run_id", "status", unique=False),
        UniqueConstraint("run_id", "interrupt_id"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    run_id: Mapped[str] = mapped_column(
        Text, ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    agent_run_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=True
    )
    interrupt_id: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    request_json: Mapped[str] = mapped_column(Text, nullable=False)
    response_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'pending'"))
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)
    agent_run: Mapped[AgentRun | None] = relationship(foreign_keys=[agent_run_id])
    run: Mapped[Run] = relationship(foreign_keys=[run_id])


class RunEvent(Base):
    """映射 run_events 记录。"""

    __tablename__ = "run_events"
    __table_args__ = (UniqueConstraint("run_id", "seq"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    run_id: Mapped[str] = mapped_column(
        Text, ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    run: Mapped[Run] = relationship(foreign_keys=[run_id])


class Setting(Base):
    """映射 settings 记录。"""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    value_json: Mapped[str] = mapped_column(Text, nullable=False)


class LegacyImport(Base):
    """映射 legacy_imports 记录。"""

    __tablename__ = "legacy_imports"

    source_key: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    imported_at: Mapped[str] = mapped_column(Text, nullable=False)


class SchemaMigration(Base):
    """映射 schema_migrations 记录。"""

    __tablename__ = "schema_migrations"

    version: Mapped[int] = mapped_column(Integer, primary_key=True, nullable=False)
    applied_at: Mapped[str] = mapped_column(Text, nullable=False)
