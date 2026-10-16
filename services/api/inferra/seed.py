"""Seed the public demo workspace: `python -m inferra.seed [--reset]`.

The demo run is produced by the real pipeline with a scripted model (see llm/demo_script.py), so
visitors see genuine tool calls, artifacts, and verifier results without an API key."""

import argparse
import os
import secrets
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from inferra import storage
from inferra.api.reports import publish
from inferra.auth.accounts import demo_user, demo_workspace
from inferra.datasources.registry import datasource_for
from inferra.db.models import (
    Base,
    Dataset,
    Insight,
    MetricDefinition,
    Model,
    Report,
    Run,
    Schedule,
    User,
    Workspace,
)
from inferra.db.session import get_engine, session_scope
from inferra.llm.demo_script import demo_script
from inferra.llm.gateway import set_llm_factory
from inferra.llm.scripted import ScriptedLLM
from inferra.runtime import executor
from inferra.runtime.recorder import Recorder
from inferra.tools.profiling import profile_table

DEMO_RUNS = [
    {
        "file": "ecommerce_orders.csv",
        "description": "Six months of online orders with region, channel, discount, amount, returns, "
        "and customer ratings.",
        "goal": "Give me an overview of our e-commerce orders: what stands out, and can we trust the data?",
        "accept": 2,
    },
    {
        "file": "checkout_experiment.csv",
        "description": "A three-week randomized test of a redesigned checkout: one row per user with "
        "variant, device, country, pre-period orders and spend, conversion, revenue, and checkout latency.",
        "goal": "Did the new checkout test win? Check conversion, and make sure latency did not get worse.",
        "accept": 1,
    },
    {
        "file": "customer_churn.csv",
        "description": "Subscription customers with plan, tenure, engagement, support, billing, "
        "and a churn label.",
        "goal": "Predict which customers are likely to churn and tell me what drives it.",
        "accept": 0,
    },
    {
        "file": "loyalty_program.csv",
        "description": "Weekly sales for 60 stores; 24 joined a loyalty program starting the week "
        "of 2026-04-06.",
        "goal": "Did the loyalty program increase weekly store sales?",
        "accept": 0,
    },
    {
        "file": "daily_funnel.csv",
        "description": "Daily sessions, signups, and revenue by platform, app version, channel, and country.",
        "goal": "Why did signup conversion drop in late September?",
        "accept": 2,
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
    {
        "file": "daily_funnel.csv",
        "goal": "Alert me every Monday if weekly signup conversion drops more than 10%.",
        "accept": 1,
    },
    {
        "file": "retail_order_lines.csv",
        "description": "A flat export of 3,000 orders at order-line grain, with customer and product "
        "attributes repeated on every line.",
        "goal": "Turn the order lines export into a star schema we can use for reporting.",
        "accept": 0,
    },
    {
        "file": "daily_sales.csv",
        "description": "Eighteen months of daily orders and revenue.",
        "goal": "Forecast daily orders for the next four weeks.",
        "accept": 0,
    },
]


REPORT_SUMMARY = (
    "The redesigned checkout lifted conversion without slowing checkout down, so it is ready to roll "
    "out. Separately, signup conversion stepped down in late September; the drop sits in one Android "
    "release, and the weekly monitor is still flagging it."
)


def samples_dir() -> Path:
    env = os.environ.get("INFERRA_SAMPLES_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[3] / "samples"


def seed_demo(reset: bool = False) -> str:
    from inferra.api.datasets import ingest_file

    Base.metadata.create_all(get_engine())
    with session_scope() as db:
        existing = demo_workspace(db)
        if existing is not None and not reset:
            return existing.id
        if existing is not None:
            for dataset in db.scalars(select(Dataset).where(Dataset.workspace_id == existing.id)):
                storage.delete_file(dataset.storage_key)
            for model in db.scalars(select(Model).where(Model.workspace_id == existing.id)):
                storage.delete_file(model.storage_key)
            db.delete(existing)
            db.flush()
        ws = Workspace(name="Demo · E-commerce", is_demo=True)
        db.add(ws)
        db.flush()
        user = demo_user(db)
        user_id = user.id
        run_ids = []
        ingested: dict[str, Dataset] = {}
        for spec in DEMO_RUNS:
            dataset = ingested.get(spec["file"])
            if dataset is None:
                dataset = ingest_file(
                    db,
                    ws.id,
                    samples_dir() / spec["file"],
                    spec["file"],
                    None,
                    description=spec["description"],
                )
                dataset.profile = profile_table(datasource_for(db, ws.id, [dataset.id]), dataset.table_name)
                ingested[spec["file"]] = dataset
            for metric in spec.get("metrics", []):
                db.add(MetricDefinition(workspace_id=ws.id, dataset_id=dataset.id, **metric))
            db.flush()
            run = Run(workspace_id=ws.id, created_by=user.id, goal=spec["goal"], dataset_ids=[dataset.id])
            db.add(run)
            db.flush()
            run_ids.append((run.id, spec["accept"]))
        db.commit()
        workspace_id = ws.id

    llm = ScriptedLLM(demo_script)
    set_llm_factory(lambda: llm)
    try:
        for run_id, accept in run_ids:
            with session_scope() as db:
                run = db.get(Run, run_id)
                assert run is not None
                Recorder(db, run).event("created", "Goal received")
                executor.plan_run(db, {"run_id": run_id})
                run.status = "running"
                Recorder(db, run).event("approved", "Plan approved by demo")
                db.commit()
                executor.execute_run(db, {"run_id": run_id})
                insights = db.scalars(
                    select(Insight).where(Insight.run_id == run_id).order_by(Insight.position)
                ).all()
                for insight in insights[:accept]:
                    insight.status = "accepted"
                    insight.reviewed_by = user_id
                    insight.reviewed_at = datetime.now(UTC)
                db.commit()
    finally:
        set_llm_factory(None)

    with session_scope() as db:
        # The demo is static data, so its schedule is shown paused rather than re-running forever.
        for schedule in db.scalars(select(Schedule).where(Schedule.workspace_id == workspace_id)):
            schedule.enabled = False
        accepted = db.scalars(
            select(Insight)
            .join(Run, Run.id == Insight.run_id)
            .where(Run.workspace_id == workspace_id, Insight.status == "accepted")
            .order_by(Run.created_at, Insight.position)
        ).all()
        picked = [i for i in accepted if i.run_id in {run_ids[1][0], run_ids[4][0], run_ids[5][0]}]
        report = Report(
            workspace_id=workspace_id,
            title="Weekly product review",
            summary=REPORT_SUMMARY,
            insight_ids=[i.id for i in picked],
            created_by=user_id,
        )
        db.add(report)
        user = db.get(User, user_id)
        assert user is not None
        publish(db, report, user)
        report.share_token = secrets.token_urlsafe(24)
    return workspace_id


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset", action="store_true", help="Rebuild the demo workspace.")
    args = parser.parse_args()
    print(f"Demo workspace: {seed_demo(reset=args.reset)}")


if __name__ == "__main__":
    main()
