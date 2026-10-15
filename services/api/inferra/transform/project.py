"""A data model project: SQL nodes that reference sources and each other, dbt-style.

Nodes write `{{ source('orders') }}` for an uploaded dataset and `{{ ref('stg_orders') }}` for
another node. Those references define the lineage graph and compile to plain table names, so the
same SQL runs in Inferra's sandbox and exports unchanged to a dbt project."""

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

NAME = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
REF = re.compile(r"\{\{\s*ref\(\s*['\"]([^'\"]+)['\"]\s*\)\s*\}\}")
SOURCE = re.compile(r"\{\{\s*source\(\s*(?:['\"][^'\"]+['\"]\s*,\s*)?['\"]([^'\"]+)['\"]\s*\)\s*\}\}")
JINJA = re.compile(r"\{\{|\{%")
LAYERS = ("staging", "dimension", "fact", "mart")


class ProjectError(Exception):
    """The project cannot compile; the message says what to change."""


class TestSpec(BaseModel):
    type: Literal["unique", "not_null", "accepted_values", "relationships"]
    values: list[str] | None = Field(default=None, description="accepted_values: the allowed values.")
    to: str | None = Field(default=None, description="relationships: the parent node.")
    field: str | None = Field(default=None, description="relationships: the key column in the parent.")
    severity: Literal["error", "warn"] = "error"


class ColumnSpec(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = ""
    tests: list[TestSpec] = Field(default_factory=list)


class NodeSpec(BaseModel):
    name: str = Field(description="Lowercase identifier, e.g. dim_customer.")
    layer: Literal["staging", "dimension", "fact", "mart"]
    description: str = ""
    sql: str = Field(
        min_length=1,
        max_length=20000,
        description="One DuckDB SELECT. Reference datasets as {{ source('table') }} and other nodes "
        "as {{ ref('node') }}.",
    )
    materialized: Literal["table", "view"] = "table"
    grain: list[str] = Field(
        default_factory=list, description="Columns that identify one row; tested for uniqueness."
    )
    columns: list[ColumnSpec] = Field(default_factory=list, description="Documented and tested columns.")

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        if not NAME.match(value):
            raise ValueError("use lowercase letters, digits, and underscores, starting with a letter")
        return value


@dataclass
class Compiled:
    name: str
    sql: str  # executable SQL with references replaced by table names
    refs: list[str]
    sources: list[str]


@dataclass
class CompiledProject:
    nodes: list[NodeSpec]  # in build order
    compiled: dict[str, Compiled]
    sources: list[str]
    edges: list[dict[str, str]] = field(default_factory=list)


def quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def parse_nodes(raw: list[dict[str, Any]]) -> list[NodeSpec]:
    return [NodeSpec.model_validate(n) for n in raw]


def compile_project(nodes: list[NodeSpec], source_tables: set[str]) -> CompiledProject:
    """Resolve references, check names, and order nodes so each runs after its parents."""
    names = [n.name for n in nodes]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    if duplicates:
        raise ProjectError(f"Node names must be unique: {', '.join(duplicates)}.")
    clashes = sorted(set(names) & source_tables)
    if clashes:
        raise ProjectError(f"Node names clash with dataset names: {', '.join(clashes)}. Rename the nodes.")
    by_name = {n.name: n for n in nodes}

    compiled: dict[str, Compiled] = {}
    for node in nodes:
        refs = list(dict.fromkeys(REF.findall(node.sql)))
        sources = list(dict.fromkeys(SOURCE.findall(node.sql)))
        unknown_refs = [r for r in refs if r not in by_name]
        if unknown_refs:
            raise ProjectError(f"{node.name}: ref() to unknown node(s) {', '.join(unknown_refs)}.")
        unknown_sources = [s for s in sources if s not in source_tables]
        if unknown_sources:
            raise ProjectError(
                f"{node.name}: source() to unknown dataset(s) {', '.join(unknown_sources)}. "
                f"Available: {', '.join(sorted(source_tables)) or 'none'}."
            )
        if node.name in refs:
            raise ProjectError(f"{node.name} cannot ref() itself.")
        sql = REF.sub(lambda m: quote(m.group(1)), node.sql)
        sql = SOURCE.sub(lambda m: quote(m.group(1)), sql)
        if JINJA.search(sql):
            raise ProjectError(
                f"{node.name}: only {{{{ ref('...') }}}} and {{{{ source('...') }}}} are supported."
            )
        compiled[node.name] = Compiled(node.name, sql.strip().rstrip(";").strip(), refs, sources)

    order: list[str] = []
    state: dict[str, str] = {}

    def visit(name: str, path: list[str]) -> None:
        if state.get(name) == "done":
            return
        if state.get(name) == "visiting":
            cycle = path[path.index(name) :] + [name]
            raise ProjectError(f"Circular references: {' -> '.join(cycle)}.")
        state[name] = "visiting"
        for parent in compiled[name].refs:
            visit(parent, [*path, name])
        state[name] = "done"
        order.append(name)

    for node in nodes:
        visit(node.name, [])

    for node in nodes:
        for col in node.columns:
            for test in col.tests:
                if test.type == "relationships" and (not test.to or not test.field):
                    raise ProjectError(f"{node.name}.{col.name}: relationships tests need 'to' and 'field'.")
                if test.type == "relationships" and test.to not in by_name and test.to not in source_tables:
                    raise ProjectError(
                        f"{node.name}.{col.name}: relationships test points to unknown '{test.to}'."
                    )
                if test.type == "accepted_values" and not test.values:
                    raise ProjectError(f"{node.name}.{col.name}: accepted_values tests need 'values'.")

    edges = [{"from": p, "to": n} for n in order for p in compiled[n].sources + compiled[n].refs]
    used_sources = sorted({s for c in compiled.values() for s in c.sources})
    return CompiledProject(
        nodes=[by_name[n] for n in order], compiled=compiled, sources=used_sources, edges=edges
    )


def undeclared_references(sql_tables: set[str], compiled: Compiled, known: set[str]) -> list[str]:
    """Tables a node reads without ref()/source(): they would break lineage and the dbt export."""
    declared = {*compiled.refs, *compiled.sources}
    return sorted(t for t in sql_tables if t in known and t not in declared)
