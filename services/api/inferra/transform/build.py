"""Build a data model in the sandbox: create each node as a table in dependency order, then run the
tests. A failing node skips its descendants instead of stopping the build, so one result shows
everything that is wrong."""

import time
from dataclasses import dataclass
from typing import Any

import duckdb

from inferra.datasources.base import DataSource, QueryError
from inferra.datasources.files import json_safe
from inferra.transform.project import (
    NodeSpec,
    ProjectError,
    TestSpec,
    compile_project,
    quote,
    undeclared_references,
)

PREVIEW_ROWS = 5
SAMPLE_FAILURES = 5


@dataclass
class BuildOutput:
    result: dict[str, Any]
    tables: dict[str, Any]  # node name -> Arrow table, only when `keep` asked for it


def _test_sql(table: str, column: str | None, test: TestSpec, grain: list[str] | None = None) -> str:
    """SQL returning the failing values (one row per failure group)."""
    t = quote(table)
    if grain is not None:
        cols = ", ".join(quote(c) for c in grain)
        return f"SELECT {cols}, count(*) AS n_rows FROM {t} GROUP BY {cols} HAVING count(*) > 1"
    c = quote(column or "")
    if test.type == "unique":
        return (
            f"SELECT {c}, count(*) AS n_rows FROM {t} WHERE {c} IS NOT NULL GROUP BY {c} HAVING count(*) > 1"
        )
    if test.type == "not_null":
        return f"SELECT * FROM {t} WHERE {c} IS NULL"
    if test.type == "accepted_values":
        values = ", ".join("'" + str(v).replace("'", "''") + "'" for v in test.values or [])
        return (
            f"SELECT {c}, count(*) AS n_rows FROM {t} WHERE {c} IS NOT NULL "
            f"AND CAST({c} AS VARCHAR) NOT IN ({values}) GROUP BY {c}"
        )
    parent, key = quote(test.to or ""), quote(test.field or "")
    return (
        f"SELECT child.{c}, count(*) AS n_rows FROM {t} AS child LEFT JOIN {parent} AS parent "
        f"ON child.{c} = parent.{key} WHERE child.{c} IS NOT NULL AND parent.{key} IS NULL GROUP BY child.{c}"
    )


def _describe(test: TestSpec, column: str | None, grain: list[str] | None) -> str:
    if grain is not None:
        return f"one row per {', '.join(grain)}"
    if test.type == "relationships":
        return f"{column} exists in {test.to}.{test.field}"
    if test.type == "accepted_values":
        return f"{column} in ({', '.join(test.values or [])})"
    return f"{column} is {test.type.replace('_', ' ')}"


def _tests_for(node: NodeSpec) -> list[tuple[str | None, TestSpec, list[str] | None]]:
    tests: list[tuple[str | None, TestSpec, list[str] | None]] = []
    if node.grain:
        tests.append((None, TestSpec(type="unique"), list(node.grain)))
    for col in node.columns:
        tests += [(col.name, t, None) for t in col.tests]
    return tests


def build_project(
    source: DataSource,
    raw_nodes: list[NodeSpec],
    *,
    name: str = "",
    description: str = "",
    keep: set[str] | None = None,
) -> BuildOutput:
    """Compile and build every node, run every test, and summarize. Raises ProjectError when the
    project cannot compile at all."""
    source_tables = {t.name: t for t in source.list_tables()}
    project = compile_project(raw_nodes, set(source_tables))
    nodes_out: list[dict[str, Any]] = []
    tests_out: list[dict[str, Any]] = []
    tables: dict[str, Any] = {}
    status: dict[str, str] = {}
    known = set(source_tables) | {n.name for n in project.nodes}
    started = time.perf_counter()

    # Parse table names on an empty connection: one that has the views resolves them away.
    parser = duckdb.connect()
    with source.session() as con:
        for node in project.nodes:
            compiled = project.compiled[node.name]
            entry: dict[str, Any] = {
                "name": node.name,
                "layer": node.layer,
                "description": node.description,
                "materialized": node.materialized,
                "depends_on": compiled.sources + compiled.refs,
                "sql": node.sql,
                "grain": node.grain,
            }
            failed_parents = [p for p in compiled.refs if status.get(p) != "ok"]
            if failed_parents:
                status[node.name] = "skipped"
                nodes_out.append(
                    {**entry, "status": "skipped", "error": f"Upstream failed: {', '.join(failed_parents)}"}
                )
                continue
            try:
                sql = source.validated(con, compiled.sql)
                undeclared = undeclared_references(parser.get_table_names(sql), compiled, known)
                if undeclared:
                    raise QueryError(
                        "Reference these through {{ ref() }} or {{ source() }}: " + ", ".join(undeclared)
                    )
                con.execute(f"CREATE TABLE {quote(node.name)} AS {sql}")
                described = con.execute(f"DESCRIBE {quote(node.name)}").fetchall()
                columns = [{"name": r[0], "type": r[1]} for r in described]
                row_count = con.execute(f"SELECT count(*) FROM {quote(node.name)}").fetchone()[0]  # type: ignore[index]
                cursor = con.execute(f"SELECT * FROM {quote(node.name)} LIMIT {PREVIEW_ROWS}")
                names = [d[0] for d in cursor.description or []]
                preview = [
                    {k: json_safe(v) for k, v in zip(names, row, strict=True)} for row in cursor.fetchall()
                ]
            except (QueryError, duckdb.Error) as exc:
                status[node.name] = "error"
                nodes_out.append({**entry, "status": "error", "error": str(exc).split("\n")[0]})
                continue
            documented = {c.name: c.description for c in node.columns}
            missing = [c for c in documented if c not in {col["name"] for col in columns}]
            for col in columns:
                col["description"] = documented.get(col["name"], "")
            status[node.name] = "ok"
            if keep and node.name in keep:
                arrow = con.execute(f"SELECT * FROM {quote(node.name)}").arrow()
                tables[node.name] = arrow.read_all() if hasattr(arrow, "read_all") else arrow
            nodes_out.append(
                {
                    **entry,
                    "status": "ok",
                    "row_count": int(row_count),
                    "columns": columns,
                    "preview": preview,
                    "undocumented_columns": [c["name"] for c in columns if c["name"] not in documented],
                    "missing_columns": missing,
                }
            )

        for node in project.nodes:
            for column, test, grain in _tests_for(node):
                test_id = (
                    f"{node.name}.grain"
                    if grain is not None
                    else f"{node.name}.{column}.{test.type}" + (f"_{test.to}" if test.to else "")
                )
                item: dict[str, Any] = {
                    "id": test_id,
                    "node": node.name,
                    "column": column,
                    "columns": grain,
                    "test": "unique_combination" if grain is not None and len(grain) > 1 else test.type,
                    "description": _describe(test, column, grain),
                    "severity": test.severity,
                }
                if status.get(node.name) != "ok" or (
                    test.type == "relationships" and test.to in status and status[test.to] != "ok"
                ):
                    tests_out.append({**item, "status": "skipped", "failures": None})
                    continue
                sql = _test_sql(node.name, column, test, grain)
                try:
                    failures = con.execute(f"SELECT count(*) FROM ({sql})").fetchone()[0]  # type: ignore[index]
                    sample_cursor = con.execute(f"{sql} LIMIT {SAMPLE_FAILURES}")
                    names = [d[0] for d in sample_cursor.description or []]
                    sample = [
                        {k: json_safe(v) for k, v in zip(names, row, strict=True)}
                        for row in sample_cursor.fetchall()
                    ]
                except duckdb.Error as exc:
                    tests_out.append(
                        {
                            **item,
                            "status": "error",
                            "failures": None,
                            "error": str(exc).split("\n")[0],
                            "sql": sql,
                        }
                    )
                    continue
                state = "pass" if failures == 0 else ("warn" if test.severity == "warn" else "fail")
                tests_out.append(
                    {**item, "status": state, "failures": int(failures), "sample": sample, "sql": sql}
                )

    parser.close()
    summary = {
        "nodes": len(nodes_out),
        "nodes_built": sum(1 for n in nodes_out if n["status"] == "ok"),
        "nodes_failed": sum(1 for n in nodes_out if n["status"] == "error"),
        "nodes_skipped": sum(1 for n in nodes_out if n["status"] == "skipped"),
        "tests": len(tests_out),
        "tests_passed": sum(1 for t in tests_out if t["status"] == "pass"),
        "tests_failed": sum(1 for t in tests_out if t["status"] in {"fail", "error"}),
        "tests_warned": sum(1 for t in tests_out if t["status"] == "warn"),
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }
    source_out = [
        {
            "name": s,
            "row_count": source_tables[s].row_count,
            "columns": len(source_tables[s].columns),
        }
        for s in project.sources
    ]
    result = {
        "name": name,
        "description": description,
        "sources": source_out,
        "nodes": nodes_out,
        "edges": project.edges,
        "tests": tests_out,
        "summary": summary,
        "tests_failed": summary["tests_failed"],
        "build_ok": summary["nodes_failed"] == 0 and summary["nodes_skipped"] == 0,
    }
    return BuildOutput(result=result, tables=tables)


__all__ = ["BuildOutput", "ProjectError", "build_project"]
