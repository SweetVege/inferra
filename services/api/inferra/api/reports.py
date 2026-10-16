"""Reports: reviewed insights assembled for readers, published as a frozen snapshot and optionally
shared by an unguessable link that needs no sign-in.

Only accepted or edited insights may go into a report, checked when they are added and again when
the report is published, because a reviewer can reject an insight in between."""

import secrets
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from inferra.api.schemas import ReportCreate, ReportDetail, ReportSummary, ReportUpdate
from inferra.auth.session import WorkspaceAccess, workspace_access
from inferra.db.models import Artifact, Dataset, Insight, Report, Run, ToolCall, User, Workspace
from inferra.db.session import get_db

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["reports"])
public_router = APIRouter(prefix="/public", tags=["public"])

PUBLISHABLE = {"accepted", "edited"}


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _insights(db: Session, workspace_id: str, ids: list[str]) -> list[Insight]:
    """The insights in the given order, limited to this workspace."""
    rows = db.execute(
        select(Insight)
        .join(Run, Run.id == Insight.run_id)
        .where(Insight.id.in_(ids), Run.workspace_id == workspace_id)
    ).scalars()
    by_id = {i.id: i for i in rows}
    return [by_id[i] for i in ids if i in by_id]


def _check_publishable(insights: list[Insight], ids: list[str]) -> None:
    if len(insights) != len(set(ids)) or len(ids) != len(set(ids)):
        raise HTTPException(400, "Some insights do not exist in this workspace or are listed twice.")
    unreviewed = [i.title for i in insights if i.status not in PUBLISHABLE]
    if unreviewed:
        raise HTTPException(
            400,
            "Only accepted or edited insights can go into a report. Review first: " + "; ".join(unreviewed),
        )


def build_content(db: Session, report: Report) -> dict[str, Any]:
    """Everything a reader needs, self-contained: insights, the results they cite, and an appendix
    with the method behind each cited number."""
    workspace = db.get(Workspace, report.workspace_id)
    insights = _insights(db, report.workspace_id, list(report.insight_ids))
    reviewer_ids = {i.reviewed_by for i in insights if i.reviewed_by}
    reviewers = {u.id: u.name or u.login for u in db.scalars(select(User).where(User.id.in_(reviewer_ids)))}

    artifact_ids: list[str] = []
    for insight in insights:
        for ev in insight.evidence:
            if ev.get("artifact_id") and ev["artifact_id"] not in artifact_ids:
                artifact_ids.append(ev["artifact_id"])
    artifacts = {a.id: a for a in db.scalars(select(Artifact).where(Artifact.id.in_(artifact_ids)))}
    calls = {
        c.artifact_id: c for c in db.scalars(select(ToolCall).where(ToolCall.artifact_id.in_(artifact_ids)))
    }

    run_ids = list(dict.fromkeys(i.run_id for i in insights))
    runs = {r.id: r for r in db.scalars(select(Run).where(Run.id.in_(run_ids)))}
    dataset_ids = {d for r in runs.values() for d in r.dataset_ids}
    datasets = {d.id: d for d in db.scalars(select(Dataset).where(Dataset.id.in_(dataset_ids)))}

    appendix = []
    for run_id in run_ids:
        run = runs[run_id]
        computations = [
            {
                "artifact_id": aid,
                "title": artifacts[aid].title,
                "tool": calls[aid].tool if aid in calls else None,
                "code": calls[aid].code if aid in calls else None,
            }
            for aid in artifact_ids
            if aid in artifacts and artifacts[aid].run_id == run_id
        ]
        appendix.append(
            {
                "run_id": run.id,
                "goal": run.goal,
                "intent": run.intent,
                "plan_title": (run.plan or {}).get("title"),
                "ran_at": _iso(run.created_at),
                "datasets": [
                    {
                        "name": datasets[d].display_name,
                        "table": datasets[d].table_name,
                        "rows": datasets[d].row_count,
                    }
                    for d in run.dataset_ids
                    if d in datasets
                ],
                "steps": [{"agent": s.agent, "task": s.task, "summary": s.summary} for s in run.steps],
                "computations": computations,
            }
        )

    return {
        "title": report.title,
        "summary": report.summary,
        "workspace": workspace.name if workspace else "",
        "insights": [
            {
                "id": i.id,
                "run_id": i.run_id,
                "title": i.title,
                "claim": i.claim,
                "edited": i.status == "edited",
                "original_claim": i.original_claim,
                "evidence": i.evidence,
                "method": i.method,
                "caveats": i.caveats,
                "recommended_action": i.recommended_action,
                "confidence": i.confidence,
                "verified": i.verified,
                "checks": [{"name": c["name"], "passed": c["passed"]} for c in i.checks],
                "status": i.status,
                "reviewed_by": reviewers.get(i.reviewed_by or ""),
                "reviewed_at": _iso(i.reviewed_at),
            }
            for i in insights
        ],
        "artifacts": [
            {"id": a.id, "kind": a.kind, "title": a.title, "data": a.data}
            for a in (artifacts[aid] for aid in artifact_ids if aid in artifacts)
        ],
        "appendix": appendix,
    }


def publish(db: Session, report: Report, user: User) -> None:
    if not report.insight_ids:
        raise HTTPException(400, "Add at least one reviewed insight before publishing.")
    ids = list(report.insight_ids)
    _check_publishable(_insights(db, report.workspace_id, ids), ids)
    now = datetime.now(UTC)
    report.snapshot = {
        **build_content(db, report),
        "published_at": now.isoformat(),
        "published_by": user.name or user.login,
    }
    report.published_at = report.updated_at = now
    report.published_by = user.id


def _state(report: Report) -> str:
    if report.published_at is None:
        return "draft"
    published, updated = report.published_at, report.updated_at
    # SQLite hands back naive datetimes; compare in UTC.
    if published.tzinfo is None:
        published = published.replace(tzinfo=UTC)
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=UTC)
    return "changed" if updated > published else "published"


def _summary(report: Report) -> dict[str, Any]:
    return {
        "id": report.id,
        "title": report.title,
        "insight_count": len(report.insight_ids),
        "state": _state(report),
        "shared": report.share_token is not None,
        "published_at": report.published_at,
        "updated_at": report.updated_at,
    }


def _detail(db: Session, report: Report) -> ReportDetail:
    return ReportDetail(
        **_summary(report),
        summary=report.summary,
        insight_ids=list(report.insight_ids),
        share_token=report.share_token,
        content=build_content(db, report),
        published=report.snapshot,
    )


def _report(db: Session, access: WorkspaceAccess, report_id: str) -> Report:
    report = db.get(Report, report_id)
    if report is None or report.workspace_id != access.workspace.id:
        raise HTTPException(404, "Report not found.")
    return report


@router.get("/reports", response_model=list[ReportSummary])
def list_reports(
    access: WorkspaceAccess = Depends(workspace_access), db: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    reports = db.scalars(
        select(Report).where(Report.workspace_id == access.workspace.id).order_by(Report.updated_at.desc())
    )
    return [_summary(r) for r in reports]


@router.post("/reports", response_model=ReportDetail, status_code=201)
def create_report(
    body: ReportCreate, access: WorkspaceAccess = Depends(workspace_access), db: Session = Depends(get_db)
) -> ReportDetail:
    access.require("editor")
    _check_publishable(_insights(db, access.workspace.id, body.insight_ids), body.insight_ids)
    report = Report(
        workspace_id=access.workspace.id,
        title=body.title.strip(),
        summary=body.summary.strip(),
        insight_ids=body.insight_ids,
        created_by=access.user.id,
    )
    db.add(report)
    db.commit()
    return _detail(db, report)


@router.get("/reports/{report_id}", response_model=ReportDetail)
def get_report(
    report_id: str, access: WorkspaceAccess = Depends(workspace_access), db: Session = Depends(get_db)
) -> ReportDetail:
    return _detail(db, _report(db, access, report_id))


@router.patch("/reports/{report_id}", response_model=ReportDetail)
def update_report(
    report_id: str,
    body: ReportUpdate,
    access: WorkspaceAccess = Depends(workspace_access),
    db: Session = Depends(get_db),
) -> ReportDetail:
    access.require("editor")
    report = _report(db, access, report_id)
    if body.insight_ids is not None:
        # Insights already in the report may have been reset since; publishing re-checks them all.
        added = [i for i in body.insight_ids if i not in report.insight_ids]
        _check_publishable(_insights(db, access.workspace.id, added), added)
        if len(body.insight_ids) != len(set(body.insight_ids)):
            raise HTTPException(400, "An insight is listed twice.")
        report.insight_ids = body.insight_ids
    if body.title is not None:
        report.title = body.title.strip()
    if body.summary is not None:
        report.summary = body.summary.strip()
    report.updated_at = datetime.now(UTC)
    db.commit()
    return _detail(db, report)


@router.post("/reports/{report_id}/publish", response_model=ReportDetail)
def publish_report(
    report_id: str, access: WorkspaceAccess = Depends(workspace_access), db: Session = Depends(get_db)
) -> ReportDetail:
    """Freeze the current content. Readers of the share link see this snapshot until the next publish."""
    access.require("editor")
    report = _report(db, access, report_id)
    publish(db, report, access.user)
    db.commit()
    return _detail(db, report)


@router.post("/reports/{report_id}/share", response_model=ReportDetail)
def share_report(
    report_id: str, access: WorkspaceAccess = Depends(workspace_access), db: Session = Depends(get_db)
) -> ReportDetail:
    access.require("editor")
    report = _report(db, access, report_id)
    if report.snapshot is None:
        raise HTTPException(409, "Publish the report before sharing it.")
    if report.share_token is None:
        report.share_token = secrets.token_urlsafe(24)
        db.commit()
    return _detail(db, report)


@router.delete("/reports/{report_id}/share", response_model=ReportDetail)
def revoke_share(
    report_id: str, access: WorkspaceAccess = Depends(workspace_access), db: Session = Depends(get_db)
) -> ReportDetail:
    """The old link stops working at once; sharing again creates a new one."""
    access.require("editor")
    report = _report(db, access, report_id)
    report.share_token = None
    db.commit()
    return _detail(db, report)


@router.delete("/reports/{report_id}", status_code=204)
def delete_report(
    report_id: str, access: WorkspaceAccess = Depends(workspace_access), db: Session = Depends(get_db)
) -> None:
    access.require("editor")
    db.delete(_report(db, access, report_id))
    db.commit()


@public_router.get("/reports/{token}")
def public_report(token: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    """A shared report's published snapshot. No session needed; the token is the permission."""
    report = None
    if len(token) >= 16:
        report = db.scalars(select(Report).where(Report.share_token == token)).first()
    if report is None or report.snapshot is None:
        raise HTTPException(404, "This report link is not valid. It may have been revoked.")
    return report.snapshot
