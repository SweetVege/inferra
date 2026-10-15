"""Data modeling tools: discover keys and entities, find joins, propose a star schema, and build
and test a model. Building records a draft data model that people review, edit, and publish."""

from typing import Any

from pydantic import BaseModel, Field

from inferra.datasources.base import QueryError
from inferra.tools.base import (
    ArtifactDraft,
    ProjectDraft,
    ToolContext,
    ToolError,
    ToolOutput,
    round_floats,
    tool,
)
from inferra.tools.profiling import _table
from inferra.transform.build import build_project
from inferra.transform.design import analyze_table, low_cardinality_values, suggest_star_schema
from inferra.transform.project import NodeSpec, ProjectError, quote

MAX_PAIRS = 40


class AnalyzeKeysInput(BaseModel):
    table: str


@tool(
    "analyze_keys",
    "Find a table's grain (unique key, including composite keys), exact duplicate rows, entities "
    "(identifier columns that repeat) with the attributes each one determines, attributes that are "
    "not stable for their entity, and measures. Creates a 'keys' artifact.",
    AnalyzeKeysInput,
)
def analyze_keys(ctx: ToolContext, inp: AnalyzeKeysInput) -> ToolOutput:
    _table(ctx.source, inp.table)
    try:
        analysis = round_floats(analyze_table(ctx.source, inp.table))
    except QueryError as exc:
        raise ToolError(str(exc)) from exc
    compact = {k: v for k, v in analysis.items() if k != "columns"}
    return ToolOutput(
        result=compact,
        artifact=ArtifactDraft("keys", f"Keys and entities: {inp.table}", analysis),
    )


class RelationshipsInput(BaseModel):
    tables: list[str] = Field(min_length=2, max_length=8)


def _pairs(columns: dict[str, list[str]]) -> list[tuple[str, str, str, str]]:
    """Column pairs worth testing as joins: same name, or `<table>_id` against `id`."""
    pairs = []
    tables = list(columns)
    for i, a in enumerate(tables):
        for b in tables[i + 1 :]:
            for col in columns[a]:
                if col in columns[b]:
                    pairs.append((a, col, b, col))
            for x, y in ((a, b), (b, a)):
                stem = y.rstrip("s")
                for col in columns[x]:
                    if col in {f"{y}_id", f"{stem}_id"} and "id" in columns[y]:
                        pairs.append((x, col, y, "id"))
    return pairs[:MAX_PAIRS]


@tool(
    "find_relationships",
    "Find join keys between tables: for columns that share a name (or <table>_id against id), the "
    "share of values found on the other side and whether that side is unique, which tells "
    "one-to-many from many-to-many joins. Creates a 'relationships' artifact.",
    RelationshipsInput,
)
def find_relationships(ctx: ToolContext, inp: RelationshipsInput) -> ToolOutput:
    columns = {t: [c["name"] for c in _table(ctx.source, t).columns] for t in inp.tables}
    found = []
    q = quote
    for a, ca, b, cb in _pairs(columns):
        sql = (
            f"WITH l AS (SELECT DISTINCT {q(ca)} AS k FROM {q(a)} WHERE {q(ca)} IS NOT NULL), "
            f"r AS (SELECT DISTINCT {q(cb)} AS k FROM {q(b)} WHERE {q(cb)} IS NOT NULL) "
            f"SELECT (SELECT count(*) FROM l) AS left_values, (SELECT count(*) FROM r) AS right_values, "
            f"(SELECT count(*) FROM l JOIN r USING (k)) AS shared, "
            f"(SELECT count(*) = count(DISTINCT {q(ca)}) FROM {q(a)} WHERE {q(ca)} IS NOT NULL) "
            "AS left_unique, "
            f"(SELECT count(*) = count(DISTINCT {q(cb)}) FROM {q(b)} WHERE {q(cb)} IS NOT NULL) "
            "AS right_unique"
        )
        try:
            row = ctx.source.query(sql, limit=1).rows[0]
        except QueryError:
            continue  # incompatible types
        if not row["shared"]:
            continue
        cardinality = {
            (True, True): "one-to-one",
            (False, True): "many-to-one",
            (True, False): "one-to-many",
            (False, False): "many-to-many",
        }[(bool(row["left_unique"]), bool(row["right_unique"]))]
        found.append(
            {
                "left": f"{a}.{ca}",
                "right": f"{b}.{cb}",
                "cardinality": cardinality,
                "left_coverage": row["shared"] / row["left_values"],
                "right_coverage": row["shared"] / row["right_values"],
                "orphan_left_values": row["left_values"] - row["shared"],
            }
        )
    found.sort(key=lambda r: -r["left_coverage"])
    data = round_floats({"tables": inp.tables, "relationships": found})
    return ToolOutput(
        result=data,
        artifact=ArtifactDraft("relationships", f"Relationships: {', '.join(inp.tables)}", data),
    )


class SuggestInput(BaseModel):
    table: str = Field(description="A flat table to turn into a star schema.")


@tool(
    "suggest_star_schema",
    "Draft a tested star schema for one flat table: a staging node, a dimension per entity, a date "
    "dimension, and a fact at the table's grain, with unique, not_null, relationships, and "
    "accepted_values tests. Returns the nodes to review, adjust, and pass to build_data_model. "
    "Does not build anything.",
    SuggestInput,
)
def suggest(ctx: ToolContext, inp: SuggestInput) -> ToolOutput:
    _table(ctx.source, inp.table)
    try:
        analysis = analyze_table(ctx.source, inp.table)
        nodes = suggest_star_schema(analysis, low_cardinality_values(ctx.source, analysis))
    except QueryError as exc:
        raise ToolError(str(exc)) from exc
    return ToolOutput(result={"table": inp.table, "grain": analysis["grain"], "nodes": nodes})


class BuildInput(BaseModel):
    name: str = Field(min_length=1, max_length=200, description="Name of the data model.")
    description: str = Field(default="", max_length=2000)
    nodes: list[NodeSpec] = Field(min_length=1, max_length=40)


def _compact(result: dict[str, Any]) -> dict[str, Any]:
    """What the agent needs to iterate: statuses, row counts, and failing tests with samples."""
    return {
        "summary": result["summary"],
        "nodes": [
            {k: n.get(k) for k in ("name", "status", "row_count", "error", "missing_columns")}
            for n in result["nodes"]
        ],
        "failing_tests": [
            {k: t.get(k) for k in ("id", "description", "status", "failures", "error")}
            | {"sample": (t.get("sample") or [])[:2]}
            for t in result["tests"]
            if t["status"] not in {"pass"}
        ],
    }


@tool(
    "build_data_model",
    "Build a data model in the sandbox and run its tests. Each node is one SELECT that reads "
    "datasets with {{ source('table') }} and other nodes with {{ ref('node') }}. Nodes build in "
    "dependency order; a failing node skips its descendants. Saves the model as a draft for review "
    "(calling again in the same run updates it). Creates a 'data_model' artifact with lineage, "
    "row counts, and test results.",
    BuildInput,
)
def build_data_model(ctx: ToolContext, inp: BuildInput) -> ToolOutput:
    try:
        built = build_project(ctx.source, inp.nodes, name=inp.name, description=inp.description)
    except ProjectError as exc:
        raise ToolError(str(exc)) from exc
    result = round_floats(built.result)
    nodes = [n.model_dump() for n in inp.nodes]
    return ToolOutput(
        result=_compact(result),
        artifact=ArtifactDraft("data_model", f"Data model: {inp.name}", result),
        code="\n\n".join(f"-- {n.name}\n{n.sql}" for n in inp.nodes),
        project=ProjectDraft(name=inp.name, description=inp.description, nodes=nodes, build=result),
    )
