"""Seed the public demo workspace: `python -m inferra.seed [--reset]`.

The demo run is produced by the real pipeline with a scripted model (see llm/demo_script.py), so
visitors see genuine tool calls, artifacts, and verifier results without an API key."""

import argparse
import base64
import hmac
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from inferra import storage
from inferra.api.reports import publish
from inferra.auth.accounts import demo_user, demo_workspace, ensure_membership
from inferra.config import get_settings
from inferra.db.models import (
    Base,
    Dataset,
    Insight,
    Membership,
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
from inferra.samples import ingest_sample

DEMO_RUNS = [
    {
        "file": "ecommerce_orders.csv",
        "goal": "Give me an overview of our e-commerce orders: what stands out, and can we trust the data?",
        "accept": 2,
    },
    {
        "file": "checkout_experiment.csv",
        "goal": "Did the new checkout test win? Check conversion, and make sure latency did not get worse.",
        "accept": 2,  # the conversion win and the latency guardrail it has to weigh
    },
    {
        "file": "customer_churn.csv",
        "goal": "Predict which customers are likely to churn and tell me what drives it.",
        "accept": 0,
    },
    {
        "file": "loyalty_program.csv",
        "goal": "Did the loyalty program increase weekly store sales?",
        "accept": 0,
    },
    {
        "file": "daily_funnel.csv",
        "goal": "Why did signup conversion drop in late September?",
        "accept": 2,
    },
    {
        "file": "daily_funnel.csv",
        "goal": "Alert me every Monday if weekly signup conversion drops more than 10%.",
        "accept": 1,
    },
    {
        "file": "retail_order_lines.csv",
        "goal": "Turn the order lines export into a star schema we can use for reporting.",
        "accept": 0,
    },
    {
        "file": "daily_sales.csv",
        "goal": "Forecast daily orders for the next four weeks.",
        "accept": 0,
    },
]


REPORT_SUMMARY = (
    "The redesigned checkout lifted conversion, but it also made checkout slower, so it should roll "
    "out with a latency budget. Separately, signup conversion stepped down in late September; the drop "
    "sits in one Android release, and the weekly monitor is still flagging it."
)


def seed_demo(reset: bool = False) -> str:
    Base.metadata.create_all(get_engine())
    viewers: list[str] = []
    with session_scope() as db:
        existing = demo_workspace(db)
        if existing is not None and not reset and _is_current(db, existing.id):
            return existing.id
        if existing is not None:
            # Rebuilt on reset, or when a release changed the demo runs. Visitors keep their access.
            viewers = list(
                db.scalars(
                    select(Membership.user_id).where(
                        Membership.workspace_id == existing.id, Membership.role == "viewer"
                    )
                )
            )
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
                dataset = ingested[spec["file"]] = ingest_sample(db, ws.id, spec["file"], None)
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
        report.share_token = demo_share_token()
        demo = db.get(Workspace, workspace_id)
        for viewer in db.scalars(select(User).where(User.id.in_(viewers))):
            ensure_membership(db, viewer, demo, "viewer")  # type: ignore[arg-type]
    return workspace_id


def _is_current(db: Session, workspace_id: str) -> bool:
    """The demo matches this release when it has the same goals and the same shared report."""
    goals = sorted(db.scalars(select(Run.goal).where(Run.workspace_id == workspace_id)))
    report = db.scalars(select(Report).where(Report.workspace_id == workspace_id)).first()
    return (
        goals == sorted(spec["goal"] for spec in DEMO_RUNS)
        and report is not None
        and report.summary == REPORT_SUMMARY
    )


def demo_share_token() -> str:
    """Stable across rebuilds, so the demo report's public link can go in a README or a CV."""
    secret = get_settings().secret_key.encode()
    return base64.urlsafe_b64encode(hmac.digest(secret, b"inferra-demo-report", "sha256")[:24]).decode()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset", action="store_true", help="Rebuild the demo workspace.")
    args = parser.parse_args()
    print(f"Demo workspace: {seed_demo(reset=args.reset)}")


if __name__ == "__main__":
    main()
