from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class UserOut(ORM):
    id: str
    login: str
    name: str | None
    avatar_url: str | None
    is_demo: bool


class WorkspaceOut(BaseModel):
    id: str
    name: str
    is_demo: bool
    auto_approve_plans: bool
    role: str


class WorkspaceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    auto_approve_plans: bool | None = None


class DatasetOut(ORM):
    id: str
    table_name: str
    display_name: str
    description: str | None
    original_filename: str | None
    source_kind: str
    row_count: int
    columns: list[dict[str, str]]
    has_profile: bool = False
    created_at: datetime
    connection_id: str | None = None
    synced_at: datetime | None = None


class DatasetDetail(DatasetOut):
    preview: list[dict[str, Any]]
    profile: dict[str, Any] | None


class DatasetUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)


class RunCreate(BaseModel):
    goal: str = Field(min_length=3, max_length=2000)
    dataset_ids: list[str] = Field(default_factory=list)


class RunAnswer(BaseModel):
    answer: str = Field(min_length=1, max_length=1000)


class RunSummary(ORM):
    id: str
    goal: str
    status: str
    intent: str | None
    created_at: datetime
    updated_at: datetime
    insight_count: int = 0


class StepOut(ORM):
    id: str
    key: str
    position: int
    agent: str
    task: str
    depends_on: list[str]
    status: str
    summary: str | None
    error: str | None
    started_at: datetime | None
    finished_at: datetime | None


class ToolCallOut(ORM):
    id: str
    step_id: str
    agent: str
    tool: str
    input: dict[str, Any]
    code: str | None
    artifact_id: str | None
    error: str | None
    duration_ms: int
    created_at: datetime


class ArtifactOut(ORM):
    id: str
    step_id: str | None
    kind: str
    title: str
    data: dict[str, Any]


class InsightOut(ORM):
    id: str
    run_id: str
    position: int
    title: str
    claim: str
    evidence: list[dict[str, Any]]
    method: str
    caveats: list[str]
    recommended_action: str | None
    confidence: str
    checks: list[dict[str, Any]]
    verified: bool
    status: str
    original_claim: str | None
    review_note: str | None
    reviewed_at: datetime | None


class InsightReview(BaseModel):
    status: Literal["accepted", "edited", "rejected", "draft"]
    claim: str | None = Field(default=None, max_length=4000)
    note: str | None = Field(default=None, max_length=2000)


class EventOut(ORM):
    id: int
    kind: str
    message: str
    data: dict[str, Any]
    created_at: datetime


class RunDetail(ORM):
    id: str
    goal: str
    status: str
    intent: str | None
    plan: dict[str, Any] | None
    dataset_ids: list[str]
    clarifications: list[dict[str, Any]]
    error: str | None
    usage: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    steps: list[StepOut]
    tool_calls: list[ToolCallOut]
    artifacts: list[ArtifactOut]
    insights: list[InsightOut]
    events: list[EventOut]


class InsightFeedItem(InsightOut):
    run_goal: str
    run_intent: str | None
    created_at: datetime
