"""Sample datasets: the demo workspace is built from them, and new workspaces start with them, so
anyone who signs in can ask a question within a minute."""

import logging
import os
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from inferra.datasources.registry import datasource_for
from inferra.db.models import Dataset, MetricDefinition
from inferra.runtime.queue import handler
from inferra.tools.profiling import profile_table

log = logging.getLogger(__name__)

# Ordered as the workflows they show: explore, experiment, predict, effect, diagnose/monitor,
# data modeling, forecast.
SAMPLE_DATASETS: dict[str, dict[str, Any]] = {
    "ecommerce_orders.csv": {
        "description": "Six months of online orders with region, channel, discount, amount, returns, "
        "and customer ratings.",
    },
    "checkout_experiment.csv": {
        "description": "A three-week randomized test of a redesigned checkout: one row per user with "
        "variant, device, country, pre-period orders and spend, conversion, revenue, and checkout latency.",
    },
    "customer_churn.csv": {
        "description": "Subscription customers with plan, tenure, engagement, support, billing, "
        "and a churn label.",
    },
    "loyalty_program.csv": {
        "description": "Weekly sales for 60 stores; 24 joined a loyalty program starting the week "
        "of 2026-04-06.",
    },
    "daily_funnel.csv": {
        "description": "Daily sessions, signups, and revenue by platform, app version, channel, and country.",
        "metrics": [
            {
                "name": "conversion_rate",
                "label": "Signup conversion rate",
                "description": "Signups divided by sessions.",
                "kind": "ratio",
                "numerator": {"kind": "sum", "column": "signups"},
                "denominator": {"kind": "sum", "column": "sessions"},
                "time_column": "date",
                "dimensions": ["platform", "app_version", "channel", "country"],
                "format": "percent",
            },
            {
                "name": "sessions",
                "label": "Sessions",
                "kind": "sum",
                "column": "sessions",
                "time_column": "date",
                "dimensions": ["platform", "channel", "country"],
            },
            {
                "name": "revenue_per_session",
                "label": "Revenue per session",
                "kind": "ratio",
                "numerator": {"kind": "sum", "column": "revenue"},
                "denominator": {"kind": "sum", "column": "sessions"},
                "time_column": "date",
                "dimensions": ["platform", "channel"],
                "format": "currency",
            },
        ],
    },
    "retail_order_lines.csv": {
        "description": "A flat export of 3,000 orders at order-line grain, with customer and product "
        "attributes repeated on every line.",
    },
    "daily_sales.csv": {"description": "Eighteen months of daily orders and revenue."},
}


def samples_dir() -> Path:
    env = os.environ.get("INFERRA_SAMPLES_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[3] / "samples"


def ingest_sample(db: Session, workspace_id: str, filename: str, user_id: str | None) -> Dataset:
    """Load one sample as a profiled dataset, with its metric definitions."""
    from inferra.api.datasets import ingest_file

    spec = SAMPLE_DATASETS[filename]
    dataset = ingest_file(
        db, workspace_id, samples_dir() / filename, filename, user_id, description=spec["description"]
    )
    dataset.profile = profile_table(datasource_for(db, workspace_id, [dataset.id]), dataset.table_name)
    defined = set(
        db.scalars(select(MetricDefinition.name).where(MetricDefinition.workspace_id == workspace_id))
    )
    for metric in spec.get("metrics", []):
        if metric["name"] not in defined:
            db.add(
                MetricDefinition(
                    workspace_id=workspace_id, dataset_id=dataset.id, created_by=user_id, **metric
                )
            )
    db.commit()
    return dataset


def add_sample_datasets(db: Session, workspace_id: str, user_id: str | None) -> list[Dataset]:
    """Add every sample the workspace does not have yet. Returns the datasets added."""
    present = set(
        db.scalars(
            select(Dataset.original_filename).where(
                Dataset.workspace_id == workspace_id, Dataset.original_filename.in_(SAMPLE_DATASETS)
            )
        )
    )
    return [ingest_sample(db, workspace_id, f, user_id) for f in SAMPLE_DATASETS if f not in present]


@handler("add_samples")
def add_samples_job(db: Session, payload: dict[str, Any]) -> None:
    added = add_sample_datasets(db, payload["workspace_id"], payload.get("user_id"))
    log.info("added %d sample datasets to %s", len(added), payload["workspace_id"])
