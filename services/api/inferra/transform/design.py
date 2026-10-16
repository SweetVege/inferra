"""Find the structure hidden in a flat table and propose a star schema for it.

Keys come from exact uniqueness checks. Entities are identifier columns that repeat; an attribute
belongs to an entity when the entity's value determines it (a functional dependency), allowing a
small share of violations, which are reported because they usually mean the attribute changes over
time. Entities that determine other entities (an order determines its customer) are transaction
headers and stay in the fact table; the rest become dimensions."""

import re
from typing import Any

from inferra.datasources.base import DataSource, QueryError
from inferra.transform.project import quote

ID_LIKE = re.compile(r"(^id$|_id$|_key$|_sku$|^sku$|_code$|_number$|_no$)", re.I)
NUMERIC = re.compile(
    r"^(TINYINT|SMALLINT|INTEGER|BIGINT|HUGEINT|UTINYINT|USMALLINT|UINTEGER|UBIGINT|FLOAT|DOUBLE|REAL|DECIMAL)",
    re.I,
)
MAX_VIOLATION_SHARE = 0.02
MAX_COMPOSITE_CANDIDATES = 6
ACCEPTED_VALUES_MAX = 10


def _entity_name(column: str) -> str:
    stripped = re.sub(r"(_id|_key|_sku|_code)$", "", column, flags=re.I)
    if column.lower() == "sku":
        return "product"
    return stripped.lower() or column.lower()


def analyze_table(source: DataSource, table: str) -> dict[str, Any]:
    """Keys, duplicate rows, entities with their attributes, unstable attributes, and measures."""
    if table not in {t.name for t in source.list_tables()}:
        raise QueryError(f"Unknown table '{table}'.")
    t = quote(table)
    with source.session() as con:
        described = con.execute(f"DESCRIBE {t}").fetchall()
        types = {r[0]: str(r[1]) for r in described}
        names = list(types)
        rows = int(con.execute(f"SELECT count(*) FROM {t}").fetchone()[0])  # type: ignore[index]
        con.execute(f"CREATE TEMP TABLE __dedup AS SELECT DISTINCT * FROM {t}")
        distinct_rows = int(con.execute("SELECT count(*) FROM __dedup").fetchone()[0])  # type: ignore[index]
        stats_sql = ", ".join(
            f"count(DISTINCT {quote(c)}), count(*) - count({quote(c)}), "
            + (
                f"count(TRY_CAST({quote(c)} AS DATE))"
                if types[c].upper() == "VARCHAR"
                else ("count(*)" if types[c].upper() in {"DATE"} else "0")
            )
            for c in names
        )
        values = con.execute(f"SELECT {stats_sql} FROM __dedup").fetchone() or ()
        columns = []
        for i, c in enumerate(names):
            distinct, nulls, dates = values[3 * i], values[3 * i + 1], values[3 * i + 2]
            non_null = distinct_rows - nulls
            columns.append(
                {
                    "name": c,
                    "type": types[c],
                    "distinct": int(distinct),
                    "nulls": int(nulls),
                    "date_like": types[c].upper() in {"DATE", "TIMESTAMP", "TIMESTAMP WITH TIME ZONE"}
                    or (types[c].upper() == "VARCHAR" and non_null > 0 and dates == non_null),
                    "id_like": bool(ID_LIKE.search(c)),
                    "numeric": bool(NUMERIC.match(types[c])),
                }
            )
        by_name = {c["name"]: c for c in columns}

        single_keys = [[c["name"]] for c in columns if c["distinct"] == distinct_rows and c["nulls"] == 0]
        composite: list[list[str]] = []
        if not single_keys:
            found = _composite_key(con, columns, distinct_rows)
            if found:
                composite.append(found)
        candidate_keys = single_keys + composite
        id_keys = [k for k in candidate_keys if all(by_name[c]["id_like"] for c in k)]
        grain = (id_keys or candidate_keys or [[]])[0]

        entity_cols = [
            c
            for c in columns
            if c["id_like"] and 1 < c["distinct"] < distinct_rows and [c["name"]] not in single_keys
        ]
        dependencies: dict[str, dict[str, dict[str, int]]] = {}
        for entity in entity_cols:
            e = quote(entity["name"])
            others = [c["name"] for c in columns if c["name"] != entity["name"] and c["distinct"] > 1]
            if not others:
                continue
            inner = ", ".join(f"count(DISTINCT {quote(o)}) AS c{j}" for j, o in enumerate(others))
            outer = ", ".join(f"sum(CASE WHEN c{j} > 1 THEN 1 ELSE 0 END)" for j in range(len(others)))
            out = (
                con.execute(
                    f"SELECT count(*), {outer} FROM "
                    f"(SELECT {e}, {inner} FROM __dedup WHERE {e} IS NOT NULL GROUP BY {e})"
                ).fetchone()
                or ()
            )
            groups = int(out[0])
            dependencies[entity["name"]] = {
                o: {"violations": int(v), "groups": groups}
                for o, v in zip(others, out[1:], strict=True)
                if groups and int(v) / groups <= MAX_VIOLATION_SHARE
            }

        def examples(entity: str, column: str) -> list[Any]:
            e, c = quote(entity), quote(column)
            found = con.execute(
                f"SELECT {e} FROM __dedup WHERE {e} IS NOT NULL GROUP BY {e} HAVING count(DISTINCT {c}) > 1 "
                f"ORDER BY {e} LIMIT 3"
            ).fetchall()
            return [r[0] for r in found]

        # Each attribute belongs to the most granular entity that determines it.
        owner: dict[str, str] = {}
        for column in names:
            determining = [e for e, deps in dependencies.items() if column in deps]
            if determining:
                owner[column] = min(determining, key=lambda e: by_name[e]["distinct"])
        entity_names = {e["name"] for e in entity_cols}
        entities = []
        unstable = []
        for entity in entity_cols:
            name = entity["name"]
            attrs = [c for c, o in owner.items() if o == name and c not in entity_names]
            references = [c for c in dependencies.get(name, {}) if c in entity_names]
            role = "transaction" if references else ("dimension" if attrs else "identifier")
            entities.append(
                {
                    "column": name,
                    "name": _entity_name(name),
                    "distinct": entity["distinct"],
                    "role": role,
                    "attributes": attrs,
                    "references": references,
                }
            )
            for attr in attrs:
                dep = dependencies[name][attr]
                if dep["violations"]:
                    unstable.append(
                        {
                            "entity": name,
                            "column": attr,
                            "violations": dep["violations"],
                            "groups": dep["groups"],
                            "share": dep["violations"] / dep["groups"],
                            "examples": examples(name, attr),
                        }
                    )

    dimension_attrs = {a for e in entities if e["role"] == "dimension" for a in e["attributes"]}
    measures = [
        c["name"]
        for c in columns
        if c["numeric"] and not c["id_like"] and c["name"] not in dimension_attrs and c["name"] not in grain
    ]
    return {
        "table": table,
        "row_count": rows,
        "duplicate_rows": rows - distinct_rows,
        "columns": columns,
        "candidate_keys": candidate_keys,
        "grain": grain,
        "entities": entities,
        "unstable_attributes": unstable,
        "measures": measures,
        "date_columns": [c["name"] for c in columns if c["date_like"]],
    }


def _composite_key(con: Any, columns: list[dict[str, Any]], rows: int) -> list[str] | None:
    """Greedy search for a small set of identifying columns: add whichever column separates the
    most rows, then drop any column the rest make redundant."""
    candidates = [
        c["name"]
        for c in columns
        if c["nulls"] == 0
        and c["distinct"] > 1
        and (c["id_like"] or c["date_like"] or (not c["numeric"] and c["distinct"] <= 1000))
    ][: MAX_COMPOSITE_CANDIDATES * 2]

    def count(cols: list[str]) -> int:
        inner = ", ".join(quote(c) for c in cols)
        return int(con.execute(f"SELECT count(DISTINCT ({inner}, NULL)) FROM __dedup").fetchone()[0])

    chosen: list[str] = []
    while len(chosen) < MAX_COMPOSITE_CANDIDATES:
        remaining = [c for c in candidates if c not in chosen]
        if not remaining:
            return None
        exprs = ", ".join(
            f"count(DISTINCT ({', '.join(quote(x) for x in [*chosen, c])}, NULL))" for c in remaining
        )
        counts = con.execute(f"SELECT {exprs} FROM __dedup").fetchone() or ()
        best = max(range(len(remaining)), key=lambda k: counts[k])
        chosen.append(remaining[best])
        if counts[best] == rows:
            for col in list(chosen):
                rest = [c for c in chosen if c != col]
                if len(rest) >= 1 and count(rest) == rows:
                    chosen = rest
            return chosen
    return None


def _select_list(lines: list[str]) -> str:
    return ",\n".join(f"    {line}" for line in lines)


def suggest_star_schema(
    analysis: dict[str, Any], values: dict[str, list[str]] | None = None
) -> list[dict[str, Any]]:
    """A complete, tested starting point: staging, one dimension per entity, a date dimension, and
    a fact at the table's grain. Agents and people refine it from here."""
    table = analysis["table"]
    cols = {c["name"]: c for c in analysis["columns"]}
    stg, fct = f"stg_{table}", f"fct_{table}"
    dims = [e for e in analysis["entities"] if e["role"] == "dimension"]
    dim_attrs = {a for e in dims for a in e["attributes"]}
    unstable = {(u["entity"], u["column"]): u for u in analysis["unstable_attributes"]}
    grain = list(analysis["grain"])

    def is_date(name: str) -> bool:
        return bool(cols[name]["date_like"])

    stg_lines = []
    for name, c in cols.items():
        if c["date_like"] and c["type"].upper() == "VARCHAR":
            stg_lines.append(f"CAST({quote(name)} AS DATE) AS {quote(name)}")
        elif c["date_like"] and c["type"].upper().startswith("TIMESTAMP"):
            stg_lines.append(quote(name))
            stg_lines.append(f"CAST({quote(name)} AS DATE) AS {quote(name + '_date')}")
        else:
            stg_lines.append(quote(name))
    dedup = analysis["duplicate_rows"] > 0
    nodes: list[dict[str, Any]] = [
        {
            "name": stg,
            "layer": "staging",
            "description": f"Typed copy of {table}"
            + (f", with {analysis['duplicate_rows']} exact duplicate rows removed." if dedup else "."),
            "sql": f"SELECT{' DISTINCT' if dedup else ''}\n{_select_list(stg_lines)}\n"
            f"FROM {{{{ source('{table}') }}}}",
            "materialized": "view",
            "grain": grain,
            "columns": [],
        }
    ]

    fact_dates = [
        (n + "_date" if cols[n]["type"].upper().startswith("TIMESTAMP") else n)
        for n in analysis["date_columns"]
        if n not in dim_attrs
    ]
    order_by = fact_dates[0] if fact_dates else None
    for entity in dims:
        key = entity["column"]
        lines = [quote(key)]
        notes = []
        for attr in entity["attributes"]:
            agg = f"arg_max({quote(attr)}, {quote(order_by)})" if order_by else f"min({quote(attr)})"
            lines.append(f"{agg} AS {quote(attr)}")
            if (key, attr) in unstable:
                u = unstable[(key, attr)]
                notes.append(
                    f"{attr} changes for {u['violations']} {entity['name']}s; this keeps the latest value"
                )
        nodes.append(
            {
                "name": f"dim_{entity['name']}",
                "layer": "dimension",
                "description": f"One row per {key}." + (" " + "; ".join(notes) + "." if notes else ""),
                "sql": f"SELECT\n{_select_list(lines)}\nFROM {{{{ ref('{stg}') }}}}\n"
                f"WHERE {quote(key)} IS NOT NULL\nGROUP BY {quote(key)}",
                "materialized": "table",
                "grain": [],
                "columns": [
                    {
                        "name": key,
                        "description": f"Natural key of the {entity['name']}.",
                        "tests": [{"type": "unique"}, {"type": "not_null"}],
                    },
                    *[{"name": a, "description": "", "tests": []} for a in entity["attributes"]],
                ],
            }
        )

    if fact_dates:
        exprs = [quote(d) for d in fact_dates]
        lo = exprs[0] if len(exprs) == 1 else f"least({', '.join(exprs)})"
        hi = exprs[0] if len(exprs) == 1 else f"greatest({', '.join(exprs)})"
        nodes.append(
            {
                "name": "dim_date",
                "layer": "dimension",
                "description": "One row per calendar day across the fact's date range.",
                "sql": (
                    "WITH bounds AS (\n"
                    f"    SELECT min({lo}) AS lo, max({hi}) AS hi FROM {{{{ ref('{stg}') }}}}\n"
                    "),\ndays AS (\n"
                    "    SELECT CAST(unnest(generate_series(lo::TIMESTAMP, hi::TIMESTAMP, INTERVAL 1 DAY)) "
                    "AS DATE) AS date_day\n"
                    "    FROM bounds\n)\n"
                    "SELECT\n"
                    + _select_list(
                        [
                            "date_day",
                            "year(date_day) AS year",
                            "quarter(date_day) AS quarter",
                            "month(date_day) AS month",
                            "monthname(date_day) AS month_name",
                            "CAST(date_trunc('week', date_day) AS DATE) AS week_start",
                            "isodow(date_day) AS day_of_week",
                            "isodow(date_day) >= 6 AS is_weekend",
                        ]
                    )
                    + "\nFROM days"
                ),
                "materialized": "table",
                "grain": [],
                "columns": [
                    {
                        "name": "date_day",
                        "description": "Calendar day.",
                        "tests": [{"type": "unique"}, {"type": "not_null"}],
                    }
                ],
            }
        )

    keys = [
        e["column"]
        for e in analysis["entities"]
        if e["role"] in {"dimension", "identifier"} and e["column"] not in grain
    ]
    transaction_keys = [
        e["column"] for e in analysis["entities"] if e["role"] == "transaction" and e["column"] not in grain
    ]
    used = set(grain) | set(keys) | set(transaction_keys) | dim_attrs | set(analysis["measures"])
    date_cols = [d for d in fact_dates if d not in used]
    degenerate = [n for n, c in cols.items() if n not in used and n not in date_cols and not c["date_like"]]
    measures = [m for m in analysis["measures"] if m not in grain]
    fact_columns: list[dict[str, Any]] = []
    for g in grain:
        fact_columns.append({"name": g, "description": "Part of the grain.", "tests": [{"type": "not_null"}]})
    for d in date_cols:
        tests: list[dict[str, Any]] = [{"type": "not_null"}]
        tests.append({"type": "relationships", "to": "dim_date", "field": "date_day"})
        fact_columns.append({"name": d, "description": "Joins to dim_date.date_day.", "tests": tests})
    for k in keys:
        entity = next(e for e in analysis["entities"] if e["column"] == k)
        tests = [{"type": "not_null"}]
        if entity["role"] == "dimension":
            tests.append({"type": "relationships", "to": f"dim_{entity['name']}", "field": k})
        fact_columns.append({"name": k, "description": f"Key of the {entity['name']}.", "tests": tests})
    for k in transaction_keys:
        fact_columns.append(
            {
                "name": k,
                "description": "Transaction identifier (degenerate dimension).",
                "tests": [{"type": "not_null"}],
            }
        )
    for d in degenerate:
        tests = []
        if values and values.get(d):
            tests.append({"type": "accepted_values", "values": values[d]})
        fact_columns.append(
            {"name": d, "description": "Descriptive attribute kept on the fact.", "tests": tests}
        )
    for m in measures:
        fact_columns.append({"name": m, "description": "Measure.", "tests": []})
    order = [*grain, *transaction_keys, *date_cols, *keys, *degenerate, *measures]
    nodes.append(
        {
            "name": fct,
            "layer": "fact",
            "description": f"One row per {', '.join(grain) or 'source row'} from {table}.",
            "sql": f"SELECT\n{_select_list([quote(c) for c in dict.fromkeys(order)])}\n"
            f"FROM {{{{ ref('{stg}') }}}}",
            "materialized": "table",
            "grain": grain if len(grain) > 1 else [],
            "columns": fact_columns
            if len(grain) != 1
            else [
                {**fact_columns[0], "tests": [{"type": "unique"}, {"type": "not_null"}]},
                *fact_columns[1:],
            ],
        }
    )
    return nodes


def low_cardinality_values(source: DataSource, analysis: dict[str, Any]) -> dict[str, list[str]]:
    """Record the current values of low-cardinality text columns for accepted_values tests."""
    wanted = [
        c["name"]
        for c in analysis["columns"]
        if c["type"].upper() == "VARCHAR" and 1 < c["distinct"] <= ACCEPTED_VALUES_MAX and c["nulls"] == 0
    ]
    found: dict[str, list[str]] = {}
    for column in wanted:
        result = source.query(
            f"SELECT DISTINCT {quote(column)} AS v FROM {quote(analysis['table'])} ORDER BY 1",
            limit=ACCEPTED_VALUES_MAX,
        )
        found[column] = [str(r["v"]) for r in result.rows]
    return found
