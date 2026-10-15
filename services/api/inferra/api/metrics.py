from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from inferra.api.schemas import ORM
from inferra.auth.session import WorkspaceAccess, workspace_access
from inferra.datasources.registry import datasource_for
from inferra.db.models import Dataset, MetricDefinition
from inferra.db.session import get_db
from inferra.metrics_layer import to_spec
from inferra.tools.base import ToolContext, ToolError
from inferra.tools.metrics import MetricPart, MetricSpec, run_metric

router = APIRouter(prefix="/workspaces/{workspace_id}/metrics", tags=["metrics"])


class MetricIn(BaseModel):
    dataset_id: str
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{1,60}$", description="Identifier, e.g. conversion_rate.")
    label: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    kind: Literal["sum", "count", "count_distinct", "average", "ratio"]
    column: str | None = None
    numerator: MetricPart | None = None
    denominator: MetricPart | None = None
    filter: str | None = Field(default=None, max_length=1000)
    time_column: str | None = None
    dimensions: list[str] = Field(default_factory=list, max_length=12)
    format: Literal["number", "percent", "currency"] = "number"


class MetricOut(ORM):
    id: str
    dataset_id: str
    name: str
    label: str
    description: str | None
    kind: str
    column: str | None
    numerator: dict[str, Any] | None
    denominator: dict[str, Any] | None
    filter: str | None
    time_column: str | None
    dimensions: list[str]
    format: str
    created_at: datetime
    dataset_name: str | None = None
    current_value: float | None = None


def _dataset(db: Session, access: WorkspaceAccess, dataset_id: str) -> Dataset:
    d = db.get(Dataset, dataset_id)
    if d is None or d.workspace_id != access.workspace.id:
        raise HTTPException(404, "Dataset not found.")
    return d


def _evaluate(db: Session, workspace_id: str, dataset: Dataset, spec: dict[str, Any]) -> float | None:
    """Compute the metric once over all data: proves the definition runs and gives its current value."""
    try:
        m = MetricSpec.model_validate(spec)
    except ValidationError as exc:
        raise HTTPException(400, exc.errors()[0]["msg"]) from exc
    columns = {c["name"] for c in dataset.columns}
    for col in [m.time_column, *spec.get("dimensions", [])]:
        if col and col not in columns:
            raise HTTPException(400, f"Unknown column '{col}' in {dataset.table_name}.")
    ctx = ToolContext(source=datasource_for(db, workspace_id, [dataset.id]))
    try:
        rows, _ = run_metric(ctx, m)
    except ToolError as exc:
        raise HTTPException(400, str(exc)) from exc
    value = rows[0]["value"] if rows else None
    return float(value) if value is not None else None


def _out(db: Session, metric: MetricDefinition, value: float | None = None) -> MetricOut:
    out = MetricOut.model_validate(metric)
    dataset = db.get(Dataset, metric.dataset_id)
    out.dataset_name = dataset.display_name if dataset else None
    out.current_value = value
    return out


@router.get("", response_model=list[MetricOut])
def list_metrics(
    access: WorkspaceAccess = Depends(workspace_access), db: Session = Depends(get_db)
) -> list[MetricOut]:
    metrics = db.scalars(
        select(MetricDefinition)
        .where(MetricDefinition.workspace_id == access.workspace.id)
        .order_by(MetricDefinition.name)
    )
    return [_out(db, m) for m in metrics]


@router.post("", response_model=MetricOut, status_code=201)
def create_metric(
    body: MetricIn, access: WorkspaceAccess = Depends(workspace_access), db: Session = Depends(get_db)
) -> MetricOut:
    access.require("editor")
    dataset = _dataset(db, access, body.dataset_id)
    fields = body.model_dump(exclude={"dataset_id"})
    value = _evaluate(db, access.workspace.id, dataset, {**fields, "table": dataset.table_name})
    metric = MetricDefinition(
        workspace_id=access.workspace.id, dataset_id=dataset.id, created_by=access.user.id, **fields
    )
    db.add(metric)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, f"A metric named '{body.name}' already exists.") from exc
    return _out(db, metric, value)


def _get(db: Session, access: WorkspaceAccess, metric_id: str) -> MetricDefinition:
    metric = db.get(MetricDefinition, metric_id)
    if metric is None or metric.workspace_id != access.workspace.id:
        raise HTTPException(404, "Metric not found.")
    return metric


@router.put("/{metric_id}", response_model=MetricOut)
def update_metric(
    metric_id: str,
    body: MetricIn,
    access: WorkspaceAccess = Depends(workspace_access),
    db: Session = Depends(get_db),
) -> MetricOut:
    access.require("editor")
    metric = _get(db, access, metric_id)
    dataset = _dataset(db, access, body.dataset_id)
    fields = body.model_dump(exclude={"dataset_id"})
    value = _evaluate(db, access.workspace.id, dataset, {**fields, "table": dataset.table_name})
    for key, val in fields.items():
        setattr(metric, key, val)
    metric.dataset_id = dataset.id
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, f"A metric named '{body.name}' already exists.") from exc
    return _out(db, metric, value)


@router.get("/{metric_id}/values")
def metric_values(
    metric_id: str,
    grain: Literal["day", "week", "month"] = "week",
    access: WorkspaceAccess = Depends(workspace_access),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """The metric's recent series, for sparklines and the metric page."""
    metric = _get(db, access, metric_id)
    dataset = db.get(Dataset, metric.dataset_id)
    assert dataset is not None
    spec = MetricSpec.model_validate(to_spec(metric, dataset.table_name))
    ctx = ToolContext(source=datasource_for(db, access.workspace.id, [dataset.id]))
    try:
        total = run_metric(ctx, spec)[0][0]
        series = run_metric(ctx, spec, grain=grain, limit=2000)[0] if spec.time_column else []
    except ToolError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"total": total, "grain": grain, "series": series[-52:]}


@router.delete("/{metric_id}", status_code=204)
def delete_metric(
    metric_id: str, access: WorkspaceAccess = Depends(workspace_access), db: Session = Depends(get_db)
) -> None:
    access.require("editor")
    db.delete(_get(db, access, metric_id))
    db.commit()
