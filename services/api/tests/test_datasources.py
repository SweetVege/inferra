from pathlib import Path

import pandas as pd
import pytest

from inferra import storage
from inferra.datasources.base import QueryError, TableInfo
from inferra.datasources.files import FileSource, convert_to_parquet, to_table_name
from inferra.tools.profiling import profile_table


def make_source(csv: Path, workspace: str = "ws1") -> FileSource:
    key = f"workspaces/{workspace}/orders.parquet"
    rows, columns = convert_to_parquet(csv, storage.local_path(key))
    return FileSource(workspace, [(TableInfo("orders", columns, rows), key)])


def test_table_names_are_sanitized() -> None:
    assert to_table_name("Q3 Sales (final).csv") == "q3_sales_final"
    assert to_table_name("2024.csv") == "t_2024"


def test_select_queries_work(orders_csv: Path) -> None:
    source = make_source(orders_csv)
    res = source.query("SELECT region, count(*) AS n FROM orders GROUP BY 1 ORDER BY 1")
    assert [r["region"] for r in res.rows] == ["East", "North", "South", "West"]
    assert sum(r["n"] for r in res.rows) == 2412


def test_row_limit_truncates(orders_csv: Path) -> None:
    res = make_source(orders_csv).query("SELECT * FROM orders", limit=5)
    assert len(res.rows) == 5 and res.truncated


@pytest.mark.parametrize(
    "sql",
    [
        "DROP VIEW orders",
        "SELECT 1; SELECT 2",
        "COPY orders TO '/tmp/leak.csv'",
        "SELECT * FROM read_csv('/etc/passwd')",
        "INSTALL httpfs",
    ],
)
def test_unsafe_sql_is_rejected(orders_csv: Path, sql: str) -> None:
    with pytest.raises(QueryError):
        make_source(orders_csv).query(sql)


def test_other_workspaces_are_not_readable(orders_csv: Path) -> None:
    other = make_source(orders_csv, workspace="ws2")
    path = storage.local_path("workspaces/ws2/orders.parquet")
    with pytest.raises(QueryError):
        make_source(orders_csv, workspace="ws1").query(f"SELECT * FROM read_parquet('{path}')")
    assert other.query("SELECT count(*) AS n FROM orders").rows[0]["n"] == 2412


def test_profile_matches_pandas(orders_csv: Path) -> None:
    profile = profile_table(make_source(orders_csv), "orders")
    frame = pd.read_csv(orders_csv)
    assert profile["row_count"] == len(frame)
    assert profile["duplicate_rows"] == int(frame.duplicated().sum())
    amount = profile["columns"]["amount"]
    assert amount["kind"] == "numeric"
    assert amount["mean"] == pytest.approx(frame["amount"].mean(), rel=1e-6)
    assert amount["median"] == pytest.approx(frame["amount"].median(), rel=1e-6)
    assert amount["null_count"] == 0
    rating = profile["columns"]["rating"]
    assert rating["null_count"] == int(frame["rating"].isna().sum())
    assert sum(b["count"] for b in amount["histogram"]) == len(frame)
    region = profile["columns"]["region"]
    assert {v["value"] for v in region["top_values"]} == {"North", "South", "East", "West"}

    issues = {(f["issue"], f["column"]) for f in profile["findings"]}
    assert ("duplicate_rows", None) in issues
    assert ("missing_values", "rating") in issues
    assert ("negative_amounts", "amount") in issues
