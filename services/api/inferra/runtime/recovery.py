"""Recover runs a restart interrupted.

A worker that stops mid-run (a deploy, a crash, a free-tier instance going to sleep) leaves its run
in 'planning' or 'running' forever. The worker loop sweeps for runs with no progress for
`run_stall_minutes`: a run makes progress on every model call and step, so a quiet run that has
no job still waiting in the queue was interrupted. It is marked failed with a clear message, and
people can retry it."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from inferra.config import get_settings
from inferra.db.models import Job, Run, Step
from inferra.runtime.recorder import Recorder

INTERRUPTED = (
    "The run was interrupted (the server restarted or went to sleep). Retry it to run the same plan again."
)


def recover_stalled(db: Session, now: datetime | None = None) -> list[str]:
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(minutes=get_settings().run_stall_minutes)
    # Waiting in the queue behind other work is not stalled.
    queued = {
        str(job.payload.get("run_id"))
        for job in db.scalars(select(Job).where(Job.status == "queued"))
        if job.payload.get("run_id")
    }
    stalled = [
        run
        for run in db.scalars(
            select(Run).where(Run.status.in_(["planning", "running"]), Run.updated_at < cutoff)
        )
        if run.id not in queued
    ]
    for run in stalled:
        for step in db.scalars(select(Step).where(Step.run_id == run.id)):
            if step.status == "running":
                step.status, step.error, step.finished_at = "failed", "Interrupted.", now
            elif step.status == "pending":
                step.status = "skipped"
        run.status, run.error = "failed", INTERRUPTED
        db.commit()
        Recorder(db, run).event("failed", INTERRUPTED)
    for job in db.scalars(select(Job).where(Job.status == "running", Job.locked_at < cutoff)):
        job.status, job.error, job.finished_at = "failed", "Interrupted.", now
    db.commit()
    return [run.id for run in stalled]
