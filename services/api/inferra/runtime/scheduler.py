"""Scheduled re-runs and alerts.

A schedule stores a plan a person approved. When it falls due, the worker refreshes the plan's
extracts, starts a new run of the same plan (no re-planning, so results stay comparable), and the
executor raises an alert for every metric check that says so. Several workers may tick at once:
each claims a schedule by moving its next_run_at with a compare-and-set update."""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from inferra.db.models import Alert, Artifact, Dataset, Run, Schedule, Step, User, Workspace
from inferra.runtime.queue import enqueue, handler
from inferra.runtime.quota import run_quota
from inferra.runtime.recorder import Recorder

log = logging.getLogger(__name__)

CADENCES = ("daily", "weekly")
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def aware(value: datetime) -> datetime:
    """SQLite hands back naive datetimes; everything here is UTC."""
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def next_run(cadence: str, weekday: int, hour: int, after: datetime) -> datetime:
    after = aware(after)
    candidate = after.replace(hour=hour, minute=0, second=0, microsecond=0)
    if cadence == "weekly":
        candidate += timedelta(days=(weekday - candidate.weekday()) % 7)
    step = timedelta(days=7 if cadence == "weekly" else 1)
    while candidate <= after:
        candidate += step
    return candidate


def describe(schedule: Schedule) -> str:
    when = f"{schedule.hour:02d}:00 UTC"
    if schedule.cadence == "weekly":
        return f"Every {WEEKDAYS[schedule.weekday]} at {when}"
    return f"Every day at {when}"


def create_schedule(
    db: Session, run: Run, cadence: str, weekday: int, hour: int, user_id: str | None
) -> Schedule:
    schedule = Schedule(
        workspace_id=run.workspace_id,
        source_run_id=run.id,
        goal=run.goal,
        intent=run.intent or "",
        plan=dict(run.plan or {}),
        dataset_ids=list(run.dataset_ids),
        cadence=cadence,
        weekday=weekday,
        hour=hour,
        next_run_at=next_run(cadence, weekday, hour, datetime.now(UTC)),
        created_by=user_id,
    )
    db.add(schedule)
    db.flush()
    return schedule


def schedule_monitor(db: Session, run: Run) -> Schedule | None:
    """A completed monitor run turns into its schedule: approving the plan approved the monitoring."""
    if run.intent != "monitor" or run.schedule_id is not None:
        return None
    existing = db.scalars(select(Schedule).where(Schedule.source_run_id == run.id)).first()
    if existing is not None:
        return existing
    params = (run.plan or {}).get("params", {})
    cadence = "daily" if str(params.get("cadence", "")).lower().startswith("daily") else "weekly"
    schedule = create_schedule(db, run, cadence, 0, 8, run.created_by)
    Recorder(db, run).event(
        "scheduled", f"Monitoring scheduled: {describe(schedule)}", schedule_id=schedule.id
    )
    return schedule


def raise_alerts(db: Session, run: Run, schedule_id: str | None) -> list[Alert]:
    """One alert per metric check whose status is 'alert'. The tool decided; nothing is inferred."""
    checks = db.scalars(
        select(Artifact).where(Artifact.run_id == run.id, Artifact.kind == "metric_check")
    ).all()
    alerts = []
    for art in checks:
        data = art.data or {}
        if data.get("status") != "alert":
            continue
        label = (data.get("metric") or {}).get("label") or "Metric"
        latest = data.get("latest") or {}
        alert = Alert(
            workspace_id=run.workspace_id,
            run_id=run.id,
            schedule_id=schedule_id,
            artifact_id=art.id,
            title=f"{label} moved beyond its threshold",
            message=str(data.get("reason", "")),
            data={
                "metric": data.get("metric"),
                "period": latest.get("period"),
                "value": latest.get("value"),
                "baseline": (data.get("baseline") or {}).get("median"),
                "relative_change": data.get("relative_change"),
                "grain": data.get("grain"),
            },
        )
        db.add(alert)
        alerts.append(alert)
    if alerts:
        db.flush()
        Recorder(db, run).event("alert", f"{len(alerts)} alert(s) raised", alert_ids=[a.id for a in alerts])
    return alerts


def fire_due(db: Session, now: datetime | None = None) -> list[str]:
    """Enqueue every enabled schedule that is due. Demo workspaces are static, so they never fire."""
    now = now or datetime.now(UTC)
    due = db.scalars(
        select(Schedule)
        .join(Workspace, Workspace.id == Schedule.workspace_id)
        .where(Schedule.enabled.is_(True), Schedule.next_run_at <= now, Workspace.is_demo.is_(False))
        .order_by(Schedule.next_run_at)
        .limit(50)
    ).all()
    fired = []
    for schedule in due:
        following = next_run(schedule.cadence, schedule.weekday, schedule.hour, now)
        claimed = db.execute(
            update(Schedule)
            .where(Schedule.id == schedule.id, Schedule.next_run_at == schedule.next_run_at)
            .values(next_run_at=following)
            .execution_options(synchronize_session=False)
        ).rowcount
        if claimed:
            enqueue(db, "scheduled_run", schedule_id=schedule.id)
            fired.append(schedule.id)
    db.commit()
    return fired


@handler("scheduled_run")
def scheduled_run(db: Session, payload: dict[str, Any]) -> None:
    from inferra.api.connections import refresh_dataset, refreshable

    schedule = db.get(Schedule, payload["schedule_id"])
    if schedule is None:
        return
    owner = db.get(User, schedule.created_by) if schedule.created_by else None
    quota = run_quota(db, owner) if owner is not None else None
    if quota is None or not quota.allowed:
        # Recorded as a failed run, so the schedule's history says why nothing ran.
        run = Run(
            workspace_id=schedule.workspace_id,
            created_by=schedule.created_by,
            goal=schedule.goal,
            dataset_ids=list(schedule.dataset_ids),
            intent=schedule.intent,
            plan=schedule.plan,
            status="failed",
            error=quota.reason if quota else "The schedule's owner no longer exists.",
            schedule_id=schedule.id,
        )
        db.add(run)
        db.flush()
        schedule.last_run_at, schedule.last_run_id = datetime.now(UTC), run.id
        Recorder(db, run).event("failed", f"Scheduled re-run skipped: {run.error}", schedule_id=schedule.id)
        return

    datasets = list(db.scalars(select(Dataset).where(Dataset.id.in_(schedule.dataset_ids))))
    if not datasets:
        schedule.enabled = False
        db.commit()
        log.warning("schedule %s paused: its datasets were deleted", schedule.id)
        return
    notes = []
    for dataset in datasets:
        if not refreshable(dataset):
            continue
        try:
            refresh_dataset(db, dataset, schedule.created_by)
            notes.append(f"Refreshed {dataset.display_name}")
        except Exception as exc:  # stale data is better than no check; the run says so
            db.rollback()
            notes.append(f"Could not refresh {dataset.display_name} ({exc}); using the last extract")

    run = Run(
        workspace_id=schedule.workspace_id,
        created_by=schedule.created_by,
        goal=schedule.goal,
        dataset_ids=[d.id for d in datasets],
        intent=schedule.intent,
        plan=schedule.plan,
        status="running",
        schedule_id=schedule.id,
    )
    db.add(run)
    db.flush()
    for position, st in enumerate(schedule.plan.get("steps", [])):
        db.add(
            Step(
                run_id=run.id,
                key=st["key"],
                position=position,
                agent=st["agent"],
                task=st["task"],
                depends_on=st["depends_on"],
            )
        )
    schedule.last_run_at, schedule.last_run_id = datetime.now(UTC), run.id
    rec = Recorder(db, run)
    rec.event("created", f"Scheduled re-run ({describe(schedule)})", schedule_id=schedule.id)
    for note in notes:
        rec.event("refreshed", note)
    rec.event("approved", "Runs the plan approved when the schedule was created")
    enqueue(db, "execute_run", run_id=run.id)
    db.commit()
