from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from inferra.api.schemas import (
    InsightFeedItem,
    InsightOut,
    InsightReview,
    RunAnswer,
    RunCreate,
    RunDetail,
    RunSummary,
)
from inferra.auth.session import WorkspaceAccess, workspace_access
from inferra.db.models import Alert, Artifact, Dataset, Insight, Run, RunEvent, Schedule, ToolCall
from inferra.db.session import get_db
from inferra.llm.gateway import llm_available
from inferra.runtime.queue import enqueue
from inferra.runtime.quota import require_run_quota
from inferra.runtime.recorder import Recorder

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["runs"])


def _run(db: Session, access: WorkspaceAccess, run_id: str) -> Run:
    run = db.get(Run, run_id)
    if run is None or run.workspace_id != access.workspace.id:
        raise HTTPException(404, "Run not found.")
    return run


def run_detail(db: Session, run: Run) -> RunDetail:
    from inferra.api.schedules import schedule_out

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
                    "schedule_id",
                )
            },
            "schedules": [
                schedule_out(db, s, with_runs=False)
                for s in db.scalars(select(Schedule).where(Schedule.source_run_id == run.id))
            ],
            "alerts": rows(Alert, Alert.created_at),
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
    require_run_quota(db, access.user)
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


@router.post("/runs/{run_id}/retry", response_model=RunDetail, status_code=201)
def retry(
    run_id: str, access: WorkspaceAccess = Depends(workspace_access), db: Session = Depends(get_db)
) -> RunDetail:
    """Start a new run for a failed one. A run only fails after planning once its plan was
    approved, so the plan is reused as is; a run that failed while planning plans again."""
    from inferra.runtime.executor import add_steps

    access.require("editor")
    old = _run(db, access, run_id)
    if old.status != "failed":
        raise HTTPException(409, "Only a failed run can be retried.")
    if not llm_available():
        raise HTTPException(503, "Agent runs need a configured model.")
    require_run_quota(db, access.user)
    run = Run(
        workspace_id=access.workspace.id,
        created_by=access.user.id,
        goal=old.goal,
        dataset_ids=list(old.dataset_ids),
        clarifications=list(old.clarifications),
        status="running" if old.plan else "planning",
        intent=old.intent,
        plan=old.plan,
    )
    db.add(run)
    db.flush()
    rec = Recorder(db, run)
    rec.event("created", "Retry of an earlier run", retry_of=old.id)
    if old.plan:
        add_steps(db, run, old.plan)
        rec.event("approved", "Runs the plan approved for the earlier run")
        enqueue(db, "execute_run", run_id=run.id)
    else:
        enqueue(db, "plan_run", run_id=run.id)
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
        _apply_edit(db, run, insight, body)
    insight.status = body.status
    insight.review_note = body.note
    insight.reviewed_by = access.user.id if body.status != "draft" else None
    insight.reviewed_at = datetime.now(UTC) if body.status != "draft" else None
    db.commit()
    return insight


# Editable field -> the column that keeps what the agent wrote.
EDITABLE = {"title": "original_title", "claim": "original_claim", "recommended_action": "original_action"}


def _apply_edit(db: Session, run: Run, insight: Insight, body: InsightReview) -> None:
    """Apply a reviewer's rewrite and run the verifier on it, so "Verified" keeps meaning that every
    number traces back to a tool's output. An edit that would break a verified insight is refused."""
    from inferra.runtime.executor import verifier_context
    from inferra.verify.verifier import verify_insight

    changes = {
        field: value.strip()
        for field in EDITABLE
        if (value := getattr(body, field)) is not None and value.strip() != (getattr(insight, field) or "")
    }
    if not changes.get("title", "x") or not changes.get("claim", "x"):
        raise HTTPException(400, "The title and the claim cannot be empty.")
    if not changes and insight.status != "edited":
        raise HTTPException(400, "Nothing was changed.")

    draft = {
        "title": changes.get("title", insight.title),
        "claim": changes.get("claim", insight.claim),
        "recommended_action": changes.get("recommended_action", insight.recommended_action),
        "caveats": insight.caveats,
        "evidence": insight.evidence,
        "confidence": insight.confidence,
    }
    artifacts = db.scalars(select(Artifact).where(Artifact.run_id == run.id))
    verdict = verify_insight(
        draft, {a.id: {"kind": a.kind, "data": a.data} for a in artifacts}, verifier_context(db, run)
    )
    if insight.verified and not verdict.verified:
        raise HTTPException(
            422, "This edit would no longer pass verification. " + "; ".join(verdict.failures())
        )
    for field, value in changes.items():
        original = EDITABLE[field]
        if getattr(insight, original) is None:
            setattr(insight, original, getattr(insight, field) or "")
        setattr(insight, field, value or None)  # an emptied next step is removed
    insight.checks = [c.as_dict() for c in verdict.checks]
    insight.verified = verdict.verified


@router.get("/insights", response_model=list[InsightFeedItem])
def insight_feed(
    status: str | None = None,
    verified: bool | None = None,
    access: WorkspaceAccess = Depends(workspace_access),
    db: Session = Depends(get_db),
) -> list[InsightFeedItem]:
    """Insights across all runs in the workspace, newest first."""
    query = (
        select(Insight, Run.goal, Run.intent)
        .join(Run, Run.id == Insight.run_id)
        .where(Run.workspace_id == access.workspace.id)
    )
    if status:
        query = query.where(Insight.status.in_(status.split(",")))
    if verified is not None:
        query = query.where(Insight.verified.is_(verified))
    rows = db.execute(query.order_by(Insight.created_at.desc(), Insight.position).limit(200)).all()
    return [
        InsightFeedItem.model_validate(
            {
                **InsightOut.model_validate(i).model_dump(),
                "run_goal": goal,
                "run_intent": intent,
                "created_at": i.created_at,
            }
        )
        for i, goal, intent in rows
    ]
