from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from inferra.api.schemas import WorkspaceOut, WorkspaceUpdate
from inferra.auth.session import WorkspaceAccess, current_user, workspace_access
from inferra.db.models import Alert, Dataset, Insight, Membership, Report, Run, Schedule, User
from inferra.db.session import get_db
from inferra.llm.gateway import llm_available
from inferra.workflows.templates import WORKFLOWS

router = APIRouter(tags=["workspaces"])


def _out(access_role: str, ws) -> WorkspaceOut:  # type: ignore[no-untyped-def]
    return WorkspaceOut(
        id=ws.id, name=ws.name, is_demo=ws.is_demo, auto_approve_plans=ws.auto_approve_plans, role=access_role
    )


@router.get("/workspaces", response_model=list[WorkspaceOut])
def list_workspaces(user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[WorkspaceOut]:
    memberships = db.scalars(select(Membership).where(Membership.user_id == user.id)).all()
    items = [_out(m.role, m.workspace) for m in memberships]
    # Personal workspaces first, the demo last.
    return sorted(items, key=lambda w: (w.is_demo, w.name.lower()))


@router.patch("/workspaces/{workspace_id}", response_model=WorkspaceOut)
def update_workspace(
    body: WorkspaceUpdate,
    access: WorkspaceAccess = Depends(workspace_access),
    db: Session = Depends(get_db),
) -> WorkspaceOut:
    access.require("owner")
    ws = access.workspace
    if body.name is not None:
        ws.name = body.name
    if body.auto_approve_plans is not None:
        ws.auto_approve_plans = body.auto_approve_plans
    db.commit()
    return _out(access.role, ws)


@router.get("/workspaces/{workspace_id}/overview")
def overview(access: WorkspaceAccess = Depends(workspace_access), db: Session = Depends(get_db)) -> dict:  # type: ignore[type-arg]
    """Counts behind the getting-started checklist and the navigation badges."""
    ws = access.workspace.id

    def count(query) -> int:  # type: ignore[no-untyped-def]
        return db.scalar(select(func.count()).select_from(query.subquery())) or 0

    return {
        "datasets": count(select(Dataset.id).where(Dataset.workspace_id == ws)),
        "runs": count(select(Run.id).where(Run.workspace_id == ws)),
        "completed_runs": count(select(Run.id).where(Run.workspace_id == ws, Run.status == "completed")),
        "reviewed_insights": count(
            select(Insight.id)
            .join(Run, Run.id == Insight.run_id)
            .where(Run.workspace_id == ws, Insight.status != "draft")
        ),
        "reports": count(select(Report.id).where(Report.workspace_id == ws)),
        "published_reports": count(
            select(Report.id).where(Report.workspace_id == ws, Report.published_at.is_not(None))
        ),
        "schedules": count(select(Schedule.id).where(Schedule.workspace_id == ws)),
        "open_alerts": count(select(Alert.id).where(Alert.workspace_id == ws, Alert.status == "open")),
    }


@router.get("/meta")
def meta() -> dict:  # type: ignore[type-arg]
    return {
        "llm_available": llm_available(),
        "intents": [
            {
                "intent": w.intent,
                "title": w.title,
                "description": w.description,
                "examples": list(w.examples),
                "available": w.available,
                "phase": w.phase,
            }
            for w in WORKFLOWS.values()
        ],
    }
