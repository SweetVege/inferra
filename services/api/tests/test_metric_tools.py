"""Metric layer tools: exact decompositions, change points, and computations against pandas."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from inferra import storage
from inferra.datasources.base import TableInfo
from inferra.datasources.files import FileSource, convert_to_parquet
from inferra.tools.base import ToolContext, ToolError, get_tool

SAMPLES = Path(__file__).resolve().parents[3] / "samples"
CONVERSION = {
    "name": "conversion_rate",
    "label": "Conversion",
    "table": "t",
    "kind": "ratio",
    "numerator": {"kind": "sum", "column": "signups"},
    "denominator": {"kind": "sum", "column": "sessions"},
    "time_column": "date",
}
SESSIONS = {"name": "sessions", "table": "t", "kind": "sum", "column": "sessions", "time_column": "date"}


def ctx_for(data: pd.DataFrame | Path, tmp_path: Path) -> ToolContext:
    path = data
    if isinstance(data, pd.DataFrame):
        path = tmp_path / "t.csv"
        data.to_csv(path, index=False)
    key = "workspaces/ws/t.parquet"
    rows, columns = convert_to_parquet(Path(path), storage.local_path(key))
    return ToolContext(
        source=FileSource("ws", [(TableInfo("t", columns, rows), key)]),
        metrics={"conversion_rate": CONVERSION, "sessions": SESSIONS},
    )


def run(ctx: ToolContext, tool: str, **args):  # type: ignore[no-untyped-def]
    return get_tool(tool).run(ctx, args).artifact.data


PERIODS = {
    "baseline_start": "2026-08-31",
    "baseline_end": "2026-09-20",
    "comparison_start": "2026-09-21",
    "comparison_end": "2026-10-11",
}


@pytest.fixture
def funnel(tmp_path: Path) -> tuple[ToolContext, pd.DataFrame]:
    path = SAMPLES / "daily_funnel.csv"
    return ctx_for(path, tmp_path), pd.read_csv(path)


def test_compute_metric_matches_pandas(funnel) -> None:  # type: ignore[no-untyped-def]
    ctx, df = funnel
    data = run(ctx, "compute_metric", metric="conversion_rate", group_by=["platform"], start="2026-09-21")
    sub = df[df.date >= "2026-09-21"]
    g = sub.groupby("platform")[["signups", "sessions"]].sum()
    for row in data["rows"]:
        assert row["value"] == pytest.approx(
            g.loc[row["platform"], "signups"] / g.loc[row["platform"], "sessions"]
        )
    assert data["total"]["value"] == pytest.approx(sub.signups.sum() / sub.sessions.sum())


def test_ratio_decomposition_is_exact(funnel) -> None:  # type: ignore[no-untyped-def]
    ctx, _ = funnel
    data = run(
        ctx,
        "decompose_change",
        metric="conversion_rate",
        dimensions=["platform", "app_version", "channel"],
        top_n=20,
        **PERIODS,
    )
    for dim in data["dimensions"]:
        effects = sum(s["effect"] for s in dim["segments"])
        assert effects == pytest.approx(data["change"], rel=1e-6)
        assert dim["mix_effect"] + dim["rate_effect"] == pytest.approx(data["change"], rel=1e-6)
    # The planted cause: Android release 5.2 (new in the comparison period) carries the drop.
    top = data["dimensions"][0]
    assert (top["dimension"], top["top_segment"]) == ("app_version", "5.2")
    platform = next(d for d in data["dimensions"] if d["dimension"] == "platform")
    assert platform["top_segment"] == "android"


def test_additive_decomposition_is_exact(funnel) -> None:  # type: ignore[no-untyped-def]
    ctx, _ = funnel
    data = run(ctx, "decompose_change", metric="sessions", dimensions=["channel"], **PERIODS)
    assert sum(s["effect"] for s in data["dimensions"][0]["segments"]) == pytest.approx(data["change"])
    assert data["method"] == "additive segment deltas"


def test_inline_specs_and_distinct_counts(funnel) -> None:  # type: ignore[no-untyped-def]
    ctx, df = funnel
    spec = {"table": "t", "kind": "count_distinct", "column": "country", "time_column": "date"}
    data = run(ctx, "compute_metric", spec=spec)
    assert data["total"]["value"] == df.country.nunique()
    with pytest.raises(ToolError, match="Distinct counts"):
        run(ctx, "decompose_change", spec=spec, dimensions=["platform"], **PERIODS)
    with pytest.raises(ToolError, match="Unknown metric"):
        run(ctx, "compute_metric", metric="nope")


def test_change_point_and_anomalies(tmp_path: Path) -> None:
    rng = np.random.default_rng(2)
    days = pd.date_range("2026-01-01", periods=120).strftime("%Y-%m-%d")
    sessions = rng.integers(900, 1100, 120)
    rate = np.where(np.arange(120) >= 90, 0.04, 0.06)
    df = pd.DataFrame({"date": days, "sessions": sessions, "signups": rng.binomial(sessions, rate)})
    data = run(ctx_for(df, tmp_path), "detect_anomalies", metric="conversion_rate")
    cp = data["change_point"]
    assert abs((pd.Timestamp(cp["period"]) - pd.Timestamp(days[90])).days) <= 2
    assert cp["relative_change"] == pytest.approx(-1 / 3, abs=0.06)
    assert data["anomaly_count"] >= 1
    with pytest.raises(ToolError, match="periods"):
        run(ctx_for(df.head(20), tmp_path), "detect_anomalies", metric="conversion_rate")
