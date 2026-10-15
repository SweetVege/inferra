"""Dataset profiling and deterministic data-quality findings."""

import re
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from inferra.datasources.base import DataSource, QueryError
from inferra.tools.base import ArtifactDraft, ToolContext, ToolError, ToolOutput, round_floats, tool

NUMERIC_PREFIXES = (
    "TINYINT",
    "SMALLINT",
    "INTEGER",
    "BIGINT",
    "HUGEINT",
    "UTINYINT",
    "USMALLINT",
    "UINTEGER",
    "UBIGINT",
    "UHUGEINT",
    "FLOAT",
    "DOUBLE",
    "DECIMAL",
    "REAL",
)
MONEY_LIKE = re.compile(r"(amount|price|revenue|cost|qty|quantity|sales|spend)", re.I)
ID_LIKE = re.compile(r"(^id$|_id$|^uuid|_key$)", re.I)


def column_kind(duck_type: str) -> str:
    t = duck_type.upper()
    if t.startswith(NUMERIC_PREFIXES):
        return "numeric"
    if t == "BOOLEAN":
        return "boolean"
    if t.startswith(("DATE", "TIMESTAMP", "TIME")):
        return "temporal"
    if t.startswith("VARCHAR") or t == "UUID" or t.startswith("ENUM"):
        return "categorical"
    return "other"


def _table(source: DataSource, name: str) -> Any:
    for info in source.list_tables():
        if info.name == name:
            return info
    known = ", ".join(t.name for t in source.list_tables()) or "none"
    raise ToolError(f"Unknown table '{name}'. Available tables: {known}.")


def _one(source: DataSource, sql: str) -> dict[str, Any]:
    return source.query(sql, limit=1).rows[0]


def column_stats(
    source: DataSource, table: str, column: str, col_type: str, row_count: int
) -> dict[str, Any]:
    q = source.quote
    c, t = q(column), q(table)
    kind = column_kind(col_type)
    base = _one(
        source,
        f"SELECT count(*) - count({c}) AS null_count, count(DISTINCT {c}) AS distinct_count FROM {t}",
    )
    stats: dict[str, Any] = {
        "type": col_type,
        "kind": kind,
        "null_count": base["null_count"],
        "null_rate": (base["null_count"] / row_count) if row_count else 0.0,
        "distinct_count": base["distinct_count"],
    }
    if kind == "numeric":
        num = _one(
            source,
            f"""SELECT min({c})::DOUBLE AS min, max({c})::DOUBLE AS max, avg({c})::DOUBLE AS mean,
                stddev_samp({c})::DOUBLE AS std,
                quantile_cont({c}, 0.25)::DOUBLE AS p25, quantile_cont({c}, 0.5)::DOUBLE AS median,
                quantile_cont({c}, 0.75)::DOUBLE AS p75,
                count(*) FILTER (WHERE {c} = 0) AS zero_count,
                count(*) FILTER (WHERE {c} < 0) AS negative_count
              FROM {t}""",
        )
        stats.update(num)
        if num["p25"] is not None and num["p75"] is not None:
            iqr = num["p75"] - num["p25"]
            lo, hi = num["p25"] - 1.5 * iqr, num["p75"] + 1.5 * iqr
            out = _one(
                source,
                f"SELECT count(*) AS n FROM {t} WHERE {c} < {lo!r} OR {c} > {hi!r}",
            )
            stats["outlier_count"] = out["n"]
            non_null = row_count - stats["null_count"]
            stats["outlier_rate"] = out["n"] / non_null if non_null else 0.0
        if num["min"] is not None and num["max"] is not None and num["max"] > num["min"]:
            width = (num["max"] - num["min"]) / 10
            hist = source.query(
                f"""SELECT least(floor(({c} - {num["min"]!r}) / {width!r}), 9)::INTEGER AS bin,
                    count(*) AS n FROM {t} WHERE {c} IS NOT NULL GROUP BY 1 ORDER BY 1"""
            ).rows
            counts = {r["bin"]: r["n"] for r in hist}
            stats["histogram"] = [
                {
                    "bin_start": num["min"] + i * width,
                    "bin_end": num["min"] + (i + 1) * width,
                    "count": counts.get(i, 0),
                }
                for i in range(10)
            ]
    elif kind in {"categorical", "boolean"}:
        top = source.query(
            f"""SELECT {c}::VARCHAR AS value, count(*) AS count FROM {t}
                WHERE {c} IS NOT NULL GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 10"""
        ).rows
        stats["top_values"] = [{**r, "share": r["count"] / row_count if row_count else 0.0} for r in top]
    elif kind == "temporal":
        rng = _one(
            source,
            f"""SELECT min({c})::VARCHAR AS min, max({c})::VARCHAR AS max,
                count(*) FILTER (WHERE {c}::TIMESTAMP > now()::TIMESTAMP) AS future_count
              FROM {t}""",
        )
        stats.update(rng)
    return stats


def quality_findings(profile: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    rows = profile["row_count"]

    def add(severity: str, column: str | None, issue: str, detail: str, metric: str, value: Any) -> None:
        findings.append(
            {
                "severity": severity,
                "column": column,
                "issue": issue,
                "detail": detail,
                "metric": metric,
                "value": value,
            }
        )

    if rows == 0:
        add("high", None, "empty_table", "The table has no rows.", "row_count", 0)
        return findings
    if profile["duplicate_rows"]:
        add(
            "medium",
            None,
            "duplicate_rows",
            "Some rows are exact duplicates.",
            "duplicate_rows",
            profile["duplicate_rows"],
        )
    for name, col in profile["columns"].items():
        rate = col["null_rate"]
        if rate >= 0.5:
            add(
                "high",
                name,
                "mostly_missing",
                "Half or more of the values are missing.",
                f"columns.{name}.null_rate",
                rate,
            )
        elif rate >= 0.05:
            add(
                "medium",
                name,
                "missing_values",
                "A notable share of values is missing.",
                f"columns.{name}.null_rate",
                rate,
            )
        non_null = rows - col["null_count"]
        if non_null > 0 and col["distinct_count"] == 1:
            add(
                "low",
                name,
                "constant",
                "Every non-missing value is identical.",
                f"columns.{name}.distinct_count",
                1,
            )
        if col["kind"] == "numeric":
            if col.get("outlier_rate", 0) >= 0.05:
                add(
                    "low",
                    name,
                    "outliers",
                    "Many values fall outside 1.5×IQR of the quartiles.",
                    f"columns.{name}.outlier_rate",
                    col["outlier_rate"],
                )
            if col.get("negative_count") and MONEY_LIKE.search(name):
                add(
                    "medium",
                    name,
                    "negative_amounts",
                    "An amount-like column contains negative values.",
                    f"columns.{name}.negative_count",
                    col["negative_count"],
                )
        if col["kind"] == "temporal" and col.get("future_count"):
            add(
                "medium",
                name,
                "future_dates",
                "Some dates are in the future.",
                f"columns.{name}.future_count",
                col["future_count"],
            )
        if (
            col["kind"] in {"categorical", "numeric"}
            and non_null == rows
            and col["distinct_count"] == rows
            and ID_LIKE.search(name)
        ):
            add(
                "info",
                name,
                "identifier",
                "Values are unique; this looks like a row identifier.",
                f"columns.{name}.distinct_count",
                col["distinct_count"],
            )
    return findings


def profile_table(source: DataSource, table: str) -> dict[str, Any]:
    info = _table(source, table)
    q = source.quote
    try:
        row_count = _one(source, f"SELECT count(*) AS n FROM {q(table)}")["n"]
        dup = _one(
            source,
            f"SELECT {row_count} - count(*) AS n FROM (SELECT DISTINCT * FROM {q(table)})",
        )["n"]
        columns = {
            col["name"]: column_stats(source, table, col["name"], col["type"], row_count)
            for col in info.columns
        }
    except QueryError as exc:
        raise ToolError(str(exc)) from exc
    profile = {
        "table": table,
        "row_count": row_count,
        "column_count": len(info.columns),
        "duplicate_rows": dup,
        "columns": columns,
        "profiled_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    profile["findings"] = quality_findings(profile)
    return round_floats(profile)


def _compact_profile(profile: dict[str, Any]) -> dict[str, Any]:
    """What the agent reads: everything except histograms."""
    columns = {
        name: {k: v for k, v in col.items() if k != "histogram"} for name, col in profile["columns"].items()
    }
    return {**profile, "columns": columns}


class ProfileInput(BaseModel):
    table: str = Field(description="Table name exactly as listed by list_tables.")


@tool(
    "profile_dataset",
    "Profile a table: row count, duplicates, and per-column types, missing values, distinct "
    "counts, numeric distributions, top categories, date ranges, plus data-quality findings. "
    "Creates a 'profile' artifact.",
    ProfileInput,
)
def profile_dataset(ctx: ToolContext, args: ProfileInput) -> ToolOutput:
    profile = profile_table(ctx.source, args.table)
    return ToolOutput(
        result=_compact_profile(profile),
        artifact=ArtifactDraft(kind="profile", title=f"Profile of {args.table}", data=profile),
        code=f"-- profile of {args.table}: per-column aggregates over SELECT * FROM {args.table}",
    )
