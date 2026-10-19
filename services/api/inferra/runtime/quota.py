"""Who may spend the model budget, and how much of it per day.

Agent runs call a paid model, so a public deployment needs limits: an optional allowlist of logins,
a daily cap per person, and logins exempt from both (the deployment's owner). They are checked when
a run is created, by a person or by a schedule on that person's behalf."""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from inferra.config import get_settings
from inferra.db.models import Run, User


@dataclass
class RunQuota:
    allowed: bool
    limit: int | None  # None: no daily limit
    used: int
    reason: str | None = None  # why runs are not allowed now
    invited: bool = True  # False when an allowlist is set and the user is not on it

    @property
    def remaining(self) -> int | None:
        return None if self.limit is None else max(self.limit - self.used, 0)


def runs_today(db: Session, user_id: str) -> int:
    start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return db.scalar(select(func.count()).where(Run.created_by == user_id, Run.created_at >= start)) or 0


def run_quota(db: Session, user: User) -> RunQuota:
    s = get_settings()
    if user.login.lower() in s.run_unlimited_logins:
        return RunQuota(True, None, 0)
    allowlist = s.run_allowlist_logins
    limit = s.daily_run_limit or None
    used = runs_today(db, user.id) if limit else 0
    if allowlist and user.login.lower() not in allowlist:
        return RunQuota(
            False,
            limit,
            used,
            "Agent runs on this deployment are limited to invited accounts.",
            invited=False,
        )
    if limit is not None and used >= limit:
        runs = f"{limit} agent run{'s' if limit != 1 else ''}"
        return RunQuota(False, limit, used, f"You have used today's {runs}. The limit resets at 00:00 UTC.")
    return RunQuota(True, limit, used)


def require_run_quota(db: Session, user: User) -> None:
    """Raise the HTTP error an API caller should see when this user may not start a run now."""
    from fastapi import HTTPException

    quota = run_quota(db, user)
    if not quota.allowed:
        raise HTTPException(403 if not quota.invited else 429, quota.reason)
