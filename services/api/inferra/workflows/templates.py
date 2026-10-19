"""Workflow templates: the fixed shape of work for each kind of question.

The Orchestrator classifies a goal into one of these intents and fills in its parameters. It can
skip optional steps, but it cannot invent new ones. Intents marked unavailable are recognized so
Inferra can say clearly that a capability is on the roadmap instead of improvising."""

import re
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
    agent: str  # data | analyst | experiment | ml | modeler | insight
    task: str  # {param} placeholders; a [bracketed clause] is dropped unless all its params are set
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
                    "affect analysis.[ Focus: {focus}.]",
                ),
                StepTemplate(
                    "patterns",
                    "analyst",
                    "Find the most informative distributions, segment differences, and "
                    "relationships between columns.[ Focus: {focus}.]",
                    depends_on=("profile",),
                ),
                StepTemplate("insights", "insight", INSIGHT_TASK, depends_on=("profile", "patterns")),
            ),
        ),
        Workflow(
            intent="metric_query",
            title="Answer a metric question",
            description="Compute a specific number, breakdown, or trend that the user asks for.",
            examples=("Signup conversion rate by platform last month", "How many orders per week?"),
            params=(
                Param("metric", "A defined metric name, or the measure in plain words.", required=True),
                Param("breakdown", "Dimensions or time grain to break the metric down by."),
                Param("filters", "Any filters or time window the user mentioned."),
            ),
            steps=(
                StepTemplate(
                    "prepare",
                    "data",
                    "Find the defined metric or the tables and columns that express '{metric}', check "
                    "they are usable, and note any data issues that affect it.[ Filters: {filters}.]",
                ),
                StepTemplate(
                    "compute",
                    "analyst",
                    "Compute '{metric}'[ broken down by {breakdown}][, applying filters: {filters}]. Use "
                    "compute_metric for defined metrics and SQL otherwise.[ Report totals alongside the "
                    "{breakdown} breakdown.]",
                    depends_on=("prepare",),
                ),
                StepTemplate("insights", "insight", INSIGHT_TASK, depends_on=("prepare", "compute")),
            ),
        ),
        Workflow(
            intent="diagnose",
            title="Diagnose a metric change",
            description="Find when a metric moved and which segments account for the change.",
            examples=("Why did conversion drop last week?", "What is behind the revenue decline in October?"),
            params=(
                Param("metric", "A defined metric name, or the measure in plain words.", required=True),
                Param("comparison_period", "The period with the change, as start..end dates if known."),
                Param("baseline_period", "The period to compare against, as start..end dates if known."),
                Param("dimensions", "Comma-separated columns to break the change down by."),
            ),
            steps=(
                StepTemplate(
                    "check",
                    "data",
                    "Check whether '{metric}' changed because of the data itself: missing days, "
                    "volume drops that suggest broken tracking, or new values in the dimensions"
                    "[ ({dimensions})].",
                ),
                StepTemplate(
                    "diagnose",
                    "analyst",
                    "Find when '{metric}' changed (detect_anomalies), then decompose the change between "
                    "the baseline[ ({baseline_period})] and the comparison[ ({comparison_period})] across "
                    "the dimensions that best explain it[ ({dimensions})]. Drill into the top segment with "
                    "a filter to confirm where the change "
                    "sits, and separate mix effects from rate effects.",
                    depends_on=("check",),
                ),
                StepTemplate("insights", "insight", INSIGHT_TASK, depends_on=("check", "diagnose")),
            ),
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
                    "unit of analysis). Identify a date column for traffic[: {date_column}].",
                ),
                StepTemplate(
                    "power",
                    "experiment",
                    "Measure the baseline of '{metric}' and daily traffic[ (stated: {daily_traffic})], then "
                    "run a power analysis[ for an MDE of {mde}][ with {variants} variants]. Show how the "
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
                    "Validate the experiment data: the variant column '{variant_column}' and its labels"
                    "[ (control: {control})], one row per unit, and usable metric columns "
                    "({primary_metric}[; guardrails: {guardrail_metrics}][; covariate: {covariate}]).",
                ),
                StepTemplate(
                    "analyze",
                    "experiment",
                    "Analyze the test. Check sample ratio first. Test the primary metric '{primary_metric}'"
                    "[ (with CUPED on {covariate})][ and each guardrail ({guardrail_metrics})]."
                    "[ Break the effect down by {segments}.] Estimate the Bayesian probability that "
                    "treatment wins.[ Add an always-valid sequential read over {date_column}.]",
                    depends_on=("validate",),
                ),
                StepTemplate("insights", "insight", INSIGHT_TASK, depends_on=("validate", "analyze")),
            ),
        ),
        Workflow(
            intent="estimate_effect",
            title="Estimate an effect from observational data",
            description="Difference-in-differences, matching, or regression, with assumption checks.",
            examples=(
                "Did the loyalty program increase store sales?",
                "What is the effect of discounts on churn, controlling for plan and tenure?",
            ),
            params=(
                Param(
                    "treatment",
                    "The intervention or exposure column, or how treatment is defined.",
                    required=True,
                ),
                Param("outcome", "The outcome column.", required=True),
                Param(
                    "method",
                    "diff_in_diff when there is a panel with a known start date; propensity_match for a "
                    "one-time treatment with pre-treatment covariates; regression otherwise.",
                ),
                Param("unit_column", "Unit identifier for panel data."),
                Param("time_column", "Period column for panel data."),
                Param("post_start", "First treated period for difference-in-differences."),
                Param("covariates", "Comma-separated pre-treatment variables to control for."),
            ),
            steps=(
                StepTemplate(
                    "prepare",
                    "data",
                    "Check the data for estimating the effect of {treatment} on {outcome}: treatment and "
                    "outcome definitions, unit and time structure[ ({unit_column}, {time_column})], and "
                    "whether covariates[ ({covariates})] are measured before treatment.",
                ),
                StepTemplate(
                    "estimate",
                    "analyst",
                    "Estimate the effect of {treatment} on {outcome}[ using {method}]"
                    "[ (treatment starts {post_start})][, controlling for {covariates}]. Check the method's "
                    "assumptions (pre-trends "
                    "or covariate balance) and run a simpler cross-check.",
                    depends_on=("prepare",),
                ),
                StepTemplate("insights", "insight", INSIGHT_TASK, depends_on=("prepare", "estimate")),
            ),
        ),
        Workflow(
            intent="predict",
            title="Build a predictive model",
            description="Train, evaluate, explain, and register a classification or regression model.",
            examples=("Predict which customers will churn.", "Which customers are most likely to upgrade?"),
            params=(
                Param("target", "Column to predict.", required=True),
                Param("features", "Comma-separated columns to use; empty means all eligible columns."),
                Param("population", "Optional filter describing the rows to model."),
            ),
            steps=(
                StepTemplate(
                    "prepare",
                    "data",
                    "Check that '{target}' is a usable label (type, missing values, class balance) and flag "
                    "columns that could leak the outcome (recorded after it).[ Population: {population}.]",
                ),
                StepTemplate(
                    "train",
                    "ml",
                    "Train and evaluate a model for '{target}'[ using {features}][, restricted to "
                    "{population}]. Compare against the baseline and explain the main drivers.",
                    depends_on=("prepare",),
                ),
                StepTemplate("insights", "insight", INSIGHT_TASK, depends_on=("prepare", "train")),
            ),
        ),
        Workflow(
            intent="forecast",
            title="Forecast a time series",
            description="Forecast a metric with prediction intervals and a backtest against a naive model.",
            examples=("Forecast daily orders for the next four weeks.", "What will revenue be next quarter?"),
            params=(
                Param("metric", "Column to forecast, or 'rows' to count records.", required=True),
                Param("date_column", "Date column.", required=True),
                Param("freq", "day, week, or month."),
                Param("horizon", "Number of periods ahead."),
            ),
            steps=(
                StepTemplate(
                    "prepare",
                    "data",
                    "Check the history of '{metric}' over '{date_column}': coverage, gaps, partial "
                    "periods, and outliers that could distort a forecast.",
                ),
                StepTemplate(
                    "forecast",
                    "analyst",
                    "Forecast '{metric}'[ by {freq}][ for {horizon} periods] with prediction intervals, and "
                    "report how the model did in the backtest compared with the seasonal-naive baseline.",
                    depends_on=("prepare",),
                ),
                StepTemplate("insights", "insight", INSIGHT_TASK, depends_on=("prepare", "forecast")),
            ),
        ),
        Workflow(
            intent="model_data",
            title="Design an analytics data model",
            description="Propose a star schema (facts and dimensions) with SQL transforms, tests, and "
            "lineage, then build and test it. The result can be published as datasets or exported to dbt.",
            examples=(
                "Turn the order lines export into a star schema.",
                "Model these raw tables for analytics.",
            ),
            params=(
                Param("subject", "The business process to model, e.g. 'order lines' or 'subscriptions'."),
                Param("requirements", "Anything the user asked for: names, dimensions, rules to test."),
            ),
            steps=(
                StepTemplate(
                    "inspect",
                    "data",
                    "Inspect the source tables[ for modeling {subject}]: profile them, find each table's "
                    "grain with analyze_keys, and when there are several tables, how they join. Flag "
                    "duplicates, missing keys, and attributes that are not stable for their entity.",
                ),
                StepTemplate(
                    "model",
                    "modeler",
                    "Design a star schema[ for {subject}][ (requirements: {requirements})]. Build it with "
                    "build_data_model, fix any node that fails, and report the grain, the dimensions, "
                    "and the test results, including what the failing rows have in common.",
                    depends_on=("inspect",),
                ),
                StepTemplate("insights", "insight", INSIGHT_TASK, depends_on=("inspect", "model")),
            ),
        ),
        Workflow(
            intent="monitor",
            title="Monitor a metric",
            description="Check a metric against its recent baseline and alert when it moves beyond a "
            "threshold. Once the plan runs, Inferra re-runs it daily or weekly and raises alerts in the app.",
            examples=(
                "Alert me when conversion drops.",
                "Check revenue every Monday and tell me if it is off.",
            ),
            params=(
                Param("metric", "A defined metric name, or the measure in plain words.", required=True),
                Param("cadence", "daily or weekly (default weekly): how often to check and what period."),
                Param("direction", "down, up, or both (default both): which moves should alert."),
                Param("threshold", "Relative change that alerts, as a fraction (0.1 = 10%). Default 0.1."),
                Param("dimensions", "Comma-separated columns to break an alerting change down by."),
            ),
            steps=(
                StepTemplate(
                    "check",
                    "data",
                    "Confirm '{metric}' is defined or computable, and check that the latest[ {cadence}] "
                    "period is complete and that volumes look normal, since broken tracking would look "
                    "like a metric move.",
                ),
                StepTemplate(
                    "watch",
                    "analyst",
                    "Run check_metric for '{metric}'[ per {cadence} period] against its trailing baseline"
                    "[ (alert direction: {direction})][, alerting beyond a relative change of {threshold}]. "
                    "If it alerts, decompose the change between the baseline and the latest period"
                    "[ across {dimensions}].",
                    depends_on=("check",),
                ),
                StepTemplate("insights", "insight", INSIGHT_TASK, depends_on=("check", "watch")),
            ),
        ),
    ]
}


_OPTIONAL = re.compile(r"\[([^\[\]]*)\]")
_PLACEHOLDER = re.compile(r"\{(\w+)\}")
_EMPTY = {"", "none", "n/a", "na", "null", "not specified", "unspecified", "unknown"}


def fill(task: str, values: dict[str, str]) -> str:
    """Fill a step's task. Optional clauses whose params are missing drop out entirely, so a plan
    never reads "with CUPED on not specified"."""

    def clause(match: re.Match[str]) -> str:
        inner = match.group(1)
        return inner if all(values.get(k) for k in _PLACEHOLDER.findall(inner)) else ""

    text = _OPTIONAL.sub(clause, task)
    text = _PLACEHOLDER.sub(lambda m: values.get(m.group(1)) or "not specified", text)
    return re.sub(r" {2,}", " ", text).strip()


def instantiate(intent: str, params: dict[str, str], skip: list[str] | None = None) -> dict[str, Any]:
    """Expand a workflow template into a concrete plan (a small DAG)."""
    workflow = WORKFLOWS[intent]
    skip_set = {k for k in (skip or []) if any(s.key == k and s.optional for s in workflow.steps)}
    values = {k: str(v).strip() for k, v in params.items() if str(v or "").strip().lower() not in _EMPTY}
    steps = []
    for st in workflow.steps:
        if st.key in skip_set:
            continue
        steps.append(
            {
                "key": st.key,
                "agent": st.agent,
                "task": fill(st.task, values),
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
