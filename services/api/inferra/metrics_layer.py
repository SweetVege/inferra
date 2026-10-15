"""The metric layer: governed metric definitions, resolved into specs the tools understand."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from inferra.db.models import Dataset, MetricDefinition


def to_spec(metric: MetricDefinition, table: str) -> dict[str, Any]:
    return {
        "name": metric.name,
        "label": metric.label,
        "description": metric.description,
        "table": table,
        "kind": metric.kind,
        "column": metric.column,
        "numerator": metric.numerator,
        "denominator": metric.denominator,
        "filter": metric.filter,
        "time_column": metric.time_column,
        "format": metric.format,
        "dimensions": list(metric.dimensions or []),
    }


def metric_specs(
    db: Session, workspace_id: str, dataset_ids: list[str] | None = None
) -> dict[str, dict[str, Any]]:
    """Metric definitions keyed by name, limited to the given datasets when provided."""
    query = (
        select(MetricDefinition, Dataset.table_name)
        .join(Dataset, Dataset.id == MetricDefinition.dataset_id)
        .where(MetricDefinition.workspace_id == workspace_id)
    )
    if dataset_ids:
        query = query.where(MetricDefinition.dataset_id.in_(dataset_ids))
    return {m.name: to_spec(m, table) for m, table in db.execute(query.order_by(MetricDefinition.name)).all()}
