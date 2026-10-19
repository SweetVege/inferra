"""A small job queue on top of the metadata database.

Postgres claims jobs with SELECT ... FOR UPDATE SKIP LOCKED, so several workers can run safely.
SQLite (development and tests) has a single writer, so a plain select is enough there."""

import logging
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from inferra.config import get_settings
from inferra.db.models import Job
from inferra.db.session import session_scope

log = logging.getLogger(__name__)

Handler = Callable[[Session, dict[str, Any]], None]
_handlers: dict[str, Handler] = {}
_wake = threading.Event()


def handler(kind: str) -> Callable[[Handler], Handler]:
    def register(fn: Handler) -> Handler:
        _handlers[kind] = fn
        return fn

    return register


def enqueue(db: Session, kind: str, **payload: Any) -> Job:
    job = Job(kind=kind, payload=payload)
    db.add(job)
    db.flush()
    _wake.set()
    return job


def claim_next(db: Session) -> Job | None:
    query = select(Job).where(Job.status == "queued").order_by(Job.created_at).limit(1)
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        query = query.with_for_update(skip_locked=True)
    job = db.scalars(query).first()
    if job is None:
        return None
    job.status = "running"
    job.attempts += 1
    job.locked_at = datetime.now(UTC)
    db.commit()
    return job


def run_one() -> bool:
    """Claim and process one job. Returns False when the queue is empty."""
    from inferra import samples  # noqa: F401  (registers handlers)
    from inferra.runtime import executor, scheduler  # noqa: F401

    with session_scope() as db:
        job = claim_next(db)
        if job is None:
            return False
        job_id, kind, payload = job.id, job.kind, dict(job.payload)
    error = None
    try:
        with session_scope() as db:
            _handlers[kind](db, payload)
    except Exception as exc:  # a failed job must never take the worker down
        log.exception("job %s (%s) failed", job_id, kind)
        error = f"{type(exc).__name__}: {exc}"
    with session_scope() as db:
        job = db.get(Job, job_id)
        if job is not None:
            job.status = "failed" if error else "done"
            job.error = error
            job.finished_at = datetime.now(UTC)
    return True


def drain(max_jobs: int = 100) -> int:
    count = 0
    while count < max_jobs and run_one():
        count += 1
    return count


def tick_schedules() -> int:
    """Housekeeping on every tick: fail runs a restart interrupted, then enqueue due re-runs."""
    from inferra.runtime.recovery import recover_stalled
    from inferra.runtime.scheduler import fire_due

    with session_scope() as db:
        recover_stalled(db)
        return len(fire_due(db))


def work_forever(
    poll_seconds: float = 1.0, stop: threading.Event | None = None, schedule_every: float = 30.0
) -> None:
    stop = stop or threading.Event()
    next_tick = 0.0
    while not stop.is_set():
        try:
            if time.monotonic() >= next_tick:
                next_tick = time.monotonic() + schedule_every
                tick_schedules()
            if run_one():
                continue
        except Exception:
            log.exception("worker loop error")
        _wake.wait(timeout=poll_seconds)
        _wake.clear()


def start_inline_worker() -> threading.Event | None:
    """In development the API process also runs the worker in a background thread."""
    if not get_settings().inline_worker:
        return None
    stop = threading.Event()
    threading.Thread(target=work_forever, kwargs={"stop": stop}, daemon=True, name="inferra-worker").start()
    return stop
