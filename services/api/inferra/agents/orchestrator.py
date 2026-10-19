"""The Orchestrator: natural-language goal -> intent + parameters -> plan from a workflow template."""

import json
import re
from dataclasses import dataclass
from typing import Any

from inferra.agents import prompts
from inferra.db.models import Dataset
from inferra.llm.gateway import LLMClient, tool_uses
from inferra.runtime.recorder import Recorder
from inferra.workflows.templates import WORKFLOWS, IntentCatalog, instantiate

MAX_CLARIFICATIONS = 2

SUBMIT_PLAN = {
    "name": "submit_plan",
    "description": "Submit the chosen workflow and its parameters.",
    "input_schema": {
        "type": "object",
        "properties": {
            "intent": {"type": "string", "enum": sorted(WORKFLOWS)},
            "dataset_ids": {"type": "array", "items": {"type": "string"}},
            "params": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}, "value": {"type": "string"}},
                    "required": ["name", "value"],
                },
            },
            "rationale": {"type": "string", "description": "One sentence on why this plan fits."},
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        },
        "required": ["intent", "dataset_ids", "params", "rationale", "confidence"],
    },
}

ASK_CLARIFICATION = {
    "name": "ask_clarification",
    "description": "Ask the user one short question when the goal is ambiguous.",
    "input_schema": {
        "type": "object",
        "properties": {"question": {"type": "string"}},
        "required": ["question"],
    },
}


@dataclass
class PlanDecision:
    kind: str  # plan | question | unsupported
    plan: dict[str, Any] | None = None
    question: str | None = None
    message: str | None = None


MAX_CATALOG_COLUMNS = 40


def _words(text: str) -> set[str]:
    return {w for w in re.split(r"[^a-z0-9]+", text.lower()) if len(w) > 2}


def pick_columns(
    columns: list[dict[str, str]], goal: str, limit: int = MAX_CATALOG_COLUMNS
) -> list[dict[str, str]]:
    """At most `limit` columns for a prompt: those whose names share a word with the goal first,
    then the rest in table order. Wide tables (hundreds of columns are common in public data)
    would otherwise crowd the prompt and dilute the plan."""
    if len(columns) <= limit:
        return columns
    words = _words(goal)
    relevant = [c for c in columns if _words(c["name"]) & words]
    keep = {c["name"] for c in (relevant + [c for c in columns if c not in relevant])[:limit]}
    return [c for c in columns if c["name"] in keep]


def dataset_catalog(datasets: list[Dataset], goal: str = "") -> list[dict[str, Any]]:
    catalog = []
    for d in datasets:
        shown = pick_columns(d.columns, goal)
        entry: dict[str, Any] = {
            "id": d.id,
            "table": d.table_name,
            "name": d.display_name,
            "rows": d.row_count,
            "description": d.description,
            "columns": [f"{c['name']} ({c['type']})" for c in shown],
        }
        if len(shown) < len(d.columns):
            entry["more_columns"] = (
                f"{len(d.columns) - len(shown)} more columns not shown; "
                "list_tables with this table and a search word finds them."
            )
        catalog.append(entry)
    return catalog


def plan_goal(
    goal: str,
    datasets: list[Dataset],
    selected_ids: list[str],
    clarifications: list[dict[str, str]],
    llm: LLMClient,
    rec: Recorder,
    metrics: list[dict[str, Any]] | None = None,
) -> PlanDecision:
    catalog = dataset_catalog(datasets, goal)
    valid_ids = {d.id for d in datasets}
    lines = [
        f"Goal: {goal}",
        "",
        "Workflow catalog:",
        IntentCatalog().describe(),
        "",
        "Dataset catalog (JSON):",
        json.dumps(catalog, indent=1),
        "",
        f"Datasets the user selected: {selected_ids or 'none (choose what is needed)'}",
    ]
    if metrics:
        lines += [
            "",
            "Defined metrics (prefer these when the goal mentions them):",
            *[f"- {m['name']}: {m['label']} on table {m['table']} ({m['kind']})" for m in metrics],
        ]
    answered = [c for c in clarifications if c.get("answer")]
    if answered:
        lines += ["", "Clarifications so far:"]
        lines += [f"Q: {c['question']}\nA: {c['answer']}" for c in answered]
    if len(answered) >= MAX_CLARIFICATIONS:
        lines += ["", "You have asked enough questions. Submit the best plan now."]
    tools = [SUBMIT_PLAN] if len(answered) >= MAX_CLARIFICATIONS else [SUBMIT_PLAN, ASK_CLARIFICATION]
    messages: list[dict[str, Any]] = [{"role": "user", "content": "\n".join(lines)}]

    for _attempt in range(3):
        response = llm.create(role="planning", system=prompts.ORCHESTRATOR, tools=tools, messages=messages)
        rec.usage(response.usage)
        messages.append({"role": "assistant", "content": response.content})
        uses = tool_uses(response)
        if not uses:
            messages.append({"role": "user", "content": "Call submit_plan or ask_clarification."})
            continue
        use = uses[0]
        if use.name == "ask_clarification" and "ask_clarification" in {t["name"] for t in tools}:
            return PlanDecision("question", question=str(use.input.get("question", "")).strip())
        if use.name != "submit_plan":
            messages.append(_reject(uses, "Use submit_plan or ask_clarification."))
            continue

        args = use.input
        intent = args.get("intent")
        problems = []
        if intent not in WORKFLOWS:
            problems.append(f"Unknown intent '{intent}'.")
        ids = [i for i in args.get("dataset_ids", []) if i in valid_ids]
        if not ids:
            problems.append("Choose at least one dataset id from the catalog.")
        params = {p.get("name", ""): p.get("value", "") for p in args.get("params", [])}
        if intent in WORKFLOWS:
            missing = [p.name for p in WORKFLOWS[intent].params if p.required and not params.get(p.name)]
            if missing:
                problems.append(f"Missing required params: {', '.join(missing)}.")
        if problems:
            messages.append(_reject(uses, " ".join(problems)))
            continue

        workflow = WORKFLOWS[intent]
        if not workflow.available:
            return PlanDecision(
                "unsupported",
                message=f"This looks like '{workflow.title}', which arrives in {workflow.phase}. "
                "Try exploring the data or asking a metric question for now.",
            )
        plan = instantiate(intent, params)
        plan.update(
            dataset_ids=ids,
            rationale=str(args.get("rationale", "")),
            confidence=args.get("confidence", "medium"),
        )
        return PlanDecision("plan", plan=plan)

    return PlanDecision("unsupported", message="Inferra could not turn this goal into a plan.")


def _reject(uses: list[Any], message: str) -> dict[str, Any]:
    return {
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": u.id, "content": message, "is_error": True} for u in uses
        ],
    }
