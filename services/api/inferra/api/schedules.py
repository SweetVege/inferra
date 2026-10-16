"""Schedules (re-running an approved plan) and the alerts their metric checks raise."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from inferra.api.schemas import AlertOut, RunRef, ScheduleCreate, ScheduleOut, ScheduleUpdate
from inferra.auth.session import WorkspaceAccess, workspace_access
from inferra.db.models import Alert, Run, Schedule
from inferra.db.session import get_db
from inferra.runtime.queue import enqueue
from inferra.runtime.scheduler import create_schedule, describe, next_run

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["schedules"])


def schedule_out(db: Session, schedule: Schedule, with_runs: bool = True) -> ScheduleOut:
    open_alerts = db.scalar(
        select(func.count()).where(Alert.schedule_id == schedule.id, Alert.status == "open")
    )
    runs = (
        db.scalars(
            select(Run).where(Run.schedule_id == schedule.id).order_by(Run.created_at.desc()).limit(8)
        ).all()
        if with_runs
        else []
    )
    return ScheduleOut(
        id=schedule.id,
        source_run_id=schedule.source_run_id,
        goal=schedule.goal,
        intent=schedule.intent,
        plan_title=(schedule.plan or {}).get("title"),
        cadence=schedule.cadence,
        weekday=schedule.weekday,
        hour=schedule.hour,
        description=describe(schedule),
        enabled=schedule.enabled,
        next_run_at=schedule.next_run_at,
        last_run_at=schedule.last_run_at,
        created_at=schedule.created_at,
        open_alerts=open_alerts or 0,
        recent_runs=[RunRef.model_validate(r) for r in runs],
    )


def _schedule(db: Session, access: WorkspaceAccess, schedule_id: str) -> Schedule:
    schedule = db.get(Schedule, schedule_id)
    if schedule is None or schedule.workspace_id != access.workspace.id:
        raise HTTPException(404, "Schedule not found.")
    return schedule


@router.get("/schedules", response_model=list[ScheduleOut])
def list_schedules(
    access: WorkspaceAccess = Depends(workspace_access), db: Session = Depends(get_db)
) -> list[ScheduleOut]:
    schedules = db.scalars(
        select(Schedule).where(Schedule.workspace_id == access.workspace.id).order_by(Schedule.created_at)
    )
    return [schedule_out(db, s) for s in schedules]


@router.post("/schedules", response_model=ScheduleOut, status_code=201)
def add_schedule(
    body: ScheduleCreate, access: WorkspaceAccess = Depends(workspace_access), db: Session = Depends(get_db)
) -> ScheduleOut:
    """Re-run a completed run's plan on a schedule. The plan is copied, so later runs are comparable."""
    access.require("editor")
    run = db.get(Run, body.run_id)
    if run is None or run.workspace_id != access.workspace.id:
        raise HTTPException(404, "Run not found.")
    if run.status != "completed" or not run.plan:
        raise HTTPException(409, "Only a completed run can be scheduled.")
    if run.schedule_id is not None:
        raise HTTPException(409, "This run was started by a schedule; edit that schedule instead.")
    if db.scalars(select(Schedule).where(Schedule.source_run_id == run.id)).first() is not None:
        raise HTTPException(409, "This run is already scheduled.")
    schedule = create_schedule(db, run, body.cadence, body.weekday, body.hour, access.user.id)
    db.commit()
    return schedule_out(db, schedule)


@router.patch("/schedules/{schedule_id}", response_model=ScheduleOut)
def update_schedule(
    schedule_id: str,
    body: ScheduleUpdate,
    access: WorkspaceAccess = Depends(workspace_access),
    db: Session = Depends(get_db),
) -> ScheduleOut:
    access.require("editor")
    schedule = _schedule(db, access, schedule_id)
    for field in ("enabled", "cadence", "weekday", "hour"):
        value = getattr(body, field)
        if value is not None:
            setattr(schedule, field, value)
    schedule.next_run_at = next_run(schedule.cadence, schedule.weekday, schedule.hour, datetime.now(UTC))
    db.commit()
    return schedule_out(db, schedule)


@router.post("/schedules/{schedule_id}/run", response_model=ScheduleOut)
def run_now(
    schedule_id: str, access: WorkspaceAccess = Depends(workspace_access), db: Session = Depends(get_db)
) -> ScheduleOut:
    """Start a run of the schedule now, without moving its next scheduled time."""
    access.require("editor")
    schedule = _schedule(db, access, schedule_id)
    enqueue(db, "scheduled_run", schedule_id=schedule.id)
    db.commit()
    return schedule_out(db, schedule)


@router.delete("/schedules/{schedule_id}", status_code=204)
def delete_schedule(
    schedule_id: str, access: WorkspaceAccess = Depends(workspace_access), db: Session = Depends(get_db)
) -> None:
    """Past runs and alerts stay; they lose the link to the schedule."""
    access.require("editor")
    db.delete(_schedule(db, access, schedule_id))
    db.commit()


@router.get("/alerts", response_model=list[AlertOut])
def list_alerts(
    status: str | None = None,
    access: WorkspaceAccess = Depends(workspace_access),
    db: Session = Depends(get_db),
) -> list[Alert]:
    query = select(Alert).where(Alert.workspace_id == access.workspace.id)
    if status:
        query = query.where(Alert.status == status)
    return list(db.scalars(query.order_by(Alert.created_at.desc()).limit(100)))


@router.post("/alerts/{alert_id}/acknowledge", response_model=AlertOut)
def acknowledge(
    alert_id: str, access: WorkspaceAccess = Depends(workspace_access), db: Session = Depends(get_db)
) -> Alert:
    access.require("editor")
    alert = db.get(Alert, alert_id)
    if alert is None or alert.workspace_id != access.workspace.id:
        raise HTTPException(404, "Alert not found.")
    alert.status = "acknowledged"
    alert.acknowledged_by = access.user.id
    alert.acknowledged_at = datetime.now(UTC)
    db.commit()
    return alert
