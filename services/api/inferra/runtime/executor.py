"""Run lifecycle. Plain code walks the plan; language models only act inside a step.

planning -> (needs_input -> planning)* -> awaiting_approval -> running -> completed
Any stage may end in failed or unsupported; a user may cancel before completion."""

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from inferra.agents.insight import write_insights
from inferra.agents.orchestrator import dataset_catalog, plan_goal
from inferra.agents.specialist import SPECIALISTS, run_specialist
from inferra.config import get_settings
from inferra.datasources.registry import datasource_for
from inferra.db.models import Artifact, Dataset, Insight, Run, Step, Workspace
from inferra.llm.gateway import LLMUnavailable, get_llm
from inferra.metrics_layer import metric_specs
from inferra.runtime.queue import enqueue, handler
from inferra.runtime.recorder import Recorder
from inferra.runtime.scheduler import raise_alerts, schedule_monitor
from inferra.tools.base import ToolContext

AGENT_TITLES = {
    "data": "Data Agent",
    "analyst": "Analyst Agent",
    "insight": "Insight Agent",
    "experiment": "Experiment Agent",
    "ml": "ML Agent",
    "modeler": "Data Modeler",
}


def _now() -> datetime:
    return datetime.now(UTC)


def _datasets(db: Session, workspace_id: str, ids: list[str] | None = None) -> list[Dataset]:
    query = select(Dataset).where(Dataset.workspace_id == workspace_id)
    if ids:
        query = query.where(Dataset.id.in_(ids))
    return list(db.scalars(query.order_by(Dataset.created_at)))


@handler("plan_run")
def plan_run(db: Session, payload: dict[str, Any]) -> None:
    run = db.get(Run, payload["run_id"])
    if run is None or run.status != "planning":
        return
    rec = Recorder(db, run)
    datasets = _datasets(db, run.workspace_id)
    if not datasets:
        _finish(db, run, "failed", "Upload a dataset before asking a question.")
        return
    rec.event("planning", "Orchestrator is reading the goal and the dataset catalog")
    try:
        decision = plan_goal(
            run.goal,
            datasets,
            list(run.dataset_ids),
            list(run.clarifications),
            get_llm(),
            rec,
            metrics=list(metric_specs(db, run.workspace_id).values()),
        )
    except LLMUnavailable as exc:
        _finish(db, run, "failed", str(exc))
        return

    if decision.kind == "question":
        run.clarifications = [*run.clarifications, {"question": decision.question, "answer": None}]
        run.status = "needs_input"
        db.commit()
        rec.event("clarification", decision.question or "")
        return
    if decision.kind == "unsupported" or decision.plan is None:
        _finish(db, run, "unsupported", decision.message or "This goal is not supported yet.")
        return

    plan = decision.plan
    run.plan = plan
    run.intent = plan["intent"]
    run.dataset_ids = plan["dataset_ids"]
    for position, st in enumerate(plan["steps"]):
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
    workspace = db.get(Workspace, run.workspace_id)
    if workspace is not None and workspace.auto_approve_plans:
        run.status = "running"
        enqueue(db, "execute_run", run_id=run.id)
    else:
        run.status = "awaiting_approval"
    db.commit()
    rec.event(
        "plan_ready", f"Plan ready: {plan['title']} ({len(plan['steps'])} steps)", intent=plan["intent"]
    )


def _finish(db: Session, run: Run, status: str, error: str | None = None) -> None:
    run.status = status
    run.error = error
    db.commit()
    Recorder(db, run).event(status, error or f"Run {status}")


def _brief(
    run: Run,
    step: Step,
    datasets: list[Dataset],
    done: list[Step],
    artifacts: list[Artifact],
    metrics: dict[str, dict[str, Any]] | None = None,
) -> str:
    plan = run.plan or {}
    by_step: dict[str, list[Artifact]] = {}
    for a in artifacts:
        by_step.setdefault(a.step_id or "", []).append(a)
    lines = [
        f"Goal: {run.goal}",
        f"Plan: {plan.get('title')} (intent: {run.intent})",
        f"Parameters: {json.dumps(plan.get('params', {}))}",
    ]
    answered = [c for c in run.clarifications if c.get("answer")]
    if answered:
        lines.append("Clarifications: " + "; ".join(f"{c['question']} -> {c['answer']}" for c in answered))
    if metrics:
        lines += ["", "Defined metrics (use these names with the metric tools):"]
        lines += [f"- {m['name']}: {m['label']} ({m['kind']} on {m['table']})" for m in metrics.values()]
    lines += [
        "",
        f"Your task: {step.task}",
        "",
        "Datasets in scope (JSON):",
        json.dumps(dataset_catalog(datasets), indent=1),
    ]
    if done:
        lines += ["", "Results from earlier steps:"]
        for s in done:
            arts = ", ".join(f"{a.id} ({a.kind}: {a.title})" for a in by_step.get(s.id, []))
            lines.append(
                f"- {AGENT_TITLES.get(s.agent, s.agent)}: {s.summary}\n  Artifacts: {arts or 'none'}"
            )
    return "\n".join(lines)


def _context_text(run: Run, datasets: list[Dataset]) -> str:
    parts = [run.goal, json.dumps((run.plan or {}).get("params", {}))]
    parts += [c.get("answer") or "" for c in run.clarifications]
    for d in datasets:
        parts.append(d.table_name)
        parts += [c["name"] for c in d.columns]
    return " ".join(parts)


@handler("execute_run")
def execute_run(db: Session, payload: dict[str, Any]) -> None:
    run = db.get(Run, payload["run_id"])
    if run is None or run.status != "running":
        return
    rec = Recorder(db, run)
    datasets = _datasets(db, run.workspace_id, list(run.dataset_ids))
    source = datasource_for(db, run.workspace_id, list(run.dataset_ids))
    ctx = ToolContext(
        source=source,
        row_limit=get_settings().sql_row_limit,
        metrics=metric_specs(db, run.workspace_id, list(run.dataset_ids)),
    )
    try:
        llm = get_llm()
    except LLMUnavailable as exc:
        _finish(db, run, "failed", str(exc))
        return

    completed: list[Step] = []
    for step in list(run.steps):
        db.refresh(run)
        if run.status == "cancelled":
            return
        if any(dep not in {s.key for s in completed} for dep in step.depends_on):
            step.status = "skipped"
            db.commit()
            continue
        step.status, step.started_at = "running", _now()
        db.commit()
        title = AGENT_TITLES.get(step.agent, step.agent)
        rec.event("step_started", f"{title}: {step.task}", step=step.key, agent=step.agent)
        try:
            artifacts = list(
                db.scalars(select(Artifact).where(Artifact.run_id == run.id).order_by(Artifact.created_at))
            )
            if step.agent == "insight":
                summaries = [
                    {"agent": AGENT_TITLES.get(s.agent, s.agent), "task": s.task, "summary": s.summary or ""}
                    for s in completed
                ]
                outcome = write_insights(
                    run.goal, summaries, artifacts, _context_text(run, datasets), llm, rec
                )
                for position, item in enumerate(outcome.insights):
                    d, verdict = item.draft, item.verdict
                    db.add(
                        Insight(
                            run_id=run.id,
                            position=position,
                            title=str(d.get("title", ""))[:300],
                            claim=str(d.get("claim", "")),
                            evidence=verdict.resolved_evidence,
                            method=str(d.get("method", "")),
                            caveats=list(d.get("caveats", [])),
                            recommended_action=d.get("recommended_action"),
                            confidence=d.get("confidence", "medium") if verdict.verified else "low",
                            checks=[c.as_dict() for c in verdict.checks],
                            verified=verdict.verified,
                        )
                    )
                verified = sum(1 for i in outcome.insights if i.verdict.verified)
                step.summary = (
                    f"Drafted {len(outcome.insights)} insights; {verified} passed "
                    f"verification after {outcome.revisions} revision(s)."
                )
            else:
                spec = SPECIALISTS.get(step.agent)
                if spec is None:
                    raise RuntimeError(f"No agent named '{step.agent}'.")
                result = run_specialist(
                    spec, step, _brief(run, step, datasets, completed, artifacts, ctx.metrics), ctx, llm, rec
                )
                step.summary = result.summary
            step.status, step.finished_at = "completed", _now()
            db.commit()
            completed.append(step)
            rec.event("step_completed", f"{title} finished", step=step.key)
        except LLMUnavailable as exc:
            step.status, step.error, step.finished_at = "failed", str(exc), _now()
            db.commit()
            _finish(db, run, "failed", str(exc))
            return
        except Exception as exc:
            step.status, step.error, step.finished_at = "failed", f"{type(exc).__name__}: {exc}", _now()
            db.commit()
            _finish(db, run, "failed", f"{title} failed: {exc}")
            return

    schedule = schedule_monitor(db, run)
    raise_alerts(db, run, schedule.id if schedule else run.schedule_id)
    _finish(db, run, "completed")
