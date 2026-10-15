"""General-purpose analysis tools shared by the Data and Analyst agents."""

from typing import Literal

import numpy as np
from pydantic import BaseModel, Field

from inferra.datasources.base import QueryError
from inferra.tools.base import ArtifactDraft, ToolContext, ToolError, ToolOutput, round_floats, tool
from inferra.tools.profiling import _table, column_kind, column_stats


class ListTablesInput(BaseModel):
    pass


@tool(
    "list_tables",
    "List the tables available in this run with their columns, types, and row counts.",
    ListTablesInput,
)
def list_tables(ctx: ToolContext, _args: ListTablesInput) -> ToolOutput:
    tables = [
        {"name": t.name, "row_count": t.row_count, "description": t.description, "columns": t.columns}
        for t in ctx.source.list_tables()
    ]
    return ToolOutput(result={"tables": tables})


class RunSqlInput(BaseModel):
    sql: str = Field(
        description="One read-only DuckDB SELECT statement. Quote identifiers with double quotes."
    )
    title: str = Field(description="Short human-readable title for the result table.")


@tool(
    "run_sql",
    "Run one read-only SELECT query (DuckDB dialect) against the run's tables. Results are "
    "capped at the row limit; aggregate in SQL rather than pulling raw rows. Creates a 'table' "
    "artifact.",
    RunSqlInput,
)
def run_sql(ctx: ToolContext, args: RunSqlInput) -> ToolOutput:
    try:
        res = ctx.source.query(args.sql, limit=ctx.row_limit)
    except QueryError as exc:
        raise ToolError(str(exc)) from exc
    data = round_floats(
        {
            "sql": res.sql,
            "columns": res.columns,
            "rows": res.rows,
            "row_count": len(res.rows),
            "truncated": res.truncated,
        }
    )
    shown = data["rows"][:50]
    result = {**data, "rows": shown, "rows_shown": len(shown)}
    return ToolOutput(
        result=result, artifact=ArtifactDraft(kind="table", title=args.title, data=data), code=res.sql
    )


class SummarizeColumnInput(BaseModel):
    table: str
    column: str


@tool(
    "summarize_column",
    "Distribution summary for one column: missing values, distinct count, and either numeric "
    "statistics with quartiles and a histogram, top categories, or a date range. Creates a "
    "'column_stats' artifact.",
    SummarizeColumnInput,
)
def summarize_column(ctx: ToolContext, args: SummarizeColumnInput) -> ToolOutput:
    info = _table(ctx.source, args.table)
    col = next((c for c in info.columns if c["name"] == args.column), None)
    if col is None:
        raise ToolError(f"Unknown column '{args.column}' in {args.table}.")
    try:
        rows = ctx.source.query(f"SELECT count(*) AS n FROM {ctx.source.quote(args.table)}").rows
        stats = column_stats(ctx.source, args.table, args.column, col["type"], rows[0]["n"])
    except QueryError as exc:
        raise ToolError(str(exc)) from exc
    data = round_floats({"table": args.table, "column": args.column, "row_count": rows[0]["n"], **stats})
    return ToolOutput(
        result=data,
        artifact=ArtifactDraft(kind="column_stats", title=f"{args.table}.{args.column}", data=data),
    )


Agg = Literal["count", "count_distinct", "sum", "avg", "median", "min", "max"]


class Metric(BaseModel):
    agg: Agg
    column: str | None = Field(default=None, description="Omit only for agg='count'.")


class GroupSummaryInput(BaseModel):
    table: str
    group_by: list[str] = Field(description="Zero or more grouping columns.", max_length=3)
    metrics: list[Metric] = Field(min_length=1, max_length=6)
    where: str | None = Field(default=None, description="Optional SQL boolean filter expression.")
    order_by_metric: int | None = Field(
        default=0, description="Index of the metric to sort by (descending); null keeps group order."
    )
    limit: int = Field(default=50, ge=1, le=200)
    title: str


@tool(
    "group_summary",
    "Aggregate metrics (count, count_distinct, sum, avg, median, min, max) by up to three "
    "grouping columns, with each group's share of total count. Creates a 'table' artifact.",
    GroupSummaryInput,
)
def group_summary(ctx: ToolContext, args: GroupSummaryInput) -> ToolOutput:
    q = ctx.source.quote
    _table(ctx.source, args.table)
    selects = [q(g) for g in args.group_by]
    names: list[str] = []
    for m in args.metrics:
        if m.agg == "count" and not m.column:
            expr, name = "count(*)", "count"
        elif not m.column:
            raise ToolError(f"Metric '{m.agg}' needs a column.")
        elif m.agg == "count_distinct":
            expr, name = f"count(DISTINCT {q(m.column)})", f"count_distinct_{m.column}"
        else:
            expr, name = f"{m.agg}({q(m.column)})", f"{m.agg}_{m.column}"
        selects.append(f"{expr} AS {q(name)}")
        names.append(name)
    selects.append("count(*) / sum(count(*)) OVER () AS share_of_rows")
    sql = f"SELECT {', '.join(selects)} FROM {q(args.table)}"
    if args.where:
        sql += f" WHERE {args.where}"
    if args.group_by:
        sql += f" GROUP BY {', '.join(q(g) for g in args.group_by)}"
    if args.order_by_metric is not None and 0 <= args.order_by_metric < len(names):
        sql += f" ORDER BY {q(names[args.order_by_metric])} DESC NULLS LAST"
    elif args.group_by:
        sql += f" ORDER BY {', '.join(q(g) for g in args.group_by)}"
    sql += f" LIMIT {args.limit}"
    return run_sql(ctx, RunSqlInput(sql=sql, title=args.title))


class CorrelationsInput(BaseModel):
    table: str
    columns: list[str] = Field(min_length=2, max_length=12, description="Numeric columns.")


@tool(
    "correlations",
    "Pearson correlations between numeric columns (pairwise complete rows), sorted by absolute "
    "strength. Creates a 'correlations' artifact.",
    CorrelationsInput,
)
def correlations(ctx: ToolContext, args: CorrelationsInput) -> ToolOutput:
    info = _table(ctx.source, args.table)
    types = {c["name"]: c["type"] for c in info.columns}
    for col in args.columns:
        if col not in types:
            raise ToolError(f"Unknown column '{col}'.")
        if column_kind(types[col]) != "numeric":
            raise ToolError(f"Column '{col}' is not numeric.")
    q = ctx.source.quote
    pairs = []
    for i, a in enumerate(args.columns):
        for b in args.columns[i + 1 :]:
            row = ctx.source.query(
                f"""SELECT corr({q(a)}, {q(b)}) AS r, count(*) AS n FROM {q(args.table)}
                    WHERE {q(a)} IS NOT NULL AND {q(b)} IS NOT NULL"""
            ).rows[0]
            r = row["r"]
            if r is not None and not np.isnan(r):
                pairs.append({"a": a, "b": b, "r": r, "n": row["n"]})
    pairs.sort(key=lambda p: abs(p["r"]), reverse=True)
    data = round_floats({"table": args.table, "columns": args.columns, "pairs": pairs}, 4)
    return ToolOutput(
        result=data,
        artifact=ArtifactDraft(kind="correlations", title=f"Correlations in {args.table}", data=data),
    )
