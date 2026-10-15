"""Seed the public demo workspace: `python -m inferra.seed [--reset]`.

The demo run is produced by the real pipeline with a scripted model (see llm/demo_script.py), so
visitors see genuine tool calls, artifacts, and verifier results without an API key."""

import argparse
import os
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from inferra import storage
from inferra.auth.accounts import demo_user, demo_workspace
from inferra.datasources.registry import datasource_for
from inferra.db.models import Base, Dataset, Insight, Model, Run, Workspace
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
        "file": "daily_sales.csv",
        "description": "Eighteen months of daily orders and revenue.",
        "goal": "Forecast daily orders for the next four weeks.",
        "accept": 0,
    },
]


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
        run_ids = []
        for spec in DEMO_RUNS:
            dataset = ingest_file(
                db, ws.id, samples_dir() / spec["file"], spec["file"], None, description=spec["description"]
            )
            dataset.profile = profile_table(datasource_for(db, ws.id, [dataset.id]), dataset.table_name)
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
                    insight.reviewed_at = datetime.now(UTC)
                db.commit()
    finally:
        set_llm_factory(None)
    return workspace_id


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset", action="store_true", help="Rebuild the demo workspace.")
    args = parser.parse_args()
    print(f"Demo workspace: {seed_demo(reset=args.reset)}")


if __name__ == "__main__":
    main()
