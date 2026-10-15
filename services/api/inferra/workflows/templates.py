"""Workflow templates: the fixed shape of work for each kind of question.

The Orchestrator classifies a goal into one of these intents and fills in its parameters. It can
skip optional steps, but it cannot invent new ones. Intents marked unavailable are recognized so
Inferra can say clearly that a capability is on the roadmap instead of improvising."""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Param:
    name: str
    description: str
    required: bool = False


@dataclass(frozen=True)
class StepTemplate:
    key: str
    agent: str  # data | analyst | experiment | ml | insight
    task: str  # may reference {params} with str.format-style placeholders
    depends_on: tuple[str, ...] = ()
    optional: bool = False


@dataclass(frozen=True)
class Workflow:
    intent: str
    title: str
    description: str
    examples: tuple[str, ...]
    params: tuple[Param, ...] = ()
    steps: tuple[StepTemplate, ...] = ()
    available: bool = True
    phase: str = ""


INSIGHT_TASK = (
    "Turn the results of the previous steps into verified insights that answer the goal. "
    "Cite artifact values for every number."
)

WORKFLOWS: dict[str, Workflow] = {
    w.intent: w
    for w in [
        Workflow(
            intent="explore",
            title="Explore a dataset",
            description="Understand what a dataset contains, its quality, and its notable patterns.",
            examples=("What's in this dataset?", "Give me an overview of the orders table."),
            params=(Param("focus", "Optional area of interest, e.g. 'revenue columns'."),),
            steps=(
                StepTemplate(
                    "profile",
                    "data",
                    "Profile each dataset in scope and identify data-quality issues that could "
                    "affect analysis. Focus: {focus}.",
                ),
                StepTemplate(
                    "patterns",
                    "analyst",
                    "Find the most informative distributions, segment differences, and "
                    "relationships between columns. Focus: {focus}.",
                    depends_on=("profile",),
                ),
                StepTemplate("insights", "insight", INSIGHT_TASK, depends_on=("profile", "patterns")),
            ),
        ),
        Workflow(
            intent="metric_query",
            title="Answer a metric question",
            description="Compute a specific number, breakdown, or trend that the user asks for.",
            examples=("Average order value by region", "How many active users per month?"),
            params=(
                Param("metric", "The measure to compute, in plain words.", required=True),
                Param("breakdown", "Dimensions or time grain to break the metric down by."),
                Param("filters", "Any filters or time window the user mentioned."),
            ),
            steps=(
                StepTemplate(
                    "prepare",
                    "data",
                    "Find the tables and columns that define '{metric}', check they are usable, "
                    "and note any data issues that affect it. Filters: {filters}.",
                ),
                StepTemplate(
                    "compute",
                    "analyst",
                    "Compute '{metric}' broken down by {breakdown}, applying filters: {filters}. "
                    "Report totals alongside the breakdown.",
                    depends_on=("prepare",),
                ),
                StepTemplate("insights", "insight", INSIGHT_TASK, depends_on=("prepare", "compute")),
            ),
        ),
        Workflow(
            intent="diagnose",
            title="Diagnose a metric change",
            description="Explain why a metric moved by decomposing the change across dimensions.",
            examples=("Why did conversion drop last week?",),
            available=False,
            phase="Phase 3",
        ),
        Workflow(
            intent="experiment_design",
            title="Design an A/B test",
            description="Baseline, power analysis, sample size, minimum detectable effect, and duration.",
            examples=(
                "How long should we run a test on the checkout page?",
                "How many users do we need to detect a 5% lift in conversion?",
            ),
            params=(
                Param("metric", "Primary metric column for the test.", required=True),
                Param("mde", "Smallest effect worth detecting, e.g. '5% relative' or '0.01 absolute'."),
                Param("daily_traffic", "Units per day entering the test, if the user stated it."),
                Param("variants", "Number of arms including control (default 2)."),
                Param("date_column", "Date column to estimate daily traffic from the data."),
            ),
            steps=(
                StepTemplate(
                    "baseline",
                    "data",
                    "Locate '{metric}' and check it is usable as a test metric (type, missing values, "
                    "unit of analysis). Identify a date column for traffic: {date_column}.",
                ),
                StepTemplate(
                    "power",
                    "experiment",
                    "Measure the baseline of '{metric}' and daily traffic, then run a power analysis for "
                    "MDE {mde} with {variants} variants (daily traffic: {daily_traffic}). Show how the "
                    "detectable effect shrinks with longer durations.",
                    depends_on=("baseline",),
                ),
                StepTemplate("insights", "insight", INSIGHT_TASK, depends_on=("baseline", "power")),
            ),
        ),
        Workflow(
            intent="experiment_analysis",
            title="Analyze an A/B test",
            description="SRM, primary and guardrail metrics, CUPED, segments, Bayesian and sequential reads.",
            examples=(
                "Did variant B win?",
                "Did the new checkout increase conversion without hurting latency?",
            ),
            params=(
                Param("variant_column", "Column holding the assigned variant.", required=True),
                Param("control", "Control variant label as it appears in the data."),
                Param("primary_metric", "Primary success metric column.", required=True),
                Param("guardrail_metrics", "Comma-separated metrics that must not get worse."),
                Param("covariate", "Pre-experiment column for CUPED variance reduction."),
                Param("segments", "Comma-separated columns to break the effect down by."),
                Param("date_column", "Assignment date column, for sequential (peeking-safe) reads."),
            ),
            steps=(
                StepTemplate(
                    "validate",
                    "data",
                    "Validate the experiment data: the variant column '{variant_column}' and its labels "
                    "(control: {control}), one row per unit, and usable metric columns "
                    "({primary_metric}; guardrails: {guardrail_metrics}; covariate: {covariate}).",
                ),
                StepTemplate(
                    "analyze",
                    "experiment",
                    "Analyze the test. Check sample ratio first. Test the primary metric '{primary_metric}' "
                    "(with CUPED on {covariate} when available), each guardrail ({guardrail_metrics}), "
                    "effects by segment ({segments}), the Bayesian probability that treatment wins, and an "
                    "always-valid sequential read over {date_column}.",
                    depends_on=("validate",),
                ),
                StepTemplate("insights", "insight", INSIGHT_TASK, depends_on=("validate", "analyze")),
            ),
        ),
        Workflow(
            intent="estimate_effect",
            title="Estimate an effect from observational data",
            description="Regression, difference-in-differences, or propensity matching.",
            examples=("Did the loyalty program increase spend?",),
            available=False,
            phase="Phase 2",
        ),
        Workflow(
            intent="predict",
            title="Build a predictive model",
            description="Train, evaluate, and explain a classification or regression model.",
            examples=("Predict which customers will churn.",),
            available=False,
            phase="Phase 2",
        ),
        Workflow(
            intent="forecast",
            title="Forecast a time series",
            description="Forecast a metric with prediction intervals.",
            examples=("Forecast next month's orders.",),
            available=False,
            phase="Phase 2",
        ),
        Workflow(
            intent="model_data",
            title="Design an analytics data model",
            description="Propose a star schema with transforms, tests, and lineage.",
            examples=("Turn these raw tables into an analytics model.",),
            available=False,
            phase="Phase 4",
        ),
    ]
}


class _Blank(dict[str, str]):
    def __missing__(self, key: str) -> str:
        return "not specified"


def instantiate(intent: str, params: dict[str, str], skip: list[str] | None = None) -> dict[str, Any]:
    """Expand a workflow template into a concrete plan (a small DAG)."""
    workflow = WORKFLOWS[intent]
    skip_set = {k for k in (skip or []) if any(s.key == k and s.optional for s in workflow.steps)}
    values = _Blank({k: v for k, v in params.items() if v})
    steps = []
    for st in workflow.steps:
        if st.key in skip_set:
            continue
        steps.append(
            {
                "key": st.key,
                "agent": st.agent,
                "task": st.task.format_map(values),
                "depends_on": [d for d in st.depends_on if d not in skip_set],
            }
        )
    return {"intent": intent, "title": workflow.title, "params": dict(params), "steps": steps}


@dataclass
class IntentCatalog:
    workflows: list[Workflow] = field(default_factory=lambda: list(WORKFLOWS.values()))

    def describe(self) -> str:
        lines = []
        for w in self.workflows:
            status = "available" if w.available else f"not yet available ({w.phase})"
            params = (
                "; ".join(f"{p.name}{' (required)' if p.required else ''}: {p.description}" for p in w.params)
                or "none"
            )
            lines.append(
                f"- {w.intent} [{status}]: {w.description} Examples: "
                f"{' / '.join(w.examples)}. Params: {params}"
            )
        return "\n".join(lines)
