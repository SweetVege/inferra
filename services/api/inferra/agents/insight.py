"""The Insight Agent drafts insights; the deterministic verifier decides whether they hold."""

import json
from dataclasses import dataclass, field
from typing import Any

from inferra.agents import prompts
from inferra.config import get_settings
from inferra.db.models import Artifact
from inferra.llm.gateway import LLMClient, tool_uses
from inferra.runtime.recorder import Recorder
from inferra.verify.verifier import Verdict, verify_insight

MAX_ARTIFACT_CHARS = 16000

SUBMIT_INSIGHTS = {
    "name": "submit_insights",
    "description": "Submit the complete list of insights for verification.",
    "input_schema": {
        "type": "object",
        "properties": {
            "insights": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "claim": {"type": "string"},
                        "evidence": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "artifact_id": {"type": "string"},
                                    "path": {"type": "string"},
                                    "value": {"type": ["number", "string"]},
                                    "label": {"type": "string"},
                                },
                                "required": ["artifact_id", "path", "value"],
                            },
                        },
                        "method": {"type": "string"},
                        "caveats": {"type": "array", "items": {"type": "string"}},
                        "recommended_action": {"type": "string"},
                        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    },
                    "required": ["title", "claim", "evidence", "method", "caveats", "confidence"],
                },
            }
        },
        "required": ["insights"],
    },
}


@dataclass
class VerifiedInsight:
    draft: dict[str, Any]
    verdict: Verdict


@dataclass
class InsightOutcome:
    insights: list[VerifiedInsight] = field(default_factory=list)
    revisions: int = 0


def artifact_view(artifact: Artifact) -> dict[str, Any]:
    data = artifact.data
    if artifact.kind == "profile":
        data = {
            **data,
            "columns": {
                k: {kk: vv for kk, vv in v.items() if kk != "histogram"}
                for k, v in data.get("columns", {}).items()
            },
        }
    if artifact.kind == "data_model":
        # Keep list positions intact so paths still resolve against the stored artifact.
        data = {
            **data,
            "nodes": [
                {k: v for k, v in n.items() if k not in {"sql", "preview"}}
                | {"columns": [c["name"] for c in n.get("columns", [])]}
                for n in data.get("nodes", [])
            ],
            "tests": [
                {k: v for k, v in t.items() if k != "sql"} | {"sample": (t.get("sample") or [])[:2]}
                for t in data.get("tests", [])
            ],
        }
    text = json.dumps(data, default=str)
    if len(text) > MAX_ARTIFACT_CHARS and "rows" in data:
        data = {**data, "rows": data["rows"][:25], "note": "first 25 rows shown"}
        text = json.dumps(data, default=str)
    if len(text) > MAX_ARTIFACT_CHARS:
        text = text[:MAX_ARTIFACT_CHARS] + "... [truncated]"
    return {"artifact_id": artifact.id, "kind": artifact.kind, "title": artifact.title, "data": text}


def write_insights(
    goal: str,
    step_summaries: list[dict[str, str]],
    artifacts: list[Artifact],
    context_text: str,
    llm: LLMClient,
    rec: Recorder,
) -> InsightOutcome:
    settings = get_settings()
    by_id = {a.id: {"kind": a.kind, "data": a.data} for a in artifacts}
    brief = "\n".join(
        [
            f"Goal: {goal}",
            "",
            "Specialist summaries:",
            *[f"- [{s['agent']}] {s['task']}\n  {s['summary']}" for s in step_summaries],
            "",
            "Artifacts (data is JSON; cite paths into it):",
            *[json.dumps(artifact_view(a)) for a in artifacts],
        ]
    )
    messages: list[dict[str, Any]] = [{"role": "user", "content": brief}]
    outcome = InsightOutcome()
    latest: list[VerifiedInsight] = []

    for _turn in range(settings.max_insight_revisions + 3):
        response = llm.create(
            role="insight", system=prompts.INSIGHT_AGENT, tools=[SUBMIT_INSIGHTS], messages=messages
        )
        rec.usage(response.usage)
        messages.append({"role": "assistant", "content": response.content})
        uses = [u for u in tool_uses(response) if u.name == "submit_insights"]
        if not uses:
            messages.append({"role": "user", "content": "Submit your insights with submit_insights."})
            continue
        use = uses[0]
        drafts = [d for d in use.input.get("insights", []) if isinstance(d, dict)]
        latest = [VerifiedInsight(d, verify_insight(d, by_id, context_text)) for d in drafts]
        failures = [
            f"Insight {i + 1} ('{v.draft.get('title', '')}'): " + " | ".join(v.verdict.failures())
            for i, v in enumerate(latest)
            if not v.verdict.verified
        ]
        passed = len(latest) - len(failures)
        rec.event(
            "verification",
            f"Verifier passed {passed} of {len(latest)} insights",
            revision=outcome.revisions,
            failures=failures,
        )
        if not failures and latest:
            break
        if outcome.revisions >= settings.max_insight_revisions:
            break
        outcome.revisions += 1
        feedback = (
            "The verifier rejected some insights. Fix these problems and resubmit the complete "
            "list (drop an insight if its numbers cannot be cited):\n" + "\n".join(failures)
            if latest
            else "Submit at least one insight."
        )
        messages.append(
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": use.id, "content": feedback, "is_error": True}
                ],
            }
        )

    outcome.insights = latest
    return outcome
