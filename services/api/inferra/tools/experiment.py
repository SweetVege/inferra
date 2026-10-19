"""Experimentation tools: power analysis, SRM, frequentist, CUPED, segment, Bayesian, and sequential
tests.

Every test runs on sufficient statistics (counts, means, variances, covariances) computed inside the
data source, so experiments with millions of units never leave the database as raw rows."""

import math
from dataclasses import dataclass
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, Field
from scipy import stats

from inferra.datasources.base import DataSource, QueryError
from inferra.tools.base import ArtifactDraft, ToolContext, ToolError, ToolOutput, round_floats, tool
from inferra.tools.profiling import _table, column_kind

SRM_THRESHOLD = 0.001
MetricType = Literal["auto", "binary", "continuous"]

# --------------------------------------------------------------------------------------------
# Statistics on summary data
# --------------------------------------------------------------------------------------------


@dataclass
class Arm:
    variant: str
    n: int
    mean: float
    var: float

    @property
    def se2(self) -> float:
        return self.var / self.n if self.n else math.inf


def holm(p_values: list[float]) -> list[float]:
    """Holm-Bonferroni adjusted p-values (step-down, monotone)."""
    m = len(p_values)
    order = sorted(range(m), key=lambda i: p_values[i])
    adjusted = [0.0] * m
    running = 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (m - rank) * p_values[i]))
        adjusted[i] = running
    return adjusted


def compare(control: Arm, treatment: Arm, metric_type: str, alpha: float) -> dict[str, Any]:
    """Treatment vs control: difference, relative lift, confidence intervals, and p-value.

    Binary metrics use a two-proportion z-test (pooled variance for the test, unpooled for the
    interval). Continuous metrics use Welch's t-test. The relative-lift interval uses the delta
    method."""
    if control.n < 2 or treatment.n < 2:
        raise ToolError(f"Not enough units to compare {treatment.variant} with {control.variant}.")
    diff = treatment.mean - control.mean
    se = math.sqrt(control.se2 + treatment.se2)
    if metric_type == "binary":
        pooled = (control.mean * control.n + treatment.mean * treatment.n) / (control.n + treatment.n)
        se_test = math.sqrt(pooled * (1 - pooled) * (1 / control.n + 1 / treatment.n))
        z = diff / se_test if se_test > 0 else 0.0
        p_value = 2 * stats.norm.sf(abs(z))
        crit = stats.norm.ppf(1 - alpha / 2)
        statistic, test = z, "two-proportion z-test"
    else:
        t = diff / se if se > 0 else 0.0
        num = (control.se2 + treatment.se2) ** 2
        den = control.se2**2 / (control.n - 1) + treatment.se2**2 / (treatment.n - 1)
        df = num / den if den > 0 else control.n + treatment.n - 2
        p_value = 2 * stats.t.sf(abs(t), df)
        crit = stats.t.ppf(1 - alpha / 2, df)
        statistic, test = t, "Welch's t-test"
    out: dict[str, Any] = {
        "control": control.variant,
        "treatment": treatment.variant,
        "n_control": control.n,
        "n_treatment": treatment.n,
        "mean_control": control.mean,
        "mean_treatment": treatment.mean,
        "diff": diff,
        "diff_ci_low": diff - crit * se,
        "diff_ci_high": diff + crit * se,
        "relative_lift": None,
        "relative_lift_ci_low": None,
        "relative_lift_ci_high": None,
        "statistic": statistic,
        "test": test,
        "p_value": p_value,
    }
    if control.mean != 0:
        lift = treatment.mean / control.mean - 1
        var_lift = treatment.se2 / control.mean**2 + treatment.mean**2 * control.se2 / control.mean**4
        out.update(
            relative_lift=lift,
            relative_lift_ci_low=lift - crit * math.sqrt(var_lift),
            relative_lift_ci_high=lift + crit * math.sqrt(var_lift),
        )
    return out


def srm(observed: dict[str, int], expected_shares: dict[str, float] | None = None) -> dict[str, Any]:
    variants = sorted(observed)
    total = sum(observed.values())
    if expected_shares:
        missing = [v for v in variants if v not in expected_shares]
        if missing:
            raise ToolError(f"Expected shares are missing for: {', '.join(missing)}.")
        norm = sum(expected_shares[v] for v in variants)
        shares = {v: expected_shares[v] / norm for v in variants}
    else:
        shares = {v: 1 / len(variants) for v in variants}
    expected = [shares[v] * total for v in variants]
    chi2, p_value = stats.chisquare([observed[v] for v in variants], expected)
    return {
        "observed": [
            {"variant": v, "count": observed[v], "share": observed[v] / total, "expected_share": shares[v]}
            for v in variants
        ],
        "total": total,
        "chi2": float(chi2),
        "p_value": float(p_value),
        "threshold": SRM_THRESHOLD,
        "srm_ok": bool(p_value >= SRM_THRESHOLD),
    }


def required_n(sigma2_c: float, sigma2_t: float, delta: float, alpha: float, power: float) -> int:
    z = stats.norm.ppf(1 - alpha / 2) + stats.norm.ppf(power)
    return math.ceil(z**2 * (sigma2_c + sigma2_t) / delta**2)


def msprt(control: Arm, treatment: Arm, tau: float) -> dict[str, float]:
    """Mixture SPRT (normal mixing distribution with variance tau^2) on the difference in means.
    The resulting p-value is valid at any stopping time, so the test can be checked continuously."""
    s2 = control.se2 + treatment.se2
    diff = treatment.mean - control.mean
    tau2 = tau**2
    log_lr = 0.5 * math.log(s2 / (s2 + tau2)) + tau2 * diff**2 / (2 * s2 * (s2 + tau2))
    p = 1.0 if log_lr <= 0 else min(1.0, math.exp(-log_lr))
    return {"diff": diff, "likelihood_ratio": math.exp(min(log_lr, 700.0)), "always_valid_p_value": p}


# --------------------------------------------------------------------------------------------
# Data access helpers
# --------------------------------------------------------------------------------------------


def _column(source: DataSource, table: str, name: str) -> dict[str, str]:
    info = _table(source, table)
    col = next((c for c in info.columns if c["name"] == name), None)
    if col is None:
        known = ", ".join(c["name"] for c in info.columns)
        raise ToolError(
            f"Unknown column '{name}' in {table}. Columns: {known}. To use a column from another table "
            "or a derived one, define a view with create_view and pass the view's name."
        )
    return col


def metric_sql(source: DataSource, table: str, column: str) -> str:
    """SQL expression that turns a metric column into a number (booleans become 0/1)."""
    col = _column(source, table, column)
    q = source.quote(column)
    kind = column_kind(col["type"])
    if col["type"].upper() == "BOOLEAN":
        return f"CAST({q} AS INTEGER)"
    if kind == "numeric":
        return f"CAST({q} AS DOUBLE)"
    if kind == "categorical":
        return (
            f"CASE lower(trim(CAST({q} AS VARCHAR))) WHEN 'true' THEN 1 WHEN 'yes' THEN 1 "
            f"WHEN 'false' THEN 0 WHEN 'no' THEN 0 ELSE TRY_CAST({q} AS DOUBLE) END"
        )
    raise ToolError(f"Column '{column}' ({col['type']}) cannot be used as a metric.")


def detect_type(source: DataSource, table: str, expr: str, requested: str) -> str:
    if requested != "auto":
        return requested
    row = source.query(
        f"SELECT count(*) FILTER (WHERE m NOT IN (0, 1)) AS other FROM "
        f"(SELECT {expr} AS m FROM {source.quote(table)}) WHERE m IS NOT NULL"
    ).rows[0]
    return "binary" if row["other"] == 0 else "continuous"


def _where(where: str | None, extra: str | None = None) -> str:
    parts = [p for p in (where, extra) if p]
    return f"WHERE {' AND '.join(f'({p})' for p in parts)}" if parts else ""


def arm_stats(
    source: DataSource,
    table: str,
    variant_col: str,
    expr: str,
    where: str | None = None,
    group_col: str | None = None,
) -> tuple[list[dict[str, Any]], str]:
    q = source.quote
    _column(source, table, variant_col)
    group = f"CAST({q(group_col)} AS VARCHAR) AS segment, " if group_col else ""
    group_by = "1, 2" if group_col else "1"
    sql = (
        f"SELECT {group}CAST({q(variant_col)} AS VARCHAR) AS variant, count(m) AS n, avg(m) AS mean, "
        f"coalesce(var_samp(m), 0) AS var FROM (SELECT *, {expr} AS m FROM {q(table)}) "
        f"{_where(where, f'{q(variant_col)} IS NOT NULL')} GROUP BY {group_by} ORDER BY {group_by}"
    )
    try:
        return source.query(sql).rows, sql
    except QueryError as exc:
        raise ToolError(str(exc)) from exc


def _arms(rows: list[dict[str, Any]]) -> dict[str, Arm]:
    return {
        r["variant"]: Arm(r["variant"], int(r["n"]), float(r["mean"] or 0), float(r["var"] or 0))
        for r in rows
    }


def _pick(arms: dict[str, Arm], control: str, treatment: str | None) -> tuple[Arm, list[Arm]]:
    if control not in arms:
        raise ToolError(f"Control variant '{control}' not found. Variants: {', '.join(sorted(arms))}.")
    if treatment is not None:
        if treatment not in arms:
            raise ToolError(
                f"Treatment variant '{treatment}' not found. Variants: {', '.join(sorted(arms))}."
            )
        return arms[control], [arms[treatment]]
    others = [a for v, a in sorted(arms.items()) if v != control]
    if not others:
        raise ToolError("Only one variant found; nothing to compare.")
    return arms[control], others


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def robust_compare(
    source: DataSource,
    table: str,
    variant_col: str,
    expr: str,
    where: str | None,
    control: str,
    treatment: str,
    alpha: float,
) -> dict[str, Any]:
    """Outlier-resistant comparison for a continuous metric, computed in the database.

    Mann-Whitney U (normal approximation with the tie correction) compares whole distributions;
    the 1%/99% winsorized means keep every unit but cap extreme values, so a handful of whales or
    bots cannot decide the result. Skewness says when these should be preferred over Welch."""
    q = source.quote
    arms = f"CAST({q(variant_col)} AS VARCHAR) IN ({_literal(control)}, {_literal(treatment)})"
    base = (
        f"SELECT CAST({q(variant_col)} AS VARCHAR) AS v, CAST(m AS DOUBLE) AS m "
        f"FROM (SELECT *, {expr} AS m FROM {q(table)}) {_where(where, f'{arms} AND m IS NOT NULL')}"
    )
    sql = (
        f"WITH base AS ({base}), "
        "bounds AS (SELECT quantile_cont(m, 0.01) AS lo, quantile_cont(m, 0.99) AS hi, "
        "skewness(m) AS skew FROM base), "
        "ranked AS (SELECT v, m, rank() OVER (ORDER BY m) + (count(*) OVER (PARTITION BY m) - 1) / 2.0 AS r "
        "FROM base) "
        "SELECT v, count(*) AS n, median(m) AS median, sum(r) AS rank_sum, "
        "avg(least(greatest(m, lo), hi)) AS w_mean, "
        "coalesce(var_samp(least(greatest(m, lo), hi)), 0) AS w_var, "
        "any_value(lo) AS lo, any_value(hi) AS hi, any_value(skew) AS skew "
        "FROM ranked, bounds GROUP BY v"
    )
    ties_sql = (
        f"WITH base AS ({base}) SELECT coalesce(sum(c * c * c - c), 0) AS t "
        "FROM (SELECT count(*)::DOUBLE AS c FROM base GROUP BY m)"
    )
    try:
        rows = {r["v"]: r for r in source.query(sql).rows}
        ties = float(source.query(ties_sql).rows[0]["t"])
    except QueryError as exc:
        raise ToolError(str(exc)) from exc
    if control not in rows or treatment not in rows:
        raise ToolError("Both arms need values to compare.")
    c, t = rows[control], rows[treatment]
    n1, n2 = int(c["n"]), int(t["n"])
    big_n = n1 + n2
    u_treatment = float(t["rank_sum"]) - n2 * (n2 + 1) / 2
    sigma = math.sqrt(n1 * n2 / 12 * ((big_n + 1) - ties / (big_n * (big_n - 1)))) if big_n > 1 else 0.0
    z = (u_treatment - n1 * n2 / 2) / sigma if sigma > 0 else 0.0
    skew = None if c["skew"] is None else float(c["skew"])
    winsorized = compare(
        Arm(control, n1, float(c["w_mean"]), float(c["w_var"])),
        Arm(treatment, n2, float(t["w_mean"]), float(t["w_var"])),
        "continuous",
        alpha,
    )
    return {
        "method": (
            "Mann-Whitney U (tie-corrected normal approximation); means winsorized at the 1st/99th percentile"
        ),
        "skewness": skew if skew is not None and math.isfinite(skew) else None,  # NaN for a constant metric
        "median_control": c["median"],
        "median_treatment": t["median"],
        "mann_whitney_p_value": float(2 * stats.norm.sf(abs(z))),
        "prob_treatment_higher": u_treatment / (n1 * n2),  # P(random treatment unit > control unit)
        "winsorize_low": c["lo"],
        "winsorize_high": c["hi"],
        "winsorized_mean_control": winsorized["mean_control"],
        "winsorized_mean_treatment": winsorized["mean_treatment"],
        "winsorized_diff": winsorized["diff"],
        "winsorized_diff_ci_low": winsorized["diff_ci_low"],
        "winsorized_diff_ci_high": winsorized["diff_ci_high"],
        "winsorized_p_value": winsorized["p_value"],
    }


def _assignment_counts(source: DataSource, table: str, variant_col: str, where: str | None) -> dict[str, int]:
    q = source.quote
    rows = source.query(
        f"SELECT CAST({q(variant_col)} AS VARCHAR) AS variant, count(*) AS n FROM {q(table)} "
        f"{_where(where, f'{q(variant_col)} IS NOT NULL')} GROUP BY 1"
    ).rows
    return {r["variant"]: int(r["n"]) for r in rows}


# --------------------------------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------------------------------


class BaselineInput(BaseModel):
    table: str
    metric_column: str
    metric_type: MetricType = "auto"
    date_column: str | None = Field(
        default=None, description="Date/timestamp column to estimate daily volume."
    )
    where: str | None = Field(default=None, description="Optional SQL filter, e.g. to exclude a past test.")


@tool(
    "metric_baseline",
    "Baseline for a metric before an experiment: mean (or rate), standard deviation, unit count, and "
    "average daily volume when a date column is given. Creates a 'baseline' artifact.",
    BaselineInput,
)
def metric_baseline(ctx: ToolContext, args: BaselineInput) -> ToolOutput:
    src, q = ctx.source, ctx.source.quote
    expr = metric_sql(src, args.table, args.metric_column)
    metric_type = detect_type(src, args.table, expr, args.metric_type)
    select = "count(m) AS n, avg(m) AS mean, coalesce(stddev_samp(m), 0) AS std"
    if args.date_column:
        _column(src, args.table, args.date_column)
        d = q(args.date_column)
        select += (
            f", count(DISTINCT CAST({d} AS DATE)) AS days, min(CAST({d} AS DATE))::VARCHAR AS first_day, "
            f"max(CAST({d} AS DATE))::VARCHAR AS last_day"
        )
    sql = f"SELECT {select} FROM (SELECT *, {expr} AS m FROM {q(args.table)}) {_where(args.where)}"
    try:
        row = src.query(sql).rows[0]
    except QueryError as exc:
        raise ToolError(str(exc)) from exc
    data: dict[str, Any] = {
        "table": args.table,
        "metric": args.metric_column,
        "metric_type": metric_type,
        **row,
    }
    if metric_type == "binary" and row["mean"] is not None:
        data["std"] = math.sqrt(row["mean"] * (1 - row["mean"]))
    if args.date_column and row.get("days"):
        data["daily_units"] = row["n"] / row["days"]
    data = round_floats(data)
    return ToolOutput(
        result=data,
        artifact=ArtifactDraft("baseline", f"Baseline for {args.metric_column}", data),
        code=sql,
    )


class PowerInput(BaseModel):
    metric_type: Literal["binary", "continuous"]
    baseline_mean: float = Field(description="Baseline rate (0-1) for binary metrics, or mean.")
    baseline_std: float | None = Field(default=None, description="Required for continuous metrics.")
    mde: float = Field(gt=0, description="Minimum detectable effect.")
    mde_type: Literal["relative", "absolute"] = "relative"
    alpha: float = Field(default=0.05, gt=0, lt=0.5)
    power: float = Field(default=0.8, ge=0.5, lt=1)
    variants: int = Field(default=2, ge=2, le=10, description="Number of arms including control.")
    daily_units: float | None = Field(default=None, gt=0, description="Units entering the test per day.")


@tool(
    "power_analysis",
    "Sample size and duration for an A/B test with a two-sided test and equal allocation, plus the "
    "smallest effect detectable after 1-6 weeks when daily traffic is known. Applies a Bonferroni "
    "correction when there are more than two arms. Creates a 'power' artifact.",
    PowerInput,
)
def power_analysis(ctx: ToolContext, args: PowerInput) -> ToolOutput:
    base = args.baseline_mean
    delta = args.mde * abs(base) if args.mde_type == "relative" else args.mde
    if delta == 0:
        raise ToolError("The minimum detectable effect resolves to zero.")
    alpha = args.alpha / (args.variants - 1)
    if args.metric_type == "binary":
        if not 0 < base < 1:
            raise ToolError("A binary baseline must be a rate strictly between 0 and 1.")
        treated = base + delta
        if not 0 < treated < 1:
            raise ToolError("Baseline plus the effect falls outside 0-1.")
        var_c, var_t = base * (1 - base), treated * (1 - treated)
    else:
        if not args.baseline_std or args.baseline_std <= 0:
            raise ToolError("Continuous metrics need a positive baseline_std.")
        var_c = var_t = args.baseline_std**2
    n = required_n(var_c, var_t, delta, alpha, args.power)
    data: dict[str, Any] = {
        "metric_type": args.metric_type,
        "baseline_mean": base,
        "baseline_std": math.sqrt(var_c),
        "mde_absolute": delta,
        "mde_relative": delta / base if base else None,
        "alpha": args.alpha,
        "alpha_per_comparison": alpha,
        "power": args.power,
        "variants": args.variants,
        "sample_size_per_variant": n,
        "total_sample_size": n * args.variants,
        "daily_units": args.daily_units,
        "estimated_days": math.ceil(n * args.variants / args.daily_units) if args.daily_units else None,
        "mde_by_duration": [],
    }
    if args.daily_units:
        z = stats.norm.ppf(1 - alpha / 2) + stats.norm.ppf(args.power)
        for weeks in range(1, 7):
            per_arm = args.daily_units * 7 * weeks / args.variants
            mde_abs = z * math.sqrt(2 * var_c / per_arm)
            data["mde_by_duration"].append(
                {
                    "days": 7 * weeks,
                    "sample_size_per_variant": int(per_arm),
                    "mde_absolute": mde_abs,
                    "mde_relative": mde_abs / base if base else None,
                }
            )
    data = round_floats(data)
    return ToolOutput(result=data, artifact=ArtifactDraft("power", "Sample size and duration", data))


class Share(BaseModel):
    variant: str
    share: float = Field(gt=0)


class SrmInput(BaseModel):
    table: str
    variant_column: str
    expected_shares: list[Share] | None = Field(default=None, description="Omit for an equal split.")
    where: str | None = None


@tool(
    "srm_check",
    "Sample ratio mismatch check: chi-square test of observed assignment counts against the intended "
    "split (equal by default). A p-value below 0.001 means the assignment is broken and results should "
    "not be trusted. Creates an 'srm' artifact.",
    SrmInput,
)
def srm_check(ctx: ToolContext, args: SrmInput) -> ToolOutput:
    counts = _assignment_counts(ctx.source, args.table, args.variant_column, args.where)
    if len(counts) < 2:
        raise ToolError("Found fewer than two variants.")
    expected = {s.variant: s.share for s in args.expected_shares} if args.expected_shares else None
    data = round_floats({"table": args.table, "variant_column": args.variant_column, **srm(counts, expected)})
    return ToolOutput(result=data, artifact=ArtifactDraft("srm", "Sample ratio check", data))


class AbTestInput(BaseModel):
    table: str
    variant_column: str
    control: str = Field(description="Control variant label, exactly as it appears in the data.")
    metric_column: str
    metric_type: MetricType = "auto"
    treatment: str | None = Field(
        default=None, description="Omit to compare every other variant with control."
    )
    alpha: float = Field(default=0.05, gt=0, lt=0.5)
    where: str | None = None
    expected_shares: list[Share] | None = None


def _experiment_data(
    args: Any,
    metric_type: str,
    arms: dict[str, Arm],
    comparisons: list[dict[str, Any]],
    srm_result: dict[str, Any],
) -> dict[str, Any]:
    return {
        "design": "randomized" if srm_result["srm_ok"] else "randomized_srm_failed",
        "table": args.table,
        "metric": args.metric_column,
        "metric_type": metric_type,
        "alpha": args.alpha,
        "srm_ok": srm_result["srm_ok"],
        "srm_p_value": srm_result["p_value"],
        "arms": [
            {"variant": a.variant, "n": a.n, "mean": a.mean, "std": math.sqrt(a.var)} for a in arms.values()
        ],
        "comparisons": comparisons,
    }


@tool(
    "ab_test",
    "Frequentist A/B test of one metric: per-arm means, absolute difference and relative lift with "
    "confidence intervals, and p-values (z-test for binary metrics, Welch's t-test otherwise; Holm "
    "correction across several treatments). For continuous metrics each comparison also has 'robust' "
    "results: medians, a Mann-Whitney U test, and 1%/99% winsorized means with an interval. When "
    "skewness is large (|skew| > 2, e.g. revenue or activity counts), rely on the robust results "
    "instead of dropping outliers with a hand-picked cutoff. Includes the SRM check. Creates an "
    "'ab_test' artifact.",
    AbTestInput,
)
def ab_test(ctx: ToolContext, args: AbTestInput) -> ToolOutput:
    src = ctx.source
    expr = metric_sql(src, args.table, args.metric_column)
    metric_type = detect_type(src, args.table, expr, args.metric_type)
    rows, sql = arm_stats(src, args.table, args.variant_column, expr, args.where)
    arms = _arms(rows)
    control, treatments = _pick(arms, args.control, args.treatment)
    comparisons = [compare(control, t, metric_type, args.alpha) for t in treatments]
    for c, p in zip(comparisons, holm([c["p_value"] for c in comparisons]), strict=True):
        c["p_value_adjusted"] = p
        c["significant"] = p < args.alpha
        if metric_type == "continuous":
            c["robust"] = robust_compare(
                src,
                args.table,
                args.variant_column,
                expr,
                args.where,
                control.variant,
                c["treatment"],
                args.alpha,
            )
    expected = {s.variant: s.share for s in args.expected_shares} if args.expected_shares else None
    srm_result = srm(_assignment_counts(src, args.table, args.variant_column, args.where), expected)
    data = round_floats(_experiment_data(args, metric_type, arms, comparisons, srm_result), 8)
    data["where"] = args.where
    title = f"A/B test: {args.metric_column}"
    if args.where:
        title += f" where {args.where if len(args.where) <= 60 else args.where[:57] + '...'}"
    return ToolOutput(result=data, artifact=ArtifactDraft("ab_test", title, data), code=sql)


class CupedInput(BaseModel):
    table: str
    variant_column: str
    control: str
    metric_column: str
    covariate_column: str = Field(description="Pre-experiment measure of the same or a related metric.")
    treatment: str | None = None
    alpha: float = Field(default=0.05, gt=0, lt=0.5)
    where: str | None = None


@tool(
    "cuped_test",
    "CUPED variance-reduced A/B test: adjusts the metric with a pre-experiment covariate "
    "(theta = cov(Y, X) / var(X), pooled across arms), then runs Welch's t-test on the adjusted "
    "metric. Reports the variance reduction and the unadjusted result for comparison. Creates a "
    "'cuped' artifact.",
    CupedInput,
)
def cuped_test(ctx: ToolContext, args: CupedInput) -> ToolOutput:
    src, q = ctx.source, ctx.source.quote
    y = metric_sql(src, args.table, args.metric_column)
    x = metric_sql(src, args.table, args.covariate_column)
    _column(src, args.table, args.variant_column)
    sql = (
        f"SELECT CAST({q(args.variant_column)} AS VARCHAR) AS variant, count(*) AS n, avg(y) AS mean_y, "
        f"avg(x) AS mean_x, coalesce(var_samp(y), 0) AS var_y, coalesce(var_samp(x), 0) AS var_x, "
        f"coalesce(covar_samp(y, x), 0) AS cov_xy FROM (SELECT *, {y} AS y, {x} AS x FROM {q(args.table)}) "
        f"{_where(args.where, f'y IS NOT NULL AND x IS NOT NULL AND {q(args.variant_column)} IS NOT NULL')} "
        f"GROUP BY 1 ORDER BY 1"
    )
    try:
        rows = src.query(sql).rows
    except QueryError as exc:
        raise ToolError(str(exc)) from exc
    total = sum(r["n"] for r in rows)
    if total < 4:
        raise ToolError("Not enough rows with both the metric and the covariate.")
    # Pooled within-arm (co)variances give theta; the overall covariate mean centers the adjustment.
    dof = sum(r["n"] - 1 for r in rows)
    var_x = sum((r["n"] - 1) * r["var_x"] for r in rows) / dof
    cov_xy = sum((r["n"] - 1) * r["cov_xy"] for r in rows) / dof
    if var_x == 0:
        raise ToolError("The covariate has no variance, so it cannot reduce variance.")
    theta = cov_xy / var_x
    mean_x_all = sum(r["n"] * r["mean_x"] for r in rows) / total
    adjusted, raw = {}, {}
    for r in rows:
        var_adj = max(r["var_y"] + theta**2 * r["var_x"] - 2 * theta * r["cov_xy"], 0.0)
        adjusted[r["variant"]] = Arm(
            r["variant"], r["n"], r["mean_y"] - theta * (r["mean_x"] - mean_x_all), var_adj
        )
        raw[r["variant"]] = Arm(r["variant"], r["n"], r["mean_y"], r["var_y"])
    control, treatments = _pick(adjusted, args.control, args.treatment)
    raw_control, _ = _pick(raw, args.control, args.treatment)
    comparisons, unadjusted = [], []
    for t in treatments:
        comparisons.append(compare(control, t, "continuous", args.alpha))
        unadjusted.append(compare(raw_control, raw[t.variant], "continuous", args.alpha))
    for c, p in zip(comparisons, holm([c["p_value"] for c in comparisons]), strict=True):
        c["p_value_adjusted"] = p
        c["significant"] = p < args.alpha
    var_raw = sum((r["n"] - 1) * r["var_y"] for r in rows) / dof
    var_adj = sum((a.n - 1) * a.var for a in adjusted.values()) / dof
    srm_result = srm({r["variant"]: int(r["n"]) for r in rows})
    data = _experiment_data(args, "continuous", adjusted, comparisons, srm_result)
    data.update(
        covariate=args.covariate_column,
        theta=theta,
        variance_reduction=1 - var_adj / var_raw if var_raw else 0.0,
        unadjusted=unadjusted,
    )
    data = round_floats(data, 8)
    return ToolOutput(
        result=data,
        artifact=ArtifactDraft("cuped", f"CUPED test: {args.metric_column}", data),
        code=sql,
    )


class SegmentInput(BaseModel):
    table: str
    variant_column: str
    control: str
    treatment: str
    metric_column: str
    segment_column: str
    metric_type: MetricType = "auto"
    alpha: float = Field(default=0.05, gt=0, lt=0.5)
    max_segments: int = Field(default=10, ge=2, le=25)
    min_units: int = Field(default=30, ge=2, description="Skip segments with fewer units per arm.")
    where: str | None = None


@tool(
    "segment_effects",
    "Treatment effect within each segment (for example device or country), with Holm-adjusted "
    "p-values across segments and a Cochran's Q test of whether the effect differs between segments. "
    "Creates a 'segment_effects' artifact.",
    SegmentInput,
)
def segment_effects(ctx: ToolContext, args: SegmentInput) -> ToolOutput:
    src = ctx.source
    expr = metric_sql(src, args.table, args.metric_column)
    metric_type = detect_type(src, args.table, expr, args.metric_type)
    _column(src, args.table, args.segment_column)
    rows, sql = arm_stats(
        src, args.table, args.variant_column, expr, args.where, group_col=args.segment_column
    )
    by_segment: dict[str, dict[str, Arm]] = {}
    for r in rows:
        seg = r["segment"] if r["segment"] is not None else "(missing)"
        by_segment.setdefault(seg, {})[r["variant"]] = Arm(
            r["variant"], int(r["n"]), float(r["mean"]), float(r["var"])
        )
    eligible = [
        (seg, arms)
        for seg, arms in by_segment.items()
        if args.control in arms
        and args.treatment in arms
        and min(arms[args.control].n, arms[args.treatment].n) >= args.min_units
    ]
    eligible.sort(key=lambda item: -(item[1][args.control].n + item[1][args.treatment].n))
    skipped = len(by_segment) - min(len(eligible), args.max_segments)
    eligible = eligible[: args.max_segments]
    if not eligible:
        raise ToolError("No segment has enough units in both arms.")
    segments = []
    for seg, arms in eligible:
        c = compare(arms[args.control], arms[args.treatment], metric_type, args.alpha)
        segments.append({"segment": seg, **c})
    for s, p in zip(segments, holm([s["p_value"] for s in segments]), strict=True):
        s["p_value_adjusted"] = p
        s["significant"] = p < args.alpha
    # Cochran's Q on the absolute differences.
    weights = [
        1 / ((s["diff_ci_high"] - s["diff_ci_low"]) / (2 * stats.norm.ppf(1 - args.alpha / 2))) ** 2
        for s in segments
    ]
    pooled = sum(w * s["diff"] for w, s in zip(weights, segments, strict=True)) / sum(weights)
    q_stat = sum(w * (s["diff"] - pooled) ** 2 for w, s in zip(weights, segments, strict=True))
    heterogeneity_p = float(stats.chi2.sf(q_stat, len(segments) - 1)) if len(segments) > 1 else 1.0
    data = round_floats(
        {
            "design": "randomized",
            "table": args.table,
            "metric": args.metric_column,
            "metric_type": metric_type,
            "segment_column": args.segment_column,
            "alpha": args.alpha,
            "correction": "holm",
            "segments": segments,
            "segments_skipped": skipped,
            "heterogeneity_q": q_stat,
            "heterogeneity_p_value": heterogeneity_p,
        },
        8,
    )
    return ToolOutput(
        result=data,
        artifact=ArtifactDraft(
            "segment_effects",
            f"Effect of {args.treatment} on {args.metric_column} by {args.segment_column}",
            data,
        ),
        code=sql,
    )


class BayesInput(BaseModel):
    table: str
    variant_column: str
    control: str
    treatment: str
    metric_column: str
    metric_type: MetricType = "auto"
    where: str | None = None


@tool(
    "bayesian_ab",
    "Bayesian A/B comparison: probability that treatment beats control, expected loss of shipping "
    "treatment, and a 95% credible interval for the relative lift (Beta-Binomial for binary metrics, "
    "normal approximation otherwise; 200k posterior draws, fixed seed). Creates a 'bayesian' artifact.",
    BayesInput,
)
def bayesian_ab(ctx: ToolContext, args: BayesInput) -> ToolOutput:
    src = ctx.source
    expr = metric_sql(src, args.table, args.metric_column)
    metric_type = detect_type(src, args.table, expr, args.metric_type)
    rows, sql = arm_stats(src, args.table, args.variant_column, expr, args.where)
    arms = _arms(rows)
    control, [treatment] = _pick(arms, args.control, args.treatment)
    rng = np.random.default_rng(0)
    draws = 200_000
    if metric_type == "binary":

        def posterior(a: Arm) -> np.ndarray:
            successes = round(a.mean * a.n)
            return rng.beta(1 + successes, 1 + a.n - successes, draws)

        prior = "Beta(1, 1)"
    else:

        def posterior(a: Arm) -> np.ndarray:
            return rng.normal(a.mean, math.sqrt(a.se2), draws)

        prior = "flat (normal approximation)"
    pc, pt = posterior(control), posterior(treatment)
    lift = pt / pc - 1 if np.all(pc != 0) else pt - pc
    data = round_floats(
        {
            "design": "randomized",
            "table": args.table,
            "metric": args.metric_column,
            "metric_type": metric_type,
            "prior": prior,
            "control": control.variant,
            "treatment": treatment.variant,
            "n_control": control.n,
            "n_treatment": treatment.n,
            "mean_control": control.mean,
            "mean_treatment": treatment.mean,
            "prob_treatment_better": float(np.mean(pt > pc)),
            "expected_loss_treatment": float(np.mean(np.maximum(pc - pt, 0))),
            "expected_loss_control": float(np.mean(np.maximum(pt - pc, 0))),
            "relative_lift_mean": float(np.mean(lift)),
            "relative_lift_ci_low": float(np.quantile(lift, 0.025)),
            "relative_lift_ci_high": float(np.quantile(lift, 0.975)),
            "draws": draws,
        },
        8,
    )
    return ToolOutput(
        result=data,
        artifact=ArtifactDraft("bayesian", f"Bayesian comparison: {args.metric_column}", data),
        code=sql,
    )


class SequentialInput(BaseModel):
    table: str
    variant_column: str
    control: str
    treatment: str
    metric_column: str
    date_column: str | None = Field(
        default=None, description="Assignment or event date, to show each daily look."
    )
    tau_relative: float = Field(
        default=0.05,
        gt=0,
        le=1,
        description="Mixing scale as a fraction of the control mean (the effect size you care about).",
    )
    alpha: float = Field(default=0.05, gt=0, lt=0.5)
    where: str | None = None


@tool(
    "sequential_test",
    "Always-valid sequential test (mixture SPRT) for peeking safely: an always-valid p-value at the "
    "latest data and, with a date column, at every daily look. Use it when results were checked "
    "repeatedly or the test may stop early. Creates a 'sequential' artifact.",
    SequentialInput,
)
def sequential_test(ctx: ToolContext, args: SequentialInput) -> ToolOutput:
    src, q = ctx.source, ctx.source.quote
    expr = metric_sql(src, args.table, args.metric_column)
    rows, sql = arm_stats(src, args.table, args.variant_column, expr, args.where)
    arms = _arms(rows)
    control, [treatment] = _pick(arms, args.control, args.treatment)
    tau = args.tau_relative * abs(control.mean) if control.mean else args.tau_relative
    final = msprt(control, treatment, tau)
    looks = []
    if args.date_column:
        _column(src, args.table, args.date_column)
        d, v = q(args.date_column), q(args.variant_column)
        daily_sql = (
            f"SELECT CAST({d} AS DATE)::VARCHAR AS day, CAST({v} AS VARCHAR) AS variant, count(m) AS n, "
            f"sum(m) AS s, sum(m * m) AS ss FROM (SELECT *, {expr} AS m FROM {q(args.table)}) "
            f"{_where(args.where, f'{v} IS NOT NULL AND m IS NOT NULL')} GROUP BY 1, 2 ORDER BY 1"
        )
        cum: dict[str, list[float]] = {}
        days: dict[str, dict[str, tuple[int, float, float]]] = {}
        for r in src.query(daily_sql).rows:
            days.setdefault(r["day"], {})[r["variant"]] = (r["n"], r["s"], r["ss"])
        for day in sorted(days):
            for variant, (n, s, ss) in days[day].items():
                acc = cum.setdefault(variant, [0, 0.0, 0.0])
                acc[0] += n
                acc[1] += s
                acc[2] += ss
            if control.variant in cum and treatment.variant in cum:
                pair = []
                for variant in (control.variant, treatment.variant):
                    n, s, ss = cum[variant]
                    mean = s / n
                    var = max((ss - n * mean**2) / (n - 1), 0.0) if n > 1 else 0.0
                    pair.append(Arm(variant, int(n), mean, var))
                if min(a.n for a in pair) > 1 and all(a.var > 0 for a in pair):
                    look = msprt(pair[0], pair[1], tau)
                    looks.append({"day": day, "n_control": pair[0].n, "n_treatment": pair[1].n, **look})
    first_significant = next((lk["day"] for lk in looks if lk["always_valid_p_value"] < args.alpha), None)
    data = round_floats(
        {
            "design": "randomized",
            "table": args.table,
            "metric": args.metric_column,
            "method": "mixture SPRT",
            "tau": tau,
            "alpha": args.alpha,
            "control": control.variant,
            "treatment": treatment.variant,
            "n_control": control.n,
            "n_treatment": treatment.n,
            **final,
            "significant": final["always_valid_p_value"] < args.alpha,
            "first_significant_day": first_significant,
            "looks": looks,
        },
        8,
    )
    return ToolOutput(
        result={**data, "looks": data["looks"][-10:]},
        artifact=ArtifactDraft("sequential", f"Sequential test: {args.metric_column}", data),
        code=sql,
    )
