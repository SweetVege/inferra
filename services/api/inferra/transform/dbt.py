"""Export a data model as a runnable dbt project (dbt-duckdb), with sources, tests, and docs.

The node SQL is already written with ref() and source(), so export is mostly file layout: one .sql
file per node, schema YAML with descriptions and tests, and the source Parquet files so that
`dbt build` works straight after unzipping."""

import io
import re
import zipfile
from pathlib import Path
from typing import Any

import yaml

from inferra.transform.project import SOURCE, NodeSpec

SOURCE_NAME = "inferra"
FOLDERS = {"staging": "staging", "dimension": "marts", "fact": "marts", "mart": "marts"}


# A grain test without a package dependency, so `dbt build` works offline.
COMBINATION_TEST = """{% test unique_combination_of_columns(model, combination_of_columns) %}
select {{ combination_of_columns | join(', ') }}, count(*) as n_rows
from {{ model }}
group by {{ combination_of_columns | join(', ') }}
having count(*) > 1
{% endtest %}
"""


def slug(name: str) -> str:
    out = re.sub(r"[^a-z0-9_]+", "_", name.lower()).strip("_")
    return out if out and out[0].isalpha() else f"inferra_{out or 'project'}"


def _dbt_sql(node: NodeSpec) -> str:
    body = SOURCE.sub(
        lambda m: f"{{{{ source('{SOURCE_NAME}', '{m.group(1)}') }}}}", node.sql.strip().rstrip(";")
    )
    return f"{{{{ config(materialized='{node.materialized}') }}}}\n\n{body}\n"


def _test(test: Any) -> Any:
    arguments: dict[str, Any] = {}
    if test.type == "accepted_values":
        arguments["values"] = list(test.values or [])
    if test.type == "relationships":
        arguments["to"] = f"ref('{test.to}')"
        arguments["field"] = test.field
    spec: dict[str, Any] = {"arguments": arguments} if arguments else {}
    if test.severity == "warn":
        spec["config"] = {"severity": "warn"}
    return {test.type: spec} if spec else test.type


def _model_yaml(node: NodeSpec) -> tuple[dict[str, Any], bool]:
    uses_macro = False
    columns = []
    for col in node.columns:
        entry: dict[str, Any] = {"name": col.name}
        if col.description:
            entry["description"] = col.description
        tests = [_test(t) for t in col.tests]
        if len(node.grain) == 1 and node.grain[0] == col.name and "unique" not in tests:
            tests.insert(0, "unique")
        if tests:
            entry["data_tests"] = tests
        columns.append(entry)
    if len(node.grain) == 1 and node.grain[0] not in {c.name for c in node.columns}:
        columns.insert(0, {"name": node.grain[0], "data_tests": ["unique"]})
    model: dict[str, Any] = {"name": node.name}
    if node.description:
        model["description"] = node.description
    if len(node.grain) > 1:
        uses_macro = True
        model["data_tests"] = [
            {"unique_combination_of_columns": {"arguments": {"combination_of_columns": node.grain}}}
        ]
    if columns:
        model["columns"] = columns
    return model, uses_macro


def _yaml(data: Any) -> str:
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100)


def export_project(
    name: str,
    description: str,
    nodes: list[NodeSpec],
    sources: list[dict[str, Any]],
    data_files: dict[str, Path] | None = None,
) -> bytes:
    """Build the zip. `sources` lists {name, description, row_count}; `data_files` maps source names
    to Parquet files to bundle under data/."""
    project = slug(name)
    root = f"{project}/"
    buffer = io.BytesIO()
    by_folder: dict[str, list[dict[str, Any]]] = {}
    uses_macro = False
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            root + "dbt_project.yml",
            _yaml(
                {
                    "name": project,
                    "version": "1.0.0",
                    "config-version": 2,
                    "profile": project,
                    "model-paths": ["models"],
                    "models": {
                        project: {"staging": {"+materialized": "view"}, "marts": {"+materialized": "table"}}
                    },
                }
            ),
        )
        zf.writestr(
            root + "profiles.yml",
            _yaml(
                {
                    project: {
                        "target": "dev",
                        "outputs": {"dev": {"type": "duckdb", "path": f"{project}.duckdb", "threads": 4}},
                    }
                }
            ),
        )
        source_tables = []
        for s in sources:
            table: dict[str, Any] = {"name": s["name"]}
            if s.get("description"):
                table["description"] = s["description"]
            table["config"] = {"external_location": f"data/{s['name']}.parquet"}
            source_tables.append(table)
        zf.writestr(
            root + "models/sources.yml",
            _yaml(
                {
                    "version": 2,
                    "sources": [
                        {
                            "name": SOURCE_NAME,
                            "description": "Datasets exported from Inferra as Parquet files.",
                            "tables": source_tables,
                        }
                    ],
                }
            ),
        )
        for node in nodes:
            folder = FOLDERS[node.layer]
            zf.writestr(f"{root}models/{folder}/{node.name}.sql", _dbt_sql(node))
            model, needs_macro = _model_yaml(node)
            uses_macro = uses_macro or needs_macro
            by_folder.setdefault(folder, []).append(model)
        for folder, models in by_folder.items():
            zf.writestr(f"{root}models/{folder}/_{folder}.yml", _yaml({"version": 2, "models": models}))
        if uses_macro:
            zf.writestr(root + "macros/unique_combination_of_columns.sql", COMBINATION_TEST)
        for source_name, path in (data_files or {}).items():
            zf.write(path, f"{root}data/{source_name}.parquet")
        zf.writestr(root + "README.md", _readme(project, name, description, nodes, bool(data_files)))
    return buffer.getvalue()


def _readme(project: str, name: str, description: str, nodes: list[NodeSpec], data: bool) -> str:
    rows = "\n".join(f"| `{n.name}` | {n.layer} | {n.description or ''} |" for n in nodes)
    steps = ["pip install dbt-duckdb", "dbt build --profiles-dir ."]
    data_note = (
        "The source datasets are included under `data/` as Parquet files."
        if data
        else "Place each source dataset under `data/<table>.parquet` before building."
    )
    return f"""# {name}

{description or "A dimensional model exported from Inferra."}

## Run it

```bash
cd {project}
{chr(10).join(steps)}
```

{data_note} The project targets DuckDB; to run it on a warehouse, point `profiles.yml` at your
adapter and replace the `external_location` entries in `models/sources.yml` with real source tables.

## Models

| Model | Layer | Description |
| --- | --- | --- |
{rows}
"""
