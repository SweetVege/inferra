from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from inferra.api.schemas import WorkspaceOut, WorkspaceUpdate
from inferra.auth.session import WorkspaceAccess, current_user, workspace_access
from inferra.db.models import Membership, User
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
