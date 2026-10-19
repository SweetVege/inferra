"""Modeling tools checked against reference implementations and synthetic data with known answers."""

import json
import os
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import statsmodels.api as sm

from inferra import storage
from inferra.datasources.base import TableInfo
from inferra.datasources.files import FileSource, convert_to_parquet
from inferra.tools.base import ToolContext, ToolError, get_tool
from inferra.tools.ml import predict

SAMPLES = Path(__file__).resolve().parents[3] / "samples"


def ctx_for(data: pd.DataFrame | Path, tmp_path: Path, name: str = "t") -> ToolContext:
    path = data
    if isinstance(data, pd.DataFrame):
        path = tmp_path / f"{name}.csv"
        data.to_csv(path, index=False)
    key = f"workspaces/ws/{name}.parquet"
    rows, columns = convert_to_parquet(Path(path), storage.local_path(key))
    return ToolContext(source=FileSource("ws", [(TableInfo(name, columns, rows), key)]))


def run(ctx: ToolContext, tool: str, **args):  # type: ignore[no-untyped-def]
    return get_tool(tool).run(ctx, {"table": "t", **args})


# --- regression ---------------------------------------------------------------------------------


def test_linear_regression_matches_statsmodels(tmp_path: Path) -> None:
    rng = np.random.default_rng(0)
    n = 800
    df = pd.DataFrame(
        {"x1": rng.normal(size=n), "x2": rng.normal(size=n), "g": rng.choice(["a", "b", "c"], n)}
    )
    df["y"] = 2 + 1.5 * df.x1 - 0.7 * df.x2 + df.g.map({"a": 0, "b": 1.0, "c": -0.5}) + rng.normal(size=n)
    data = run(ctx_for(df, tmp_path), "fit_regression", target="y", features=["x1", "x2", "g"]).artifact.data
    X = sm.add_constant(pd.concat([df[["x1", "x2"]], pd.get_dummies(df.g, dtype=float)[["b", "c"]]], axis=1))
    ref = sm.OLS(df.y, X).fit(cov_type="HC3")
    coef = {c["term"]: c for c in data["coefficients"]}
    assert coef["x1"]["estimate"] == pytest.approx(ref.params["x1"], rel=1e-6)
    assert coef["g=b"]["estimate"] == pytest.approx(ref.params["b"], rel=1e-6)
    assert coef["x2"]["std_error"] == pytest.approx(ref.bse["x2"], rel=1e-6)
    assert data["r_squared"] == pytest.approx(ref.rsquared, rel=1e-6)
    assert data["design"] == "observational"


def test_logistic_regression_reports_odds_ratios(tmp_path: Path) -> None:
    ctx = ctx_for(SAMPLES / "customer_churn.csv", tmp_path)
    features = ["logins_30d", "payment_failures_90d", "plan"]
    artifact = run(ctx, "fit_regression", target="churned", features=features).artifact
    data = artifact.data
    assert data["model"] == "logistic regression"
    # A logistic and a linear probability model of the same target are told apart by title.
    assert artifact.title == "Logistic regression: churned"
    linear = run(
        ctx, "fit_regression", target="churned", features=features, model="linear", where="logins_30d > 0"
    ).artifact
    assert linear.title == "Linear regression: churned where logins_30d > 0"
    failures = next(c for c in data["coefficients"] if c["term"] == "payment_failures_90d")
    assert failures["odds_ratio"] == pytest.approx(np.exp(failures["estimate"]), rel=1e-6)
    assert failures["odds_ratio_ci_low"] < failures["odds_ratio"] < failures["odds_ratio_ci_high"]
    assert 0.6 < failures["estimate"] < 1.2  # generated with 0.9


# --- difference-in-differences --------------------------------------------------------------------


def panel(tmp_path: Path, effect: float, diverge: float = 0.0, seed: int = 1) -> ToolContext:
    rng = np.random.default_rng(seed)
    rows = []
    for unit in range(40):
        treated = unit < 15
        base = rng.normal(100, 10)
        for t in range(12):
            y = base + 2 * t + (effect if treated and t >= 6 else 0) + (diverge * t if treated else 0)
            rows.append({"unit": f"u{unit}", "t": t, "treated": int(treated), "y": y + rng.normal(0, 1)})
    return ctx_for(pd.DataFrame(rows), tmp_path)


def test_did_recovers_effect_and_matches_two_by_two(tmp_path: Path) -> None:
    ctx = panel(tmp_path, effect=5.0)
    data = run(
        ctx,
        "diff_in_diff",
        outcome="y",
        unit_column="unit",
        time_column="t",
        treated_column="treated",
        post_start="6",
    ).artifact.data
    m = data["means"]
    two_by_two = (m["treated_post"] - m["treated_pre"]) - (m["control_post"] - m["control_pre"])
    assert data["att"] == pytest.approx(
        two_by_two, abs=1e-4
    )  # balanced panel, common timing (means are rounded)
    assert data["ci_low"] < 5.0 < data["ci_high"]
    assert data["pretrend_ok"] and data["design"] == "causal"
    assert data["assumption_keywords"] == ["parallel trend"]


def test_did_flags_diverging_pre_trends(tmp_path: Path) -> None:
    data = run(
        panel(tmp_path, effect=0.0, diverge=1.5),
        "diff_in_diff",
        outcome="y",
        unit_column="unit",
        time_column="t",
        treated_column="treated",
        post_start="6",
    ).artifact.data
    assert not data["pretrend_ok"] and data["design"] == "causal_pretrend_failed"


def test_did_on_dates(tmp_path: Path) -> None:
    data = run(
        ctx_for(SAMPLES / "loyalty_program.csv", tmp_path),
        "diff_in_diff",
        outcome="weekly_sales",
        unit_column="store_id",
        time_column="week_start",
        treated_column="in_program",
        post_start="2026-04-06",
    ).artifact.data
    assert data["relative_effect_ci_low"] < 0.06 < data["relative_effect_ci_high"]  # generated with +6%
    assert data["n_units"] == 60 and data["n_periods"] == 26


# --- propensity matching ----------------------------------------------------------------------


def test_psm_removes_confounding(tmp_path: Path) -> None:
    rng = np.random.default_rng(5)
    n = 4000
    x = rng.normal(size=n)
    treated = (rng.random(n) < 1 / (1 + np.exp(-1.5 * x))).astype(int)
    y = 3 * x + 2.0 * treated + rng.normal(size=n)  # true effect 2; x confounds
    df = pd.DataFrame({"x": x, "treated": treated, "y": y})
    naive = df[df.treated == 1].y.mean() - df[df.treated == 0].y.mean()
    data = run(
        ctx_for(df, tmp_path), "propensity_match", treatment_column="treated", outcome="y", covariates=["x"]
    ).artifact.data
    assert naive > 4  # badly confounded
    assert data["ci_low"] < 2.0 < data["ci_high"]
    assert abs(data["balance"][0]["smd_after"]) < abs(data["balance"][0]["smd_before"])
    assert data["balance_ok"] and data["design"] == "causal"


# --- forecasting ------------------------------------------------------------------------------


def test_forecast_beats_naive_and_intervals_cover(tmp_path: Path) -> None:
    rng = np.random.default_rng(9)
    days = pd.date_range("2025-01-01", periods=400, freq="D")
    weekly = np.array([0.9, 0.95, 1.0, 1.0, 1.05, 1.2, 0.9])[days.dayofweek]
    level = 200 + 0.3 * np.arange(400)
    y = level * weekly + rng.normal(0, 6, 400)
    df = pd.DataFrame({"d": days.strftime("%Y-%m-%d"), "v": y})
    history = df.iloc[:372]
    data = run(
        ctx_for(history, tmp_path), "forecast", date_column="d", value_column="v", horizon=28
    ).artifact.data
    actual = y[372:]
    inside = np.mean([f["lower"] <= a <= f["upper"] for f, a in zip(data["forecasts"], actual, strict=True)])
    assert inside >= 0.8
    assert data["backtest"]["mape"] < data["backtest"]["seasonal_naive_mape"]
    total = data["horizon_total"]
    assert total["lower"] < actual.sum() < total["upper"]


def test_forecast_drops_partial_last_period(tmp_path: Path) -> None:
    data = run(
        ctx_for(SAMPLES / "daily_sales.csv", tmp_path),
        "forecast",
        date_column="date",
        value_column="revenue",
        freq="week",
        horizon=4,
    ).artifact.data
    assert data["dropped_partial_period"] is True
    with pytest.raises(ToolError):
        run(ctx_for(SAMPLES / "daily_sales.csv", tmp_path), "forecast", date_column="date", agg="sum")


def test_forecast_keeps_the_last_week_of_data_stamped_once_a_week(tmp_path: Path) -> None:
    # Weekly totals dated each Friday (like Walmart's): the last week is complete, though the
    # Monday-to-Sunday bucket it falls in runs past the last date.
    fridays = pd.date_range("2022-01-07", periods=60, freq="W-FRI")
    rng = np.random.default_rng(4)
    df = pd.DataFrame({"week": fridays.strftime("%Y-%m-%d"), "sales": 100 + rng.normal(0, 5, 60)})
    data = run(
        ctx_for(df, tmp_path), "forecast", date_column="week", value_column="sales", freq="week", horizon=4
    ).artifact.data
    assert data["dropped_partial_period"] is False
    assert data["history_periods"] == 60
    assert data["last_actual"] == pytest.approx(df.sales.iloc[-1], rel=1e-6)


# --- machine learning -------------------------------------------------------------------------


def test_model_selection_prefers_simple_model_when_it_is_enough(tmp_path: Path) -> None:
    out = run(ctx_for(SAMPLES / "customer_churn.csv", tmp_path), "train_model", target="churned")
    data = out.artifact.data
    excluded = {e["feature"]: e["reason"] for e in data["excluded_features"]}
    assert "customer_id" in excluded and "signup_date" in excluded
    assert data["selected_model"] == "logistic regression"  # data were generated by a logistic model
    assert data["holdout"]["auc"] >= data["tree_model_holdout"]["auc"]
    assert json.loads(out.model.payload)["type"] == "linear"
    # The agent sees every calibration bin, including the high-score ones a churn model is used on.
    assert len(out.result["calibration"]) == len(data["calibration"]) == 10


def test_trees_win_on_nonlinear_data_and_scoring_reproduces_holdout(tmp_path: Path) -> None:
    rng = np.random.default_rng(3)
    n = 4000
    df = pd.DataFrame(
        {"a": rng.uniform(-2, 2, n), "b": rng.uniform(-2, 2, n), "c": rng.choice(["x", "y"], n)}
    )
    logit = 3 * ((df.a > 0) ^ (df.b > 0)) - 1.5 + (df.c == "x") * 0.5  # XOR: invisible to a linear model
    df["label"] = (rng.random(n) < 1 / (1 + np.exp(-logit))).astype(int)
    out = run(ctx_for(df, tmp_path), "train_model", target="label")
    data = out.artifact.data
    assert data["selected_model"] == "gradient-boosted trees"
    assert data["holdout"]["auc"] > data["baseline"]["auc"] + 0.1
    scores = predict(out.model.payload, df)
    assert scores.shape == (n,) and ((scores >= 0) & (scores <= 1)).all()
    assert {d["feature"] for d in data["drivers"][:2]} == {"a", "b"}


def test_leaky_features_are_excluded(tmp_path: Path) -> None:
    rng = np.random.default_rng(4)
    n = 1500
    df = pd.DataFrame({"x": rng.normal(size=n)})
    df["label"] = (rng.random(n) < 1 / (1 + np.exp(-df.x))).astype(int)
    df["cancel_reason_code"] = df.label * 3 + rng.integers(0, 2, n) * 0  # recorded after the outcome
    data = run(ctx_for(df, tmp_path), "train_model", target="label").artifact.data
    assert any(
        e["feature"] == "cancel_reason_code" and "leakage" in e["reason"] for e in data["excluded_features"]
    )


def test_regression_models(tmp_path: Path) -> None:
    rng = np.random.default_rng(6)
    n = 2000
    df = pd.DataFrame({"a": rng.normal(size=n), "b": rng.normal(size=n)})
    df["y"] = 3 * df.a + np.sin(3 * df.b) * 2 + rng.normal(0, 0.5, n)
    data = run(ctx_for(df, tmp_path), "train_model", target="y").artifact.data
    assert data["task"] == "regression"
    assert data["holdout"]["rmse"] < data["naive"]["rmse"]
    assert data["cv"]["metric"] == "rmse"
    assert data["beats_naive"] is True
    assert not any("do not use it" in line for line in data["card"]["limitations"])


def test_a_model_no_better_than_the_mean_is_flagged(tmp_path: Path) -> None:
    rng = np.random.default_rng(8)
    n = 1000
    df = pd.DataFrame({"a": rng.normal(size=n), "b": rng.normal(size=n), "y": rng.normal(size=n)})
    data = run(ctx_for(df, tmp_path), "train_model", target="y").artifact.data
    assert data["beats_naive"] is False
    assert data["card"]["limitations"][0].startswith("The selected model is not meaningfully better")


def test_lightgbm_threads_follow_the_container_cpu_quota(tmp_path: Path) -> None:
    from inferra.resources import cpu_threads

    quota = tmp_path / "cpu.max"
    quota.write_text("10000 100000\n")  # a 0.1-CPU instance
    assert cpu_threads(cpu_max=str(quota)) == 1
    quota.write_text("max 100000\n")
    assert cpu_threads(cpu_max=str(quota)) == min(2, len(os.sched_getaffinity(0)))
    assert cpu_threads(cpu_max=str(tmp_path / "missing")) >= 1


def test_grouped_validation_holds_out_whole_groups(tmp_path: Path) -> None:
    # Like weekly sales by store: a per-store trait identifies the store, and most of the signal is the
    # store's own level. A random split rewards memorizing stores; a grouped split shows the real skill.
    rng = np.random.default_rng(7)
    stores, weeks = 40, 50
    level = rng.normal(0, 3, stores)
    size = rng.permutation(np.arange(100, 100 + stores * 7, 7))  # unique per store, unrelated to level
    df = pd.DataFrame({"store": np.repeat(np.arange(stores), weeks), "temp": rng.normal(size=stores * weeks)})
    df["size"] = size[df.store]
    df["sales"] = level[df.store] + 0.5 * df.temp + rng.normal(0, 0.5, len(df))
    ctx = ctx_for(df, tmp_path)

    random = run(ctx, "train_model", target="sales", features=["size", "temp"]).artifact.data
    grouped = run(
        ctx, "train_model", target="sales", features=["size", "temp"], group_column="store"
    ).artifact.data
    assert random["validation"] == {"split": "random"}
    v = grouped["validation"]
    assert v["split"] == "grouped" and v["group_column"] == "store" and v["groups"] == stores
    assert grouped["n_test"] == v["test_groups"] * weeks  # every held-out store is held out entirely
    assert random["holdout"]["r2"] > 0.8 > 0.3 > grouped["holdout"]["r2"]
    assert any("held out entirely" in line for line in grouped["card"]["limitations"])

    # The group column is never a feature, even when listed.
    listed = run(ctx, "train_model", target="sales", features=["store", "temp"], group_column="store")
    assert [f["name"] for f in listed.artifact.data["features"]] == ["temp"]
    with pytest.raises(ToolError, match="at least 10 groups"):
        run(ctx, "train_model", target="sales", group_column="size", where="store < 5")


# --- views over several tables ------------------------------------------------------------------


def multi_table_ctx(tmp_path: Path, frames: dict[str, pd.DataFrame], workspace: str = "ws") -> ToolContext:
    tables = []
    for name, frame in frames.items():
        path = tmp_path / f"{name}.csv"
        frame.to_csv(path, index=False)
        key = f"workspaces/{workspace}/{name}.parquet"
        rows, columns = convert_to_parquet(path, storage.local_path(key))
        tables.append((TableInfo(name, columns, rows), key))
    return ToolContext(source=FileSource(workspace, tables))


def test_a_view_joins_tables_so_single_table_tools_can_use_them(tmp_path: Path) -> None:
    # Orders with several items each; the score depends on lateness and on the order's freight.
    rng = np.random.default_rng(9)
    n = 3000
    orders = pd.DataFrame({"order_id": np.arange(n), "late": (rng.random(n) < 0.2).astype(int)})
    items = pd.DataFrame({"order_id": np.repeat(orders.order_id, 2), "freight": rng.uniform(5, 50, 2 * n)})
    freight = items.groupby("order_id").freight.sum().to_numpy()
    reviews = pd.DataFrame(
        {
            "order_id": orders.order_id,
            "score": 4.5 - 1.5 * orders.late - 0.01 * freight + rng.normal(0, 0.5, n),
        }
    )
    ctx = multi_table_ctx(tmp_path, {"orders": orders, "items": items, "reviews": reviews})
    sql = """
        SELECT o.order_id, o.late, f.freight, r.score
        FROM orders o
        JOIN (SELECT order_id, sum(freight) AS freight FROM items GROUP BY 1) f USING (order_id)
        JOIN reviews r USING (order_id)
    """
    out = get_tool("create_view").run(
        ctx, {"name": "order_reviews", "sql": sql, "description": "One row per order."}
    )
    assert out.result["total_rows"] == n and out.artifact.kind == "table"
    assert "order_reviews" in {t.name for t in ctx.source.list_tables()}
    # Passing SQL where a table name goes points the agent to the view tool.
    with pytest.raises(ToolError, match="create_view"):
        get_tool("fit_regression").run(ctx, {"table": f"({sql})", "target": "score", "features": ["late"]})

    fit = (
        get_tool("fit_regression")
        .run(ctx, {"table": "order_reviews", "target": "score", "features": ["late", "freight"]})
        .artifact.data
    )
    late = next(c for c in fit["coefficients"] if c["term"] == "late")
    assert late["ci_low"] < -1.5 < late["ci_high"]

    # A later view can build on an earlier one; names cannot collide.
    get_tool("create_view").run(
        ctx, {"name": "late_orders", "sql": "SELECT * FROM order_reviews WHERE late = 1", "description": "x"}
    )
    with pytest.raises(ToolError, match="already exists"):
        get_tool("create_view").run(ctx, {"name": "orders", "sql": "SELECT 1 AS x", "description": "x"})


@pytest.mark.parametrize(
    "sql",
    [
        "DROP VIEW orders",
        "SELECT 1; SELECT 2",
        "SELECT * FROM read_csv('/etc/passwd')",
        "SELECT * FROM read_parquet('OTHER')",
    ],
)
def test_views_stay_inside_the_sandbox(tmp_path: Path, sql: str) -> None:
    frame = pd.DataFrame({"x": [1, 2, 3]})
    multi_table_ctx(tmp_path, {"orders": frame}, workspace="other")  # another workspace's file
    ctx = multi_table_ctx(tmp_path, {"orders": frame})
    sql = sql.replace("OTHER", str(storage.local_path("workspaces/other/orders.parquet")))
    with pytest.raises(ToolError):
        get_tool("create_view").run(ctx, {"name": "leak", "sql": sql, "description": "x"})
    assert [t.name for t in ctx.source.list_tables()] == ["orders"]


def test_later_steps_are_told_about_views() -> None:
    from types import SimpleNamespace

    from inferra.runtime.executor import _brief

    run = SimpleNamespace(
        plan={"title": "t", "params": {}}, goal="g", intent="estimate_effect", clarifications=[]
    )
    step = SimpleNamespace(task="estimate")
    view = TableInfo("order_reviews", [{"name": "late", "type": "INTEGER"}], 1234, "One row per order.")
    brief = _brief(run, step, [], [], [], None, [view])  # type: ignore[arg-type]
    assert "- order_reviews (1,234 rows): One row per order. Columns: late" in brief


def test_tools_handle_the_nullable_columns_a_join_returns(tmp_path: Path) -> None:
    # A comparison on a missing date is NULL, so DuckDB hands pandas nullable boolean and integer
    # columns; regression once crashed on them in its multicollinearity check.
    rng = np.random.default_rng(10)
    n = 1500
    start = pd.Timestamp("2017-01-01") + pd.to_timedelta(rng.integers(0, 300, n), unit="D")
    orders = pd.DataFrame(
        {
            "order_id": np.arange(n),
            "purchased": start,
            "estimated": start + pd.to_timedelta(20, unit="D"),
            "delivered": start + pd.to_timedelta(rng.integers(5, 35, n), unit="D"),
            "freight": rng.uniform(1, 50, n),
        }
    )
    orders.loc[:30, "delivered"] = pd.NaT
    late = (orders.delivered > orders.estimated).astype(float)
    reviews = pd.DataFrame({"order_id": orders.order_id, "score": 4.5 - 1.5 * late + rng.normal(0, 0.5, n)})
    ctx = multi_table_ctx(tmp_path, {"orders": orders, "reviews": reviews})
    sql = """
        SELECT o.order_id, o.delivered > o.estimated AS late, (o.delivered > o.estimated)::INT AS late_i,
               o.freight, o.purchased, o.delivered - o.purchased AS took, r.score
        FROM orders o JOIN reviews r USING (order_id)
    """
    get_tool("create_view").run(ctx, {"name": "v", "sql": sql, "description": "One row per order."})

    fit = get_tool("fit_regression").run(
        ctx, {"table": "v", "target": "score", "features": ["late", "freight", "purchased", "took"]}
    )
    late_term = next(c for c in fit.artifact.data["coefficients"] if c["term"] == "late")
    assert late_term["ci_low"] < -1.5 < late_term["ci_high"]
    assert fit.artifact.data["dropped_rows"] == 31  # the undelivered orders, not garbage values
    match = get_tool("propensity_match").run(
        ctx, {"table": "v", "treatment_column": "late_i", "outcome": "score", "covariates": ["freight"]}
    )
    assert match.artifact is not None


def test_an_unexpected_tool_error_costs_one_call_not_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from inferra.agents import specialist

    def broken(ctx: object, args: dict) -> None:
        raise AttributeError("'float' object has no attribute 'shape'")

    calls: list[dict] = []
    rec = SimpleNamespace(event=lambda *a, **k: None, tool_call=lambda *a, **k: calls.append(k))
    tool = SimpleNamespace(run=broken)
    use = SimpleNamespace(id="toolu_1", name="fit_regression", input={})
    spec = SimpleNamespace(title="Analyst Agent", name="analyst")
    result = specialist._execute(tool, use, SimpleNamespace(key="s"), spec, None, rec, [])
    assert result["is_error"] and "internal error (AttributeError" in result["content"]
    assert "AttributeError" in calls[0]["error"]


def test_large_training_sets_use_three_folds(tmp_path: Path) -> None:
    from inferra.tools.ml import n_folds

    assert n_folds(10_000) == 5 and n_folds(80_000) == 3
    rng = np.random.default_rng(11)
    n = 60_000
    df = pd.DataFrame({"a": rng.normal(size=n), "b": rng.normal(size=n)})
    df["label"] = (rng.random(n) < 1 / (1 + np.exp(-(df.a - 1.5)))).astype(int)
    data = run(ctx_for(df, tmp_path), "train_model", target="label").artifact.data
    assert len(data["cv"]["folds"]) == 3 and data["holdout"]["auc"] > 0.65
