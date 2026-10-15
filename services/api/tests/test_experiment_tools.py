"""Experiment tools checked against reference implementations (scipy, statsmodels)."""

import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import statsmodels.api as sm
from scipy import stats
from statsmodels.stats.multitest import multipletests
from statsmodels.stats.power import NormalIndPower
from statsmodels.stats.proportion import proportions_ztest
from statsmodels.stats.weightstats import CompareMeans, DescrStatsW

from inferra import storage
from inferra.datasources.base import TableInfo
from inferra.datasources.files import FileSource, convert_to_parquet
from inferra.tools.base import ToolContext, ToolError, get_tool
from inferra.tools.experiment import holm

SAMPLES = Path(__file__).resolve().parents[3] / "samples"


def source_for(frame_or_csv: pd.DataFrame | Path, tmp_path: Path, name: str = "exp") -> ToolContext:
    csv = frame_or_csv
    if isinstance(frame_or_csv, pd.DataFrame):
        csv = tmp_path / f"{name}.csv"
        frame_or_csv.to_csv(csv, index=False)
    key = f"workspaces/ws/{name}.parquet"
    rows, columns = convert_to_parquet(Path(csv), storage.local_path(key))
    return ToolContext(source=FileSource("ws", [(TableInfo(name, columns, rows), key)]))


def run(ctx: ToolContext, tool: str, **args):  # type: ignore[no-untyped-def]
    return get_tool(tool).run(ctx, {"table": "exp", **args}).artifact.data


@pytest.fixture
def checkout(tmp_path: Path) -> tuple[ToolContext, pd.DataFrame]:
    path = SAMPLES / "checkout_experiment.csv"
    return source_for(path, tmp_path), pd.read_csv(path)


def test_binary_ab_test_matches_statsmodels(checkout) -> None:  # type: ignore[no-untyped-def]
    ctx, df = checkout
    data = run(ctx, "ab_test", variant_column="variant", control="control", metric_column="converted")
    assert data["metric_type"] == "binary" and data["design"] == "randomized"
    c = data["comparisons"][0]
    counts = df.groupby("variant").converted.agg(["sum", "count"])
    z, p = proportions_ztest(
        [counts.loc["treatment", "sum"], counts.loc["control", "sum"]],
        [counts.loc["treatment", "count"], counts.loc["control", "count"]],
    )
    assert c["statistic"] == pytest.approx(z, rel=1e-5)
    assert c["p_value"] == pytest.approx(p, rel=1e-4)
    rate = df.groupby("variant").converted.mean()
    assert c["relative_lift"] == pytest.approx(rate["treatment"] / rate["control"] - 1, rel=1e-6)
    assert c["diff_ci_low"] < c["diff"] < c["diff_ci_high"]
    assert c["relative_lift_ci_low"] < c["relative_lift"] < c["relative_lift_ci_high"]


def test_continuous_ab_test_matches_welch(checkout) -> None:  # type: ignore[no-untyped-def]
    ctx, df = checkout
    data = run(
        ctx, "ab_test", variant_column="variant", control="control", metric_column="checkout_latency_ms"
    )
    c = data["comparisons"][0]
    t_vals, c_vals = (df[df.variant == v].checkout_latency_ms.astype(float) for v in ("treatment", "control"))
    ref = stats.ttest_ind(t_vals, c_vals, equal_var=False)
    assert c["statistic"] == pytest.approx(ref.statistic, rel=1e-5)
    assert c["p_value"] == pytest.approx(ref.pvalue, rel=1e-3, abs=1e-12)
    low, high = CompareMeans(DescrStatsW(t_vals), DescrStatsW(c_vals)).tconfint_diff(usevar="unequal")
    assert (c["diff_ci_low"], c["diff_ci_high"]) == pytest.approx((low, high), rel=1e-5)


def test_holm_matches_statsmodels() -> None:
    p = [0.01, 0.04, 0.03, 0.2, 0.001]
    assert holm(p) == pytest.approx(list(multipletests(p, method="holm")[1]))


def test_multiple_treatments_are_holm_corrected(tmp_path: Path) -> None:
    rng = np.random.default_rng(1)
    frame = pd.DataFrame({"variant": rng.choice(["a", "b", "c"], 3000), "y": rng.normal(10, 2, 3000)})
    data = run(
        source_for(frame, tmp_path), "ab_test", variant_column="variant", control="a", metric_column="y"
    )
    raw = [c["p_value"] for c in data["comparisons"]]
    assert [c["treatment"] for c in data["comparisons"]] == ["b", "c"]
    assert [c["p_value_adjusted"] for c in data["comparisons"]] == pytest.approx(holm(raw))


def test_srm_detects_broken_assignment(tmp_path: Path) -> None:
    frame = pd.DataFrame({"variant": ["control"] * 5300 + ["treatment"] * 4700, "y": 1})
    data = run(source_for(frame, tmp_path), "srm_check", variant_column="variant")
    chi2, p = stats.chisquare([5300, 4700])
    assert data["p_value"] == pytest.approx(p) and data["chi2"] == pytest.approx(chi2)
    assert data["srm_ok"] is False
    ab = run(
        source_for(frame, tmp_path),
        "ab_test",
        variant_column="variant",
        control="control",
        metric_column="y",
        metric_type="continuous",
    )
    assert ab["srm_ok"] is False and ab["design"] == "randomized_srm_failed"
    weighted = run(
        source_for(frame, tmp_path),
        "srm_check",
        variant_column="variant",
        expected_shares=[{"variant": "control", "share": 53}, {"variant": "treatment", "share": 47}],
    )
    assert weighted["srm_ok"] is True


def test_cuped_equals_ancova_and_reduces_variance(checkout) -> None:  # type: ignore[no-untyped-def]
    ctx, df = checkout
    data = run(
        ctx,
        "cuped_test",
        variant_column="variant",
        control="control",
        metric_column="revenue",
        covariate_column="pre_orders",
    )
    treat = (df.variant == "treatment").astype(float)
    ols = sm.OLS(df.revenue, sm.add_constant(pd.DataFrame({"t": treat, "x": df.pre_orders}))).fit()
    assert data["comparisons"][0]["diff"] == pytest.approx(ols.params["t"], rel=1e-6)
    assert 0 < data["variance_reduction"] < 1
    raw_width = data["unadjusted"][0]["diff_ci_high"] - data["unadjusted"][0]["diff_ci_low"]
    adj_width = data["comparisons"][0]["diff_ci_high"] - data["comparisons"][0]["diff_ci_low"]
    assert adj_width < raw_width


def test_power_matches_statsmodels_for_means() -> None:
    ctx = ToolContext(source=None)  # type: ignore[arg-type]
    data = (
        get_tool("power_analysis")
        .run(
            ctx,
            {
                "metric_type": "continuous",
                "baseline_mean": 50,
                "baseline_std": 20,
                "mde": 0.05,
                "daily_units": 1000,
            },
        )
        .artifact.data
    )
    ref = NormalIndPower().solve_power(effect_size=2.5 / 20, alpha=0.05, power=0.8, ratio=1)
    assert data["sample_size_per_variant"] == math.ceil(ref)
    assert data["estimated_days"] == math.ceil(2 * math.ceil(ref) / 1000)
    mdes = [row["mde_relative"] for row in data["mde_by_duration"]]
    assert mdes == sorted(mdes, reverse=True)


def test_power_for_proportions_and_extra_arms() -> None:
    ctx = ToolContext(source=None)  # type: ignore[arg-type]
    two = (
        get_tool("power_analysis")
        .run(ctx, {"metric_type": "binary", "baseline_mean": 0.1, "mde": 0.1})
        .artifact.data
    )
    z = stats.norm.ppf(0.975) + stats.norm.ppf(0.8)
    expected = math.ceil(z**2 * (0.1 * 0.9 + 0.11 * 0.89) / 0.01**2)
    assert two["sample_size_per_variant"] == expected
    three = (
        get_tool("power_analysis")
        .run(ctx, {"metric_type": "binary", "baseline_mean": 0.1, "mde": 0.1, "variants": 3})
        .artifact.data
    )
    assert three["alpha_per_comparison"] == 0.025 and three["sample_size_per_variant"] > expected
    with pytest.raises(ToolError):
        get_tool("power_analysis").run(ctx, {"metric_type": "binary", "baseline_mean": 0.95, "mde": 0.1})


def test_segment_effects_match_per_segment_tests(checkout) -> None:  # type: ignore[no-untyped-def]
    ctx, _ = checkout
    data = run(
        ctx,
        "segment_effects",
        variant_column="variant",
        control="control",
        treatment="treatment",
        metric_column="converted",
        segment_column="device",
    )
    assert {s["segment"] for s in data["segments"]} == {"desktop", "mobile", "tablet"}
    for seg in data["segments"]:
        single = run(
            ctx,
            "ab_test",
            variant_column="variant",
            control="control",
            metric_column="converted",
            where=f"device = '{seg['segment']}'",
        )
        assert seg["p_value"] == pytest.approx(single["comparisons"][0]["p_value"], rel=1e-6)
    assert [s["p_value_adjusted"] for s in data["segments"]] == pytest.approx(
        list(multipletests([s["p_value"] for s in data["segments"]], method="holm")[1])
    )
    assert 0 <= data["heterogeneity_p_value"] <= 1


def test_bayesian_agrees_with_normal_approximation(checkout) -> None:  # type: ignore[no-untyped-def]
    ctx, df = checkout
    data = run(
        ctx,
        "bayesian_ab",
        variant_column="variant",
        control="control",
        treatment="treatment",
        metric_column="converted",
    )
    g = df.groupby("variant").converted.agg(["mean", "count"])
    se = math.sqrt(sum(g.loc[v, "mean"] * (1 - g.loc[v, "mean"]) / g.loc[v, "count"] for v in g.index))
    approx = stats.norm.cdf((g.loc["treatment", "mean"] - g.loc["control", "mean"]) / se)
    assert data["prob_treatment_better"] == pytest.approx(approx, abs=0.01)
    assert data["relative_lift_ci_low"] < data["relative_lift_mean"] < data["relative_lift_ci_high"]


def test_sequential_test_controls_peeking(tmp_path: Path) -> None:
    rng = np.random.default_rng(3)
    n = 20000
    days = pd.date_range("2026-01-01", periods=20).strftime("%Y-%m-%d")
    null = pd.DataFrame(
        {"variant": rng.choice(["c", "t"], n), "y": rng.normal(10, 3, n), "d": rng.choice(days, n)}
    )
    data = run(
        source_for(null, tmp_path),
        "sequential_test",
        variant_column="variant",
        control="c",
        treatment="t",
        metric_column="y",
        date_column="d",
    )
    assert data["always_valid_p_value"] > 0.05 and data["first_significant_day"] is None
    assert len(data["looks"]) == 20
    assert [lk["n_control"] for lk in data["looks"]] == sorted(lk["n_control"] for lk in data["looks"])

    effect = null.assign(y=null.y + np.where(null.variant == "t", 0.6, 0))
    data = run(
        source_for(effect, tmp_path, "exp"),
        "sequential_test",
        variant_column="variant",
        control="c",
        treatment="t",
        metric_column="y",
        date_column="d",
    )
    assert data["significant"] and data["first_significant_day"] is not None


def test_baseline_reports_daily_traffic(checkout) -> None:  # type: ignore[no-untyped-def]
    ctx, df = checkout
    data = run(ctx, "metric_baseline", metric_column="converted", date_column="assigned_on")
    assert data["metric_type"] == "binary"
    assert data["mean"] == pytest.approx(df.converted.mean())
    assert data["daily_units"] == pytest.approx(len(df) / df.assigned_on.nunique())


def test_unknown_variant_is_a_helpful_error(checkout) -> None:  # type: ignore[no-untyped-def]
    ctx, _ = checkout
    with pytest.raises(ToolError, match="Variants: control, treatment"):
        run(ctx, "ab_test", variant_column="variant", control="A", metric_column="converted")
