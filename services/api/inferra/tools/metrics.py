"""Governed metrics: compute, watch for anomalies, and decompose changes.

A metric is a structured aggregate (sum, count, count_distinct, average, or ratio of two parts),
not free SQL. That makes it reusable across runs and lets a ratio's change be split exactly into a
mix effect (segments' weights moved) and a rate effect (segments' rates moved)."""

import math
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, Field, model_validator
from scipy import stats

from inferra.datasources.base import QueryError
from inferra.tools.base import ArtifactDraft, ToolContext, ToolError, ToolOutput, round_floats, tool
from inferra.tools.experiment import _column, metric_sql

Grain = Literal["day", "week", "month"]


class MetricPart(BaseModel):
    kind: Literal["sum", "count", "count_distinct"]
    column: str | None = None


class MetricSpec(BaseModel):
    """A metric definition, either from the workspace's metric layer or given inline."""

    name: str = "metric"
    label: str | None = None
    table: str
    kind: Literal["sum", "count", "count_distinct", "average", "ratio"]
    column: str | None = None
    numerator: MetricPart | None = None
    denominator: MetricPart | None = None
    filter: str | None = Field(default=None, description="SQL condition applied to every computation.")
    time_column: str | None = None
    format: Literal["number", "percent", "currency"] = "number"
    description: str | None = None

    @model_validator(mode="after")
    def _check(self) -> "MetricSpec":
        if self.kind == "ratio" and (self.numerator is None or self.denominator is None):
            raise ValueError("A ratio metric needs a numerator and a denominator.")
        if self.kind in {"sum", "count_distinct", "average"} and not self.column:
            raise ValueError(f"A {self.kind} metric needs a column.")
        return self

    @property
    def is_ratio(self) -> bool:
        """Averages decompose like ratios (sum over count)."""
        return self.kind in {"ratio", "average"}


def _part(ctx: ToolContext, table: str, part: MetricPart) -> str:
    q = ctx.source.quote
    if part.kind == "count":
        return f"count({q(part.column)})" if part.column else "count(*)"
    if not part.column:
        raise ToolError(f"A {part.kind} part needs a column.")
    if part.kind == "count_distinct":
        _column(ctx.source, table, part.column)
        return f"count(DISTINCT {q(part.column)})"
    return f"sum({metric_sql(ctx.source, table, part.column)})"


def aggregates(ctx: ToolContext, m: MetricSpec) -> dict[str, str]:
    """SQL aggregate expressions: always 'value'; ratios also expose 'numerator' and 'denominator'."""
    if m.kind == "ratio":
        num = _part(ctx, m.table, m.numerator)  # type: ignore[arg-type]
        den = _part(ctx, m.table, m.denominator)  # type: ignore[arg-type]
    elif m.kind == "average":
        num = _part(ctx, m.table, MetricPart(kind="sum", column=m.column))
        den = f"count({metric_sql(ctx.source, m.table, m.column or '')})"
    else:
        value = _part(ctx, m.table, MetricPart(kind=m.kind, column=m.column))  # type: ignore[arg-type]
        return {"value": value}
    return {
        "numerator": f"CAST({num} AS DOUBLE)",
        "denominator": f"CAST({den} AS DOUBLE)",
        "value": f"CAST({num} AS DOUBLE) / NULLIF({den}, 0)",
    }


class MetricRef(BaseModel):
    metric: str | None = Field(default=None, description="Name of a defined metric (see list_metrics).")
    spec: MetricSpec | None = Field(default=None, description="An inline metric when none is defined.")

    def resolve(self, ctx: ToolContext) -> MetricSpec:
        if self.spec is not None:
            return self.spec
        if self.metric and self.metric in ctx.metrics:
            return MetricSpec.model_validate(ctx.metrics[self.metric])
        known = ", ".join(sorted(ctx.metrics)) or "none defined"
        raise ToolError(f"Unknown metric '{self.metric}'. Defined metrics: {known}. Or pass an inline spec.")


def _time_filter(ctx: ToolContext, m: MetricSpec, start: str | None, end: str | None) -> list[str]:
    if not (start or end):
        return []
    if not m.time_column:
        raise ToolError("This metric has no time column, so it cannot be filtered by date.")
    t = f"CAST({ctx.source.quote(m.time_column)} AS DATE)"
    out = []
    if start:
        out.append(f"{t} >= DATE '{_date(start)}'")
    if end:
        out.append(f"{t} <= DATE '{_date(end)}'")
    return out


def _date(value: str) -> str:
    import datetime as dt

    try:
        return dt.date.fromisoformat(value[:10]).isoformat()
    except ValueError as exc:
        raise ToolError(f"'{value}' is not a date (use YYYY-MM-DD).") from exc


def _conditions(m: MetricSpec, *extra: str | None) -> str:
    parts = [p for p in (m.filter, *extra) if p]
    return f"WHERE {' AND '.join(f'({p})' for p in parts)}" if parts else ""


def run_metric(
    ctx: ToolContext,
    m: MetricSpec,
    group_by: list[str] | None = None,
    grain: str | None = None,
    conditions: list[str] | None = None,
    limit: int = 500,
) -> tuple[list[dict[str, Any]], str]:
    q = ctx.source.quote
    group_by = group_by or []
    for g in group_by:
        _column(ctx.source, m.table, g)
    keys = []
    if grain:
        if not m.time_column:
            raise ToolError("This metric has no time column.")
        keys.append(f"date_trunc('{grain}', CAST({q(m.time_column)} AS DATE))::DATE::VARCHAR AS period")
    keys += [f"CAST({q(g)} AS VARCHAR) AS {q(g)}" for g in group_by]
    aggs = aggregates(ctx, m)
    select = keys + [f"{expr} AS {name}" for name, expr in aggs.items()] + ["count(*) AS rows"]
    sql = f"SELECT {', '.join(select)} FROM {q(m.table)} {_conditions(m, *(conditions or []))}"
    if keys:
        positions = ", ".join(str(i + 1) for i in range(len(keys)))
        sql += f" GROUP BY {positions} ORDER BY {positions}"
    sql += f" LIMIT {limit}"
    try:
        return ctx.source.query(sql).rows, sql
    except QueryError as exc:
        raise ToolError(str(exc)) from exc


def _describe(m: MetricSpec) -> dict[str, Any]:
    return {"name": m.name, "label": m.label or m.name, "kind": m.kind, "format": m.format, "table": m.table}


# --------------------------------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------------------------------


class ListMetricsInput(BaseModel):
    pass


@tool(
    "list_metrics",
    "List the workspace's defined metrics with their definitions, tables, time columns, and suggested "
    "dimensions. Prefer these over writing your own aggregation.",
    ListMetricsInput,
)
def list_metrics(ctx: ToolContext, _args: ListMetricsInput) -> ToolOutput:
    return ToolOutput(result={"metrics": list(ctx.metrics.values())})


class ComputeInput(MetricRef):
    group_by: list[str] = Field(default_factory=list, max_length=3)
    time_grain: Grain | None = None
    start: str | None = Field(default=None, description="First date, inclusive (YYYY-MM-DD).")
    end: str | None = Field(default=None, description="Last date, inclusive (YYYY-MM-DD).")
    where: str | None = None
    title: str | None = None


@tool(
    "compute_metric",
    "Compute a metric overall, by up to three dimensions, and/or over time (day, week, month), with an "
    "optional date range and filter. Returns the metric value plus numerator and denominator for "
    "ratios. Creates a 'metric' artifact.",
    ComputeInput,
)
def compute_metric(ctx: ToolContext, args: ComputeInput) -> ToolOutput:
    m = args.resolve(ctx)
    conds = [*_time_filter(ctx, m, args.start, args.end), *([args.where] if args.where else [])]
    rows, sql = run_metric(ctx, m, args.group_by, args.time_grain, conds)
    total_rows, _ = run_metric(ctx, m, conditions=conds)
    data = round_floats(
        {
            "design": "descriptive",
            "metric": _describe(m),
            "group_by": args.group_by,
            "time_grain": args.time_grain,
            "start": args.start,
            "end": args.end,
            "total": total_rows[0],
            "rows": rows,
            "row_count": len(rows),
            "sql": sql,
        }
    )
    title = args.title or f"{m.label or m.name}" + (
        f" by {', '.join(args.group_by)}" if args.group_by else ""
    ) + (f" per {args.time_grain}" if args.time_grain else "")
    return ToolOutput(
        result={**data, "rows": data["rows"][:60]}, artifact=ArtifactDraft("metric", title, data), code=sql
    )


class AnomalyInput(MetricRef):
    grain: Grain = "day"
    window: int = Field(default=28, ge=7, le=120, description="Trailing periods used as the baseline.")
    threshold: float = Field(
        default=3.0, ge=2.0, le=6.0, description="Robust z-score that counts as an anomaly."
    )
    start: str | None = None
    end: str | None = None
    where: str | None = None


@tool(
    "detect_anomalies",
    "Scan a metric's time series for anomalies (robust z-score against a trailing median and MAD) and find "
    "the single most likely level shift (change point) with before and after means. Use it to find when "
    "a metric changed. Creates an 'anomalies' artifact.",
    AnomalyInput,
)
def detect_anomalies(ctx: ToolContext, args: AnomalyInput) -> ToolOutput:
    m = args.resolve(ctx)
    conds = [*_time_filter(ctx, m, args.start, args.end), *([args.where] if args.where else [])]
    rows, sql = run_metric(ctx, m, grain=args.grain, conditions=conds, limit=2000)
    values = np.array([r["value"] if r["value"] is not None else np.nan for r in rows], dtype=float)
    if len(values) < args.window + 3:
        raise ToolError(
            f"Need at least {args.window + 3} periods; found {len(values)}. Use a smaller window."
        )
    series = []
    for i, r in enumerate(rows):
        point: dict[str, Any] = {"period": r["period"], "value": r["value"]}
        if i >= args.window:
            hist = values[i - args.window : i]
            hist = hist[~np.isnan(hist)]
            med = float(np.median(hist))
            mad = float(np.median(np.abs(hist - med))) * 1.4826
            z = (values[i] - med) / mad if mad > 0 else 0.0
            point.update(expected=med, z=z, anomaly=bool(abs(z) >= args.threshold))
        series.append(point)
    # Change point: the split that best separates two means (least squares).
    clean = np.nan_to_num(values, nan=float(np.nanmean(values)))
    best, best_cost = None, math.inf
    for k in range(7, len(clean) - 7):
        cost = clean[:k].var() * k + clean[k:].var() * (len(clean) - k)
        if cost < best_cost:
            best, best_cost = k, cost
    change = None
    if best is not None:
        before, after = clean[:best], clean[best:]
        t = stats.ttest_ind(after, before, equal_var=False)
        change = {
            "period": rows[best]["period"],
            "before_mean": float(before.mean()),
            "after_mean": float(after.mean()),
            "relative_change": float(after.mean() / before.mean() - 1) if before.mean() else None,
            "periods_before": len(before),
            "periods_after": len(after),
            "p_value": float(t.pvalue),
        }
    anomalies = [p for p in series if p.get("anomaly")]
    data = round_floats(
        {
            "design": "descriptive",
            "metric": _describe(m),
            "grain": args.grain,
            "window": args.window,
            "threshold": args.threshold,
            "anomaly_count": len(anomalies),
            "anomalies": anomalies[-20:],
            "change_point": change,
            "series": series,
        }
    )
    return ToolOutput(
        result={**data, "series": data["series"][-21:]},
        artifact=ArtifactDraft("anomalies", f"Anomalies in {m.label or m.name}", data),
        code=sql,
    )


class DecomposeInput(MetricRef):
    baseline_start: str
    baseline_end: str
    comparison_start: str
    comparison_end: str
    dimensions: list[str] = Field(min_length=1, max_length=5)
    where: str | None = None
    top_n: int = Field(default=8, ge=3, le=20)


def _segments(ctx: ToolContext, m: MetricSpec, dim: str, conds: list[str]) -> dict[str, dict[str, Any]]:
    rows, _ = run_metric(ctx, m, group_by=[dim], conditions=conds, limit=5000)
    return {str(r[dim]): r for r in rows}


@tool(
    "decompose_change",
    "Explain how a metric changed between a baseline and a comparison period by splitting the change "
    "across the segments of each dimension. Additive metrics split exactly into segment deltas; ratios "
    "and averages split exactly into a mix effect (segment weights moved) and a rate effect (segment "
    "rates moved). Ranks dimensions by how disproportionately one segment carries the change relative "
    "to its volume. Creates a 'decomposition' artifact.",
    DecomposeInput,
)
def decompose_change(ctx: ToolContext, args: DecomposeInput) -> ToolOutput:
    m = args.resolve(ctx)
    if m.kind == "count_distinct":
        raise ToolError("Distinct counts do not add up across segments; decompose a sum or ratio instead.")
    extra = [args.where] if args.where else []
    base_c = [*_time_filter(ctx, m, args.baseline_start, args.baseline_end), *extra]
    comp_c = [*_time_filter(ctx, m, args.comparison_start, args.comparison_end), *extra]
    base_total = run_metric(ctx, m, conditions=base_c)[0][0]
    comp_total = run_metric(ctx, m, conditions=comp_c)[0][0]
    if base_total["value"] is None or comp_total["value"] is None:
        raise ToolError("One of the periods has no data.")
    change = comp_total["value"] - base_total["value"]
    dims = []
    for dim in args.dimensions:
        a, b = _segments(ctx, m, dim, base_c), _segments(ctx, m, dim, comp_c)
        segs = []
        for key in sorted(set(a) | set(b)):
            ra, rb = a.get(key, {}), b.get(key, {})
            if m.is_ratio:
                den_a, den_b = ra.get("denominator") or 0.0, rb.get("denominator") or 0.0
                w_a = den_a / base_total["denominator"] if base_total["denominator"] else 0.0
                w_b = den_b / comp_total["denominator"] if comp_total["denominator"] else 0.0
                # Mix is measured against the overall baseline rate, so moving weight toward a
                # below-average segment shows as negative. A new segment starts from that baseline
                # rate; a vanished one keeps its rate. The pieces still sum exactly to the change.
                overall = base_total["value"]
                r_a = ra.get("value") if ra.get("value") is not None else overall
                r_b = rb.get("value") if rb.get("value") is not None else r_a
                mix, rate = (w_b - w_a) * (r_a - overall), w_b * (r_b - r_a)
                seg: dict[str, Any] = {
                    "segment": key,
                    "baseline_value": ra.get("value"),
                    "comparison_value": rb.get("value"),
                    "baseline_weight": w_a,
                    "comparison_weight": w_b,
                    "mix_effect": mix,
                    "rate_effect": rate,
                    "effect": mix + rate,
                }
                num_a, num_b = ra.get("numerator") or 0.0, rb.get("numerator") or 0.0
                if den_a > 0 and den_b > 0 and 0 <= num_a <= den_a and 0 <= num_b <= den_b:
                    p1, p2 = num_a / den_a, num_b / den_b
                    se = math.sqrt(p1 * (1 - p1) / den_a + p2 * (1 - p2) / den_b)
                    if se > 0:
                        seg["rate_change_ci_low"] = (p2 - p1) - 1.96 * se
                        seg["rate_change_ci_high"] = (p2 - p1) + 1.96 * se
                        seg["rate_change_p_value"] = float(2 * stats.norm.sf(abs((p2 - p1) / se)))
            else:
                va, vb = ra.get("value") or 0.0, rb.get("value") or 0.0
                seg = {
                    "segment": key,
                    "baseline_value": va,
                    "comparison_value": vb,
                    "comparison_weight": vb / comp_total["value"] if comp_total["value"] else 0.0,
                    "effect": vb - va,
                }
            seg["share_of_change"] = seg["effect"] / change if change else None
            segs.append(seg)
        segs.sort(key=lambda s: -abs(s["effect"]))
        # A dimension explains the change when one segment's share of the change far exceeds its
        # share of the volume (40% of traffic carrying 90% of the drop), not merely when it is big.
        for sg in segs:
            share = sg["share_of_change"] or 0.0
            sg["excess_share"] = share - sg.get("comparison_weight", 0.0)
        top = max(segs, key=lambda sg: sg["excess_share"]) if segs else None
        entry: dict[str, Any] = {
            "dimension": dim,
            "segment_count": len(segs),
            "top_segment": top["segment"] if top else None,
            "excess_share": top["excess_share"] if top else 0.0,
            "segments": segs[: args.top_n],
        }
        if m.is_ratio:
            entry["mix_effect"] = sum(s["mix_effect"] for s in segs)
            entry["rate_effect"] = sum(s["rate_effect"] for s in segs)
        dims.append(entry)
    dims.sort(key=lambda d: -d["excess_share"])
    data = round_floats(
        {
            "design": "descriptive",
            "metric": _describe(m),
            "method": "mix/rate decomposition" if m.is_ratio else "additive segment deltas",
            "baseline": {"start": args.baseline_start, "end": args.baseline_end, **base_total},
            "comparison": {"start": args.comparison_start, "end": args.comparison_end, **comp_total},
            "change": change,
            "relative_change": change / base_total["value"] if base_total["value"] else None,
            "dimensions": dims,
        }
    )
    return ToolOutput(
        result=data,
        artifact=ArtifactDraft("decomposition", f"What moved {m.label or m.name}", data),
        code=f"-- {data['method']} of {m.name} across {', '.join(args.dimensions)}",
    )


class CheckInput(MetricRef):
    grain: Literal["day", "week"] = Field(default="week", description="Check daily or weekly values.")
    baseline_periods: int = Field(
        default=8, ge=3, le=52, description="Complete periods before the latest one used as the baseline."
    )
    direction: Literal["down", "up", "both"] = Field(default="both", description="Which moves to alert on.")
    threshold: float = Field(
        default=0.1, gt=0, le=5, description="Relative change against the baseline that alerts (0.1 = 10%)."
    )
    where: str | None = None


def _period_end(start: str, grain: str) -> str:
    import datetime as dt

    day = dt.date.fromisoformat(start)
    return (day + dt.timedelta(days=6 if grain == "week" else 0)).isoformat()


@tool(
    "check_metric",
    "Monitor a metric: compare its latest complete day or week with the trailing baseline and decide "
    "whether to alert. The baseline is the median of the previous periods, with a normal range of three "
    "robust standard deviations (from the MAD), so a recent shift does not hide itself. It alerts when "
    "the change against the median exceeds the threshold in the watched direction AND falls outside the "
    "normal range. A partial latest period is left out. Creates a 'metric_check' artifact with status "
    "'alert' or 'ok'.",
    CheckInput,
)
def check_metric(ctx: ToolContext, args: CheckInput) -> ToolOutput:
    m = args.resolve(ctx)
    if not m.time_column:
        raise ToolError("This metric has no time column, so it cannot be monitored over time.")
    q = ctx.source.quote
    conds = [args.where] if args.where else []
    rows, sql = run_metric(ctx, m, grain=args.grain, conditions=conds, limit=2000)
    day = f"CAST({q(m.time_column)} AS DATE)"
    last_day_sql = f"SELECT max({day})::VARCHAR AS last_day FROM {q(m.table)} {_conditions(m, *conds)}"
    try:
        last_day = ctx.source.query(last_day_sql).rows[0]["last_day"]
    except QueryError as exc:
        raise ToolError(str(exc)) from exc
    rows = [r for r in rows if r["value"] is not None]
    excluded = None
    if rows and last_day and _period_end(str(rows[-1]["period"]), args.grain) > str(last_day):
        excluded = rows[-1]["period"]
        rows = rows[:-1]
    if len(rows) < args.baseline_periods + 1:
        raise ToolError(
            f"Need {args.baseline_periods + 1} complete {args.grain}s; found {len(rows)}. "
            "Use a smaller baseline_periods."
        )
    latest, history = rows[-1], rows[-1 - args.baseline_periods : -1]
    values = np.array([float(r["value"]) for r in history])
    # Median and MAD rather than mean and SD: if the shift began inside the baseline window, the
    # changed periods would otherwise pull the expectation toward them and widen the range.
    median = float(np.median(values))
    spread = float(np.median(np.abs(values - median))) * 1.4826 or float(values.std(ddof=1))
    low, high = median - 3 * spread, median + 3 * spread
    value = float(latest["value"])
    change = value - median
    relative = change / median if median else None
    z = change / spread if spread > 0 else None
    moved = relative is not None and (
        (args.direction in {"down", "both"} and relative <= -args.threshold)
        or (args.direction in {"up", "both"} and relative >= args.threshold)
    )
    outside = value < low or value > high
    alert = bool(moved and outside)
    label = m.label or m.name
    if alert:
        reason = (
            f"{label} moved {relative:+.1%} against its {len(values)}-{args.grain} median, beyond the "
            f"{args.threshold:.0%} threshold and outside the normal range."
        )
    elif moved:
        reason = f"{label} moved {relative:+.1%}, but stays within the baseline's normal range."
    else:
        reason = f"{label} is within {args.threshold:.0%} of its {len(values)}-{args.grain} median."
    data = round_floats(
        {
            "design": "descriptive",
            "metric": _describe(m),
            "grain": args.grain,
            "direction": args.direction,
            "threshold": args.threshold,
            "status": "alert" if alert else "ok",
            "alert": alert,
            "reason": reason,
            "latest": {**latest, "end": _period_end(str(latest["period"]), args.grain)},
            "baseline": {
                "start": history[0]["period"],
                "end": _period_end(str(history[-1]["period"]), args.grain),
                "periods": len(values),
                "median": median,
                "mean": float(values.mean()),
                "robust_std": spread,
                "low": low,
                "high": high,
            },
            "change": change,
            "relative_change": relative,
            "z_score": z,
            "excluded_partial_period": excluded,
            "series": [
                {"period": r["period"], "value": r["value"]} for r in rows[-(args.baseline_periods + 13) :]
            ],
        }
    )
    return ToolOutput(
        result={**data, "series": data["series"][-(args.baseline_periods + 1) :]},
        artifact=ArtifactDraft("metric_check", f"Check of {label} per {args.grain}", data),
        code=sql,
    )
