"""Specialist agents: a system prompt, an allowed tool set, and one shared tool-use loop."""

import json
import time
from dataclasses import dataclass
from typing import Any

from inferra.agents import prompts
from inferra.config import get_settings
from inferra.db.models import Step
from inferra.llm.gateway import LLMClient, text_of, tool_uses
from inferra.runtime.recorder import Recorder
from inferra.tools.base import ToolContext, ToolError, get_tools

MAX_RESULT_CHARS = 12000


@dataclass(frozen=True)
class AgentSpec:
    name: str
    title: str
    system: str
    tools: tuple[str, ...]


SPECIALISTS: dict[str, AgentSpec] = {
    "data": AgentSpec(
        "data",
        "Data Agent",
        prompts.DATA_AGENT,
        ("list_tables", "profile_dataset", "summarize_column", "run_sql"),
    ),
    "analyst": AgentSpec(
        "analyst",
        "Analyst Agent",
        prompts.ANALYST_AGENT,
        ("list_tables", "run_sql", "summarize_column", "group_summary", "correlations"),
    ),
    "experiment": AgentSpec(
        "experiment",
        "Experiment Agent",
        prompts.EXPERIMENT_AGENT,
        (
            "list_tables",
            "summarize_column",
            "run_sql",
            "metric_baseline",
            "power_analysis",
            "srm_check",
            "ab_test",
            "cuped_test",
            "segment_effects",
            "bayesian_ab",
            "sequential_test",
        ),
    ),
}

FINISH_STEP = {
    "name": "finish_step",
    "description": "Finish your task and hand a summary to the rest of the team.",
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": "What you did and found, citing artifact ids for key results.",
            },
            "key_artifact_ids": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["summary"],
    },
}


@dataclass
class StepOutcome:
    summary: str
    artifact_ids: list[str]


def _dump(result: Any) -> str:
    text = json.dumps(result, default=str)
    if len(text) > MAX_RESULT_CHARS:
        text = text[:MAX_RESULT_CHARS] + "... [truncated; aggregate further to see more]"
    return text


def run_specialist(
    spec: AgentSpec, step: Step, brief: str, ctx: ToolContext, llm: LLMClient, rec: Recorder
) -> StepOutcome:
    """Run one plan step to completion and return the specialist's hand-off summary."""
    settings = get_settings()
    tools = {t.name: t for t in get_tools(list(spec.tools))}
    definitions = [t.definition() for t in tools.values()] + [FINISH_STEP]
    messages: list[dict[str, Any]] = [{"role": "user", "content": brief}]
    produced: list[str] = []
    calls = 0
    nudged = False

    for _ in range(settings.max_tool_calls_per_step + 4):
        response = llm.create(role="specialist", system=spec.system, tools=definitions, messages=messages)
        rec.usage(response.usage)
        messages.append({"role": "assistant", "content": response.content})
        uses = tool_uses(response)
        if not uses:
            if nudged:
                return StepOutcome(text_of(response) or "No summary provided.", produced)
            nudged = True
            messages.append({"role": "user", "content": "Please call finish_step with your summary now."})
            continue

        results = []
        for use in uses:
            if use.name == "finish_step":
                summary = str(use.input.get("summary", "")).strip() or "No summary provided."
                keys = [a for a in use.input.get("key_artifact_ids", []) if a in produced]
                return StepOutcome(summary, keys or produced)
            if use.name not in tools:
                results.append(_error(use.id, f"Unknown tool '{use.name}'."))
                continue
            if calls >= settings.max_tool_calls_per_step:
                results.append(_error(use.id, "Tool budget for this step is used up. Call finish_step."))
                continue
            calls += 1
            results.append(_execute(tools[use.name], use, step, spec, ctx, rec, produced))
        messages.append({"role": "user", "content": results})

    return StepOutcome("The step stopped after reaching its turn limit.", produced)


def _error(tool_use_id: str, message: str) -> dict[str, Any]:
    return {"type": "tool_result", "tool_use_id": tool_use_id, "content": message, "is_error": True}


def _execute(tool, use, step, spec, ctx, rec, produced) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    started = time.perf_counter()
    rec.event("tool_started", f"{spec.title} → {use.name}", step=step.key, tool=use.name)
    try:
        output = tool.run(ctx, dict(use.input))
    except ToolError as exc:
        rec.tool_call(
            step,
            spec.name,
            use.name,
            dict(use.input),
            code=None,
            artifact_id=None,
            error=str(exc),
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
        return _error(use.id, str(exc))
    artifact_id = None
    if output.artifact is not None:
        artifact = rec.artifact(step, output.artifact)
        artifact_id = artifact.id
        produced.append(artifact_id)
    rec.tool_call(
        step,
        spec.name,
        use.name,
        dict(use.input),
        code=output.code,
        artifact_id=artifact_id,
        error=None,
        duration_ms=int((time.perf_counter() - started) * 1000),
    )
    payload = {"artifact_id": artifact_id, "result": output.result} if artifact_id else output.result
    return {"type": "tool_result", "tool_use_id": use.id, "content": _dump(payload)}
