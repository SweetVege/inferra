"""Metadata schema. Analytical data never lives here; it stays in Parquet files or the
customer's warehouse. These tables describe workspaces, datasets, and the agent runs over them."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def new_id() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    github_id: Mapped[int | None] = mapped_column(Integer, unique=True)
    login: Mapped[str] = mapped_column(String(100), unique=True)
    name: Mapped[str | None] = mapped_column(String(200))
    avatar_url: Mapped[str | None] = mapped_column(String(500))
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)

    memberships: Mapped[list["Membership"]] = relationship(back_populates="user")


class Workspace(TimestampMixin, Base):
    __tablename__ = "workspaces"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200))
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    auto_approve_plans: Mapped[bool] = mapped_column(Boolean, default=False)

    memberships: Mapped[list["Membership"]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan", passive_deletes=True
    )


class Membership(TimestampMixin, Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("user_id", "workspace_id"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(20))  # owner | editor | viewer

    user: Mapped[User] = relationship(back_populates="memberships")
    workspace: Mapped[Workspace] = relationship(back_populates="memberships")


class Connection(TimestampMixin, Base):
    """An external database. Data is pulled into Parquet extracts; the password is encrypted at rest."""

    __tablename__ = "connections"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(20), default="postgres")
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # host, port, database, user, sslmode
    secret: Mapped[str] = mapped_column(Text, default="")  # encrypted password
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class Dataset(TimestampMixin, Base):
    __tablename__ = "datasets"
    __table_args__ = (UniqueConstraint("workspace_id", "table_name"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    source_kind: Mapped[str] = mapped_column(String(20), default="file")
    table_name: Mapped[str] = mapped_column(String(100))
    display_name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    original_filename: Mapped[str | None] = mapped_column(String(300))
    storage_key: Mapped[str] = mapped_column(String(500))
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    columns: Mapped[list[Any]] = mapped_column(default=list)  # [{"name", "type"}]
    profile: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    # Extracts from a connection: what was pulled and when.
    connection_id: Mapped[str | None] = mapped_column(ForeignKey("connections.id", ondelete="SET NULL"))
    source_query: Mapped[str | None] = mapped_column(Text)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MetricDefinition(TimestampMixin, Base):
    """A governed metric: a structured aggregate over one dataset, reused across runs."""

    __tablename__ = "metrics"
    __table_args__ = (UniqueConstraint("workspace_id", "name"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(100))  # identifier, e.g. conversion_rate
    label: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    # sum | count | count_distinct | average | ratio; ratio uses numerator/denominator specs.
    kind: Mapped[str] = mapped_column(String(20))
    column: Mapped[str | None] = mapped_column(String(200))
    numerator: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    denominator: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    filter: Mapped[str | None] = mapped_column(Text)
    time_column: Mapped[str | None] = mapped_column(String(200))
    dimensions: Mapped[list[Any]] = mapped_column(default=list)
    format: Mapped[str] = mapped_column(String(20), default="number")  # number | percent | currency
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class Model(TimestampMixin, Base):
    """A trained predictive model in the workspace registry."""

    __tablename__ = "models"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(String(200))
    task: Mapped[str] = mapped_column(String(30))  # classification | regression
    target: Mapped[str] = mapped_column(String(200))
    source_table: Mapped[str] = mapped_column(String(100))
    features: Mapped[list[Any]] = mapped_column(default=list)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    card: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    storage_key: Mapped[str] = mapped_column(String(500))
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class Run(TimestampMixin, Base):
    """One natural-language goal and everything the agents did for it."""

    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    goal: Mapped[str] = mapped_column(Text)
    dataset_ids: Mapped[list[Any]] = mapped_column(default=list)
    # planning | needs_input | awaiting_approval | running | completed | failed | cancelled
    status: Mapped[str] = mapped_column(String(30), default="planning", index=True)
    intent: Mapped[str | None] = mapped_column(String(50))
    plan: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    clarifications: Mapped[list[Any]] = mapped_column(default=list)  # [{"question", "answer"}]
    error: Mapped[str | None] = mapped_column(Text)
    usage: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    steps: Mapped[list["Step"]] = relationship(
        back_populates="run", order_by="Step.position", cascade="all, delete-orphan"
    )


class Step(TimestampMixin, Base):
    __tablename__ = "steps"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(50))
    position: Mapped[int] = mapped_column(Integer)
    agent: Mapped[str] = mapped_column(String(30))
    task: Mapped[str] = mapped_column(Text)
    depends_on: Mapped[list[Any]] = mapped_column(default=list)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    summary: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    run: Mapped[Run] = relationship(back_populates="steps")


class ToolCall(TimestampMixin, Base):
    """The reproducibility log: every tool an agent invoked, with inputs and outputs."""

    __tablename__ = "tool_calls"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    step_id: Mapped[str] = mapped_column(ForeignKey("steps.id", ondelete="CASCADE"), index=True)
    agent: Mapped[str] = mapped_column(String(30))
    tool: Mapped[str] = mapped_column(String(60))
    input: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    code: Mapped[str | None] = mapped_column(Text)  # SQL or equivalent the tool executed
    artifact_id: Mapped[str | None] = mapped_column(String(32))
    error: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)


class Artifact(TimestampMixin, Base):
    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    step_id: Mapped[str | None] = mapped_column(ForeignKey("steps.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(40))  # profile | table | stats | ...
    title: Mapped[str] = mapped_column(String(300))
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Insight(TimestampMixin, Base):
    __tablename__ = "insights"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    title: Mapped[str] = mapped_column(String(300))
    claim: Mapped[str] = mapped_column(Text)
    evidence: Mapped[list[Any]] = mapped_column(default=list)
    method: Mapped[str] = mapped_column(Text, default="")
    caveats: Mapped[list[Any]] = mapped_column(default=list)
    recommended_action: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[str] = mapped_column(String(10), default="medium")
    checks: Mapped[list[Any]] = mapped_column(default=list)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    # draft | accepted | edited | rejected
    status: Mapped[str] = mapped_column(String(20), default="draft")
    original_claim: Mapped[str | None] = mapped_column(Text)
    review_note: Mapped[str | None] = mapped_column(Text)
    reviewed_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RunEvent(TimestampMixin, Base):
    """Append-only timeline of a run, rendered as the live activity feed."""

    __tablename__ = "run_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    message: Mapped[str] = mapped_column(Text)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Job(TimestampMixin, Base):
    """Postgres-backed job queue (SQLite in development and tests)."""

    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    kind: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
