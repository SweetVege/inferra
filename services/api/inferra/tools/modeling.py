"""Statistical modeling tools: regression, difference-in-differences, propensity matching, forecasting.

These estimate relationships and effects from observational data. Every result states the design it
rests on, so the verifier can tell associations from causal estimates and check the assumptions."""

import math
import warnings
from typing import Any, Literal

import numpy as np
import pandas as pd
import statsmodels.api as sm
from pydantic import BaseModel, Field
from scipy import stats

from inferra.datasources.base import DataSource, QueryError
from inferra.tools.base import ArtifactDraft, ToolContext, ToolError, ToolOutput, round_floats, tool
from inferra.tools.experiment import _column, _where
from inferra.tools.profiling import column_kind

MAX_ROWS = 200_000
MAX_LEVELS = 20


# --------------------------------------------------------------------------------------------
# Data helpers
# --------------------------------------------------------------------------------------------


def fetch(
    source: DataSource, table: str, columns: list[str], where: str | None = None, max_rows: int = MAX_ROWS
) -> tuple[pd.DataFrame, str, bool]:
    """Pull the named columns, sampling reproducibly when the table is larger than max_rows."""
    for c in columns:
        _column(source, table, c)
    q = source.quote
    cols = ", ".join(q(c) for c in dict.fromkeys(columns))
    total = source.query(f"SELECT count(*) AS n FROM {q(table)} {_where(where)}").rows[0]["n"]
    sampled = total > max_rows
    sample = f" USING SAMPLE reservoir({max_rows} ROWS) REPEATABLE (42)" if sampled else ""
    sql = f"SELECT {cols} FROM {q(table)} {_where(where)}{sample}"
    try:
        return source.query_frame(sql), sql, sampled
    except QueryError as exc:
        raise ToolError(str(exc)) from exc


def to_numeric(series: pd.Series) -> pd.Series:
    """A float64 series. Booleans and 'true'/'false' strings become 0/1, dates become days since
    1970 and durations become days; anything else is coerced to a number. Nullable columns (which
    DuckDB returns for joins with missing values) become plain floats with NaN."""
    dtype = series.dtype
    if pd.api.types.is_bool_dtype(dtype):
        return series.astype("float64")
    if pd.api.types.is_datetime64_any_dtype(dtype):
        return (series - pd.Timestamp(0, tz=getattr(dtype, "tz", None))).dt.total_seconds() / 86400
    if pd.api.types.is_timedelta64_dtype(dtype):
        return series.dt.total_seconds() / 86400
    if pd.api.types.is_object_dtype(dtype) or pd.api.types.is_string_dtype(dtype):
        lowered = series.astype("string").str.strip().str.lower()
        mapped = lowered.map({"true": 1.0, "false": 0.0, "yes": 1.0, "no": 0.0})
        if mapped.notna().sum() >= series.notna().sum() * 0.99:
            return mapped.astype("float64")
    return pd.to_numeric(series, errors="coerce").astype("float64")


def is_categorical(source: DataSource, table: str, column: str, series: pd.Series) -> bool:
    kind = column_kind(_column(source, table, column)["type"])
    if kind != "categorical":
        return False
    return to_numeric(series).notna().sum() < series.notna().sum() * 0.99


def design_matrix(
    source: DataSource, table: str, frame: pd.DataFrame, features: list[str]
) -> tuple[pd.DataFrame, list[str]]:
    """Numeric columns as-is; categoricals one-hot encoded against their most common level."""
    parts, notes = [], []
    for f in features:
        s = frame[f]
        if is_categorical(source, table, f, s):
            counts = s.astype("string").fillna("(missing)").value_counts()
            keep = list(counts.index[:MAX_LEVELS])
            if len(counts) > MAX_LEVELS:
                notes.append(f"{f}: {len(counts) - MAX_LEVELS} rare levels grouped as 'other'.")
            filled = s.astype("string").fillna("(missing)")
            values = filled.where(filled.isin(keep), "other")
            dummies = pd.get_dummies(values, prefix=f, prefix_sep="=", dtype=float)
            reference = f"{f}={keep[0]}"
            notes.append(f"{f}: reference level is '{keep[0]}'.")
            parts.append(dummies.drop(columns=[reference], errors="ignore"))
        else:
            parts.append(to_numeric(s).rename(f).to_frame())
    return pd.concat(parts, axis=1), notes


def _ci(estimate: float, se: float, alpha: float = 0.05, df: float | None = None) -> tuple[float, float]:
    crit = stats.t.ppf(1 - alpha / 2, df) if df else stats.norm.ppf(1 - alpha / 2)
    return estimate - crit * se, estimate + crit * se


# --------------------------------------------------------------------------------------------
# Regression
# --------------------------------------------------------------------------------------------


class RegressionInput(BaseModel):
    table: str
    target: str
    features: list[str] = Field(min_length=1, max_length=25)
    model: Literal["auto", "linear", "logistic"] = "auto"
    where: str | None = None


@tool(
    "fit_regression",
    "Linear (OLS) or logistic regression with heteroskedasticity-robust (HC3) standard errors. "
    "Categorical features are one-hot encoded. Reports coefficients with 95% CIs and p-values (odds "
    "ratios for logistic), fit statistics, and variance inflation factors. Coefficients describe "
    "associations, not causal effects. Creates a 'regression' artifact.",
    RegressionInput,
)
def fit_regression(ctx: ToolContext, args: RegressionInput) -> ToolOutput:
    src = ctx.source
    if args.target in args.features:
        raise ToolError("The target cannot also be a feature.")
    frame, sql, sampled = fetch(src, args.table, [args.target, *args.features], args.where)
    y = to_numeric(frame[args.target])
    X, notes = design_matrix(src, args.table, frame, args.features)
    data = pd.concat([y.rename("__y"), X], axis=1).dropna()
    dropped = len(frame) - len(data)
    if len(data) < len(X.columns) + 10:
        raise ToolError("Too few complete rows for this many features.")
    binary = set(data["__y"].unique()) <= {0.0, 1.0}
    kind = args.model if args.model != "auto" else ("logistic" if binary else "linear")
    if kind == "logistic" and not binary:
        raise ToolError("Logistic regression needs a 0/1 target.")
    exog = sm.add_constant(data.drop(columns="__y"), has_constant="add")
    constant = [c for c in exog.columns if exog[c].std() == 0 and c != "const"]
    if constant:
        exog = exog.drop(columns=constant)
        notes.append(f"Dropped constant columns: {', '.join(constant)}.")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            if kind == "logistic":
                fit = sm.Logit(data["__y"], exog).fit(disp=0, cov_type="HC3", maxiter=200)
            else:
                fit = sm.OLS(data["__y"], exog).fit(cov_type="HC3")
        except (np.linalg.LinAlgError, ValueError) as exc:
            raise ToolError(f"The model could not be fit: {exc}") from exc
    ci = fit.conf_int()
    coefficients = []
    for term in exog.columns:
        row: dict[str, Any] = {
            "term": "intercept" if term == "const" else term,
            "estimate": float(fit.params[term]),
            "std_error": float(fit.bse[term]),
            "ci_low": float(ci.loc[term, 0]),
            "ci_high": float(ci.loc[term, 1]),
            "p_value": float(fit.pvalues[term]),
        }
        if kind == "logistic":
            row.update(
                odds_ratio=math.exp(row["estimate"]),
                odds_ratio_ci_low=math.exp(row["ci_low"]),
                odds_ratio_ci_high=math.exp(row["ci_high"]),
            )
        coefficients.append(row)
    vif = []
    features_only = exog.drop(columns="const")
    if features_only.shape[1] > 1:
        corr_inv = np.linalg.pinv(np.corrcoef(features_only.to_numpy(dtype=float), rowvar=False))
        vif = [{"term": t, "vif": float(corr_inv[i, i])} for i, t in enumerate(features_only.columns)]
        high = [v["term"] for v in vif if v["vif"] > 10]
        if high:
            notes.append(f"High multicollinearity (VIF > 10): {', '.join(high)}.")
    result: dict[str, Any] = {
        "design": "observational",
        "table": args.table,
        "target": args.target,
        "model": "logistic regression" if kind == "logistic" else "linear regression (OLS)",
        "standard_errors": "HC3 robust",
        "n": int(fit.nobs),
        "dropped_rows": int(dropped),
        "sampled": sampled,
        "coefficients": coefficients,
        "vif": vif,
        "notes": notes,
    }
    if kind == "logistic":
        result.update(pseudo_r_squared=float(fit.prsquared), log_likelihood=float(fit.llf))
    else:
        result.update(r_squared=float(fit.rsquared), adj_r_squared=float(fit.rsquared_adj))
    result = round_floats(result)
    # Name the model and the filter: runs often fit a logistic and a linear model of the same target.
    title = f"{'Logistic' if kind == 'logistic' else 'Linear'} regression: {args.target}"
    if args.where:
        title += f" where {args.where if len(args.where) <= 60 else args.where[:57] + '...'}"
    return ToolOutput(
        result=result,
        artifact=ArtifactDraft("regression", title, result),
        code=sql,
    )


# --------------------------------------------------------------------------------------------
# Difference-in-differences
# --------------------------------------------------------------------------------------------


class DidInput(BaseModel):
    table: str
    outcome: str
    unit_column: str = Field(description="Unit identifier (store, user, region).")
    time_column: str = Field(description="Period column (date or number).")
    treated_column: str = Field(description="1/true for units that eventually receive the treatment.")
    post_start: str = Field(description="First treated period, e.g. '2026-04-06' or '13'.")
    alpha: float = Field(default=0.05, gt=0, lt=0.5)
    where: str | None = None


@tool(
    "diff_in_diff",
    "Difference-in-differences with unit and period fixed effects and unit-clustered standard errors, "
    "plus a pre-trend test (do treated and control units diverge before treatment?). Returns the "
    "average effect on the treated with a CI, the effect relative to the treated pre-period mean, "
    "and group means by period. Creates a 'did' artifact.",
    DidInput,
)
def diff_in_diff(ctx: ToolContext, args: DidInput) -> ToolOutput:
    src = ctx.source
    cols = [args.outcome, args.unit_column, args.time_column, args.treated_column]
    frame, sql, _ = fetch(src, args.table, cols, args.where, max_rows=500_000)
    df = pd.DataFrame(
        {
            "y": to_numeric(frame[args.outcome]),
            "unit": frame[args.unit_column].astype(str),
            "time": frame[args.time_column],
            "treated": to_numeric(frame[args.treated_column]),
        }
    ).dropna()
    if not set(df["treated"].unique()) <= {0.0, 1.0} or df["treated"].nunique() < 2:
        raise ToolError(f"'{args.treated_column}' must be 0/1 with both groups present.")
    if df.groupby("unit")["treated"].nunique().max() > 1:
        raise ToolError(f"'{args.treated_column}' must be constant within each unit.")
    if pd.api.types.is_numeric_dtype(df["time"]):
        boundary: Any = float(args.post_start)
        df["time_key"] = df["time"].astype(float)
    else:
        df["time_key"] = pd.to_datetime(df["time"].astype(str), errors="coerce")
        boundary = pd.to_datetime(args.post_start, errors="coerce")
        if pd.isna(boundary) or df["time_key"].isna().any():
            raise ToolError("Could not parse the time column or post_start as dates.")
    df["post"] = (df["time_key"] >= boundary).astype(float)
    if df["post"].nunique() < 2:
        raise ToolError("post_start must fall inside the observed periods.")
    units, periods = df["unit"].nunique(), df["time_key"].nunique()
    if units > 3000 or periods > 400:
        raise ToolError("Too many units or periods for fixed effects; aggregate the panel first.")
    df["did"] = df["treated"] * df["post"]
    exog = pd.concat(
        [
            df[["did"]],
            pd.get_dummies(df["unit"], prefix="u", drop_first=True, dtype=float),
            pd.get_dummies(df["time_key"].astype(str), prefix="t", drop_first=True, dtype=float),
        ],
        axis=1,
    )
    exog = sm.add_constant(exog, has_constant="add")
    groups = pd.factorize(df["unit"])[0]
    fit = sm.OLS(df["y"], exog).fit(cov_type="cluster", cov_kwds={"groups": groups})
    att, se = float(fit.params["did"]), float(fit.bse["did"])
    low, high = _ci(att, se, args.alpha, df=units - 1)
    p_value = float(2 * stats.t.sf(abs(att / se), units - 1)) if se > 0 else 1.0
    base = float(df.loc[(df.treated == 1) & (df.post == 0), "y"].mean())

    # Pre-trend: within the pre-period, does the treated-control gap drift over time?
    pre = df[df["post"] == 0].copy()
    order = {t: i for i, t in enumerate(sorted(pre["time_key"].unique()))}
    pre["trend_x_treated"] = pre["time_key"].map(order) * pre["treated"]
    pre_exog = sm.add_constant(
        pd.concat(
            [
                pre[["trend_x_treated"]],
                pd.get_dummies(pre["unit"], prefix="u", drop_first=True, dtype=float),
                pd.get_dummies(pre["time_key"].astype(str), prefix="t", drop_first=True, dtype=float),
            ],
            axis=1,
        ),
        has_constant="add",
    )
    pre_fit = sm.OLS(pre["y"], pre_exog).fit(
        cov_type="cluster", cov_kwds={"groups": pd.factorize(pre["unit"])[0]}
    )
    pre_slope, pre_p = float(pre_fit.params["trend_x_treated"]), float(pre_fit.pvalues["trend_x_treated"])
    pretrend_ok = pre_p >= args.alpha and len(order) >= 3

    means = df.groupby(["treated", "post"])["y"].mean()
    series = df.groupby(["time_key", "treated"])["y"].mean().unstack().sort_index().reset_index()
    result = round_floats(
        {
            "design": "causal" if pretrend_ok else "causal_pretrend_failed",
            "method": "difference-in-differences (two-way fixed effects)",
            "assumption_keywords": ["parallel trend"],
            "table": args.table,
            "outcome": args.outcome,
            "n_units": units,
            "n_treated_units": int(df.loc[df.treated == 1, "unit"].nunique()),
            "n_periods": periods,
            "n": int(fit.nobs),
            "post_start": str(args.post_start),
            "att": att,
            "std_error": se,
            "ci_low": low,
            "ci_high": high,
            "p_value": p_value,
            "treated_pre_mean": base,
            "relative_effect": att / base if base else None,
            "relative_effect_ci_low": low / base if base else None,
            "relative_effect_ci_high": high / base if base else None,
            "means": {
                "treated_pre": float(means.get((1.0, 0.0), np.nan)),
                "treated_post": float(means.get((1.0, 1.0), np.nan)),
                "control_pre": float(means.get((0.0, 0.0), np.nan)),
                "control_post": float(means.get((0.0, 1.0), np.nan)),
            },
            "pretrend_slope": pre_slope,
            "pretrend_p_value": pre_p,
            "pretrend_ok": pretrend_ok,
            "pre_periods": len(order),
            "series": [
                {
                    "period": str(r["time_key"])[:10],
                    "treated_mean": float(r.get(1.0, np.nan)),
                    "control_mean": float(r.get(0.0, np.nan)),
                }
                for _, r in series.iterrows()
            ],
        }
    )
    return ToolOutput(
        result={**result, "series": result["series"][-12:]},
        artifact=ArtifactDraft("did", f"Difference-in-differences: {args.outcome}", result),
        code=sql,
    )


# --------------------------------------------------------------------------------------------
# Propensity score matching
# --------------------------------------------------------------------------------------------


class PsmInput(BaseModel):
    table: str
    treatment_column: str = Field(description="1/true for units that received the treatment.")
    outcome: str
    covariates: list[str] = Field(min_length=1, max_length=20, description="Pre-treatment variables only.")
    caliper: float = Field(default=0.2, gt=0, le=1, description="Max distance in SDs of the logit score.")
    alpha: float = Field(default=0.05, gt=0, lt=0.5)
    where: str | None = None


def _smd(treated: pd.Series, control: pd.Series) -> float:
    pooled = math.sqrt((treated.var() + control.var()) / 2)
    return float((treated.mean() - control.mean()) / pooled) if pooled > 0 else 0.0


@tool(
    "propensity_match",
    "Propensity score matching: logistic propensity model on pre-treatment covariates, 1:1 nearest "
    "neighbor matching on the logit score (with replacement, within a caliper), the average effect on "
    "the treated with a CI, and covariate balance (standardized mean differences) before and after "
    "matching. Creates a 'psm' artifact.",
    PsmInput,
)
def propensity_match(ctx: ToolContext, args: PsmInput) -> ToolOutput:
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler

    src = ctx.source
    frame, sql, sampled = fetch(
        src, args.table, [args.treatment_column, args.outcome, *args.covariates], args.where
    )
    t = to_numeric(frame[args.treatment_column])
    y = to_numeric(frame[args.outcome])
    X, notes = design_matrix(src, args.table, frame, args.covariates)
    data = pd.concat([t.rename("__t"), y.rename("__y"), X], axis=1).dropna()
    if not set(data["__t"].unique()) <= {0.0, 1.0} or data["__t"].nunique() < 2:
        raise ToolError(f"'{args.treatment_column}' must be 0/1 with both groups present.")
    covs = data.drop(columns=["__t", "__y"])
    scaled = StandardScaler().fit_transform(covs)
    model = LogisticRegression(max_iter=1000).fit(scaled, data["__t"])
    ps = np.clip(model.predict_proba(scaled)[:, 1], 1e-6, 1 - 1e-6)
    logit = np.log(ps / (1 - ps))
    caliper = args.caliper * logit.std()
    treated_idx = np.where(data["__t"].values == 1)[0]
    control_idx = np.where(data["__t"].values == 0)[0]
    order = np.argsort(logit[control_idx])
    sorted_logit = logit[control_idx][order]
    pairs = []
    for i in treated_idx:
        pos = np.searchsorted(sorted_logit, logit[i])
        best, best_d = None, math.inf
        for j in (pos - 1, pos):
            if 0 <= j < len(sorted_logit):
                d = abs(sorted_logit[j] - logit[i])
                if d < best_d:
                    best, best_d = j, d
        if best is not None and best_d <= caliper:
            pairs.append((i, control_idx[order[best]]))
    if len(pairs) < 10:
        raise ToolError("Fewer than 10 treated units found a match; widen the caliper or revisit covariates.")
    ti, ci = np.array([p[0] for p in pairs]), np.array([p[1] for p in pairs])
    diffs = data["__y"].values[ti] - data["__y"].values[ci]
    att = float(diffs.mean())
    se = float(diffs.std(ddof=1) / math.sqrt(len(diffs)))
    low, high = _ci(att, se, args.alpha, df=len(diffs) - 1)
    p_value = float(2 * stats.t.sf(abs(att / se), len(diffs) - 1)) if se > 0 else 1.0
    control_mean = float(data["__y"].values[ci].mean())
    balance = []
    for col in covs.columns:
        before = _smd(covs[col].iloc[treated_idx], covs[col].iloc[control_idx])
        after = _smd(covs[col].iloc[ti], covs[col].iloc[ci])
        balance.append({"covariate": col, "smd_before": before, "smd_after": after})
    max_after = max(abs(b["smd_after"]) for b in balance)
    balance_ok = max_after < 0.1
    notes.append(
        "Standard errors treat matched pairs as independent and ignore uncertainty in the propensity "
        "model; with replacement, reused controls make them somewhat optimistic."
    )
    result = round_floats(
        {
            "design": "causal" if balance_ok else "causal_imbalanced",
            "method": "propensity score matching (1:1 nearest neighbor, with replacement)",
            "assumption_keywords": ["confound"],
            "table": args.table,
            "treatment": args.treatment_column,
            "outcome": args.outcome,
            "n_treated": int(len(treated_idx)),
            "n_control": int(len(control_idx)),
            "n_matched": len(pairs),
            "n_unmatched": int(len(treated_idx) - len(pairs)),
            "unique_controls_used": int(len(set(ci.tolist()))),
            "caliper_sd": args.caliper,
            "att": att,
            "std_error": se,
            "ci_low": low,
            "ci_high": high,
            "p_value": p_value,
            "matched_control_mean": control_mean,
            "relative_effect": att / control_mean if control_mean else None,
            "max_abs_smd_after": max_after,
            "balance_ok": balance_ok,
            "balance": balance,
            "sampled": sampled,
            "notes": notes,
        }
    )
    return ToolOutput(
        result=result,
        artifact=ArtifactDraft("psm", f"Matched effect of {args.treatment_column} on {args.outcome}", result),
        code=sql,
    )


# --------------------------------------------------------------------------------------------
# Forecasting
# --------------------------------------------------------------------------------------------

FREQ = {"day": ("D", 7), "week": ("W-MON", 52), "month": ("MS", 12)}


class ForecastInput(BaseModel):
    table: str
    date_column: str
    value_column: str | None = Field(default=None, description="Omit with agg='count' to count rows.")
    agg: Literal["sum", "avg", "count"] = "sum"
    freq: Literal["day", "week", "month"] = "day"
    horizon: int = Field(default=28, ge=1, le=365, description="Periods to forecast.")
    where: str | None = None


def _fit_ets(series: pd.Series, season: int) -> Any:
    from statsmodels.tsa.holtwinters import ExponentialSmoothing

    seasonal = "add" if len(series) >= 2 * season + 2 else None
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        model = ExponentialSmoothing(
            series,
            trend="add",
            damped_trend=True,
            seasonal=seasonal,
            seasonal_periods=season if seasonal else None,
            initialization_method="estimated",
        )
        return model.fit(optimized=True), seasonal is not None


def _mape(actual: np.ndarray, predicted: np.ndarray) -> float | None:
    mask = actual != 0
    return float(np.mean(np.abs((actual[mask] - predicted[mask]) / actual[mask]))) if mask.any() else None


@tool(
    "forecast",
    "Forecast a metric over time: aggregates the column by day, week, or month, fits a damped-trend "
    "Holt-Winters model with seasonality when there is enough history, and returns forecasts with 95% "
    "prediction intervals (simulated), the horizon total with its interval, and a holdout backtest "
    "against a seasonal-naive baseline. Creates a 'forecast' artifact.",
    ForecastInput,
)
def forecast(ctx: ToolContext, args: ForecastInput) -> ToolOutput:
    src, q = ctx.source, ctx.source.quote
    _column(src, args.table, args.date_column)
    if args.agg != "count" and not args.value_column:
        raise ToolError("value_column is required unless agg is 'count'.")
    if args.value_column:
        _column(src, args.table, args.value_column)
    pandas_freq, season = FREQ[args.freq]
    measure = (
        "count(*)" if args.agg == "count" else f"{args.agg}(CAST({q(args.value_column or '')} AS DOUBLE))"
    )
    day = f"CAST({q(args.date_column)} AS DATE)"
    sql = (
        f"SELECT date_trunc('{args.freq}', {day}) AS period, {measure} AS value, "
        f"count(DISTINCT {day}) AS days FROM {q(args.table)} "
        f"{_where(args.where, f'{q(args.date_column)} IS NOT NULL')} GROUP BY 1 ORDER BY 1"
    )
    try:
        rows = src.query(sql).rows
    except QueryError as exc:
        raise ToolError(str(exc)) from exc
    if len(rows) < 8:
        raise ToolError("At least 8 periods of history are needed to forecast.")
    series = pd.Series(
        [r["value"] for r in rows], index=pd.DatetimeIndex([pd.Timestamp(r["period"]) for r in rows])
    ).asfreq(pandas_freq)
    filled = int(series.isna().sum())
    series = series.fillna(0.0 if args.agg in {"sum", "count"} else series.interpolate())
    # The last period may be partial (data stops mid-week or mid-month): drop it from training. It is
    # partial only if it also has fewer dates than a typical period, so data stamped once per period
    # (weekly sales dated each Friday, monthly snapshots) keeps its last, complete period.
    last_date = src.query(f"SELECT max({day})::VARCHAR AS d FROM {q(args.table)} {_where(args.where)}").rows[
        0
    ]["d"]
    period_end = series.index[-1] + pd.tseries.frequencies.to_offset(pandas_freq) - pd.Timedelta(days=1)
    days = [int(r["days"]) for r in rows]
    partial = (
        args.freq != "day" and pd.Timestamp(last_date) < period_end and days[-1] < float(np.median(days[:-1]))
    )
    if partial:
        series = series.iloc[:-1]

    holdout = min(args.horizon, max(1, len(series) // 5))
    train, test = series.iloc[:-holdout], series.iloc[-holdout:]
    backtest_fit, _ = _fit_ets(train, season)
    predicted = np.asarray(backtest_fit.forecast(holdout))
    naive_season = season if len(train) > season else 1
    naive = np.array([train.iloc[-naive_season + (i % naive_season)] for i in range(holdout)])

    fit, seasonal = _fit_ets(series, season)
    point = fit.forecast(args.horizon)
    sims = fit.simulate(
        args.horizon, repetitions=1000, error="add", anchor="end", rng=np.random.default_rng(7)
    )
    sims = np.asarray(sims).reshape(args.horizon, -1)
    lower, upper = np.percentile(sims, 2.5, axis=1), np.percentile(sims, 97.5, axis=1)
    totals = sims.sum(axis=0)
    history = series.iloc[-min(len(series), 4 * season if seasonal else 60) :]
    result = round_floats(
        {
            "design": "forecast",
            "table": args.table,
            "metric": f"{args.agg}({args.value_column or '*'})",
            "freq": args.freq,
            "model": f"Holt-Winters (damped additive trend{', additive seasonality' if seasonal else ''})",
            "season_length": season if seasonal else None,
            "history_periods": len(series),
            "filled_missing_periods": filled,
            "dropped_partial_period": bool(partial),
            "last_actual": float(series.iloc[-1]),
            "horizon": args.horizon,
            "forecasts": [
                {"period": str(idx.date()), "forecast": float(v), "lower": float(lo), "upper": float(hi)}
                for idx, v, lo, hi in zip(point.index, point.values, lower, upper, strict=True)
            ],
            "horizon_total": {
                "forecast": float(point.sum()),
                "lower": float(np.percentile(totals, 2.5)),
                "upper": float(np.percentile(totals, 97.5)),
            },
            "history_total_same_length": float(series.iloc[-args.horizon :].sum())
            if len(series) >= args.horizon
            else None,
            "backtest": {
                "holdout_periods": holdout,
                "mape": _mape(test.values, predicted),
                "seasonal_naive_mape": _mape(test.values, naive),
            },
            "history": [{"period": str(idx.date()), "value": float(v)} for idx, v in history.items()],
        }
    )
    shown = {**result, "forecasts": result["forecasts"][:14], "history": result["history"][-14:]}
    return ToolOutput(
        result=shown,
        artifact=ArtifactDraft("forecast", f"Forecast: {result['metric']} by {args.freq}", result),
        code=sql,
    )
