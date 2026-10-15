from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from inferra.api.schemas import (
    InsightOut,
    InsightReview,
    RunAnswer,
    RunCreate,
    RunDetail,
    RunSummary,
)
from inferra.auth.session import WorkspaceAccess, workspace_access
from inferra.db.models import Artifact, Dataset, Insight, Run, RunEvent, ToolCall
from inferra.db.session import get_db
from inferra.llm.gateway import llm_available
from inferra.runtime.queue import enqueue
from inferra.runtime.recorder import Recorder

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["runs"])


def _run(db: Session, access: WorkspaceAccess, run_id: str) -> Run:
    run = db.get(Run, run_id)
    if run is None or run.workspace_id != access.workspace.id:
        raise HTTPException(404, "Run not found.")
    return run


def run_detail(db: Session, run: Run) -> RunDetail:
    def rows(model, order):  # type: ignore[no-untyped-def]
        return list(db.scalars(select(model).where(model.run_id == run.id).order_by(order)))

    return RunDetail.model_validate(
        {
            **{
                c: getattr(run, c)
                for c in (
                    "id",
                    "goal",
                    "status",
                    "intent",
                    "plan",
                    "dataset_ids",
                    "clarifications",
                    "error",
                    "usage",
                    "created_at",
                    "updated_at",
                )
            },
            "steps": run.steps,
            "tool_calls": rows(ToolCall, ToolCall.created_at),
            "artifacts": rows(Artifact, Artifact.created_at),
            "insights": rows(Insight, Insight.position),
            "events": rows(RunEvent, RunEvent.id),
        }
    )


@router.get("/runs", response_model=list[RunSummary])
def list_runs(
    access: WorkspaceAccess = Depends(workspace_access), db: Session = Depends(get_db)
) -> list[RunSummary]:
    counts = dict(
        db.execute(
            select(Insight.run_id, func.count())
            .join(Run, Run.id == Insight.run_id)
            .where(Run.workspace_id == access.workspace.id)
            .group_by(Insight.run_id)
        ).all()
    )
    runs = db.scalars(
        select(Run).where(Run.workspace_id == access.workspace.id).order_by(Run.created_at.desc()).limit(100)
    )
    out = []
    for r in runs:
        item = RunSummary.model_validate(r)
        item.insight_count = counts.get(r.id, 0)
        out.append(item)
    return out


@router.post("/runs", response_model=RunDetail, status_code=201)
def create_run(
    body: RunCreate, access: WorkspaceAccess = Depends(workspace_access), db: Session = Depends(get_db)
) -> RunDetail:
    access.require("editor")
    if not llm_available():
        raise HTTPException(
            503,
            "Agent runs need a configured model. Set INFERRA_LLM_MODEL and "
            "ANTHROPIC_API_KEY on the API service.",
        )
    valid = set(db.scalars(select(Dataset.id).where(Dataset.workspace_id == access.workspace.id)))
    if not valid:
        raise HTTPException(400, "Upload a dataset before asking a question.")
    unknown = [i for i in body.dataset_ids if i not in valid]
    if unknown:
        raise HTTPException(400, "Some selected datasets do not exist in this workspace.")
    run = Run(
        workspace_id=access.workspace.id,
        created_by=access.user.id,
        goal=body.goal.strip(),
        dataset_ids=body.dataset_ids,
    )
    db.add(run)
    db.flush()
    Recorder(db, run).event("created", "Goal received")
    enqueue(db, "plan_run", run_id=run.id)
    db.commit()
    return run_detail(db, run)


@router.get("/runs/{run_id}", response_model=RunDetail)
def get_run(
    run_id: str, access: WorkspaceAccess = Depends(workspace_access), db: Session = Depends(get_db)
) -> RunDetail:
    return run_detail(db, _run(db, access, run_id))


@router.post("/runs/{run_id}/answer", response_model=RunDetail)
def answer(
    run_id: str,
    body: RunAnswer,
    access: WorkspaceAccess = Depends(workspace_access),
    db: Session = Depends(get_db),
) -> RunDetail:
    access.require("editor")
    run = _run(db, access, run_id)
    if run.status != "needs_input" or not run.clarifications:
        raise HTTPException(409, "This run is not waiting for an answer.")
    items = [dict(c) for c in run.clarifications]
    items[-1]["answer"] = body.answer.strip()
    run.clarifications = items
    run.status = "planning"
    Recorder(db, run).event("answered", body.answer.strip())
    enqueue(db, "plan_run", run_id=run.id)
    db.commit()
    return run_detail(db, run)


@router.post("/runs/{run_id}/approve", response_model=RunDetail)
def approve(
    run_id: str, access: WorkspaceAccess = Depends(workspace_access), db: Session = Depends(get_db)
) -> RunDetail:
    access.require("editor")
    run = _run(db, access, run_id)
    if run.status != "awaiting_approval":
        raise HTTPException(409, "This run has no plan waiting for approval.")
    run.status = "running"
    Recorder(db, run).event("approved", f"Plan approved by {access.user.login}")
    enqueue(db, "execute_run", run_id=run.id)
    db.commit()
    return run_detail(db, run)


@router.post("/runs/{run_id}/cancel", response_model=RunDetail)
def cancel(
    run_id: str, access: WorkspaceAccess = Depends(workspace_access), db: Session = Depends(get_db)
) -> RunDetail:
    access.require("editor")
    run = _run(db, access, run_id)
    if run.status in {"completed", "failed", "cancelled", "unsupported"}:
        raise HTTPException(409, "This run has already finished.")
    run.status = "cancelled"
    Recorder(db, run).event("cancelled", f"Cancelled by {access.user.login}")
    db.commit()
    return run_detail(db, run)


@router.patch("/insights/{insight_id}", response_model=InsightOut)
def review_insight(
    insight_id: str,
    body: InsightReview,
    access: WorkspaceAccess = Depends(workspace_access),
    db: Session = Depends(get_db),
) -> Insight:
    access.require("editor")
    insight = db.get(Insight, insight_id)
    run = db.get(Run, insight.run_id) if insight else None
    if insight is None or run is None or run.workspace_id != access.workspace.id:
        raise HTTPException(404, "Insight not found.")
    if body.status == "edited":
        if not body.claim or not body.claim.strip():
            raise HTTPException(400, "An edited insight needs the revised claim.")
        if insight.original_claim is None:
            insight.original_claim = insight.claim
        insight.claim = body.claim.strip()
    insight.status = body.status
    insight.review_note = body.note
    insight.reviewed_by = access.user.id if body.status != "draft" else None
    insight.reviewed_at = datetime.now(UTC) if body.status != "draft" else None
    db.commit()
    return insight
