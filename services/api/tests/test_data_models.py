"""Data modeling: key discovery, star schema design, builds with tests, publishing, dbt export,
and the model_data workflow end to end."""

import io
import zipfile
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from inferra.datasources.base import TableInfo
from inferra.datasources.files import FileSource, convert_to_parquet
from inferra.llm.demo_script import demo_script
from inferra.runtime.queue import drain
from inferra.transform.build import build_project
from inferra.transform.design import analyze_table, low_cardinality_values, suggest_star_schema
from inferra.transform.project import ProjectError, compile_project, parse_nodes
from tests.test_runs_e2e import login, upload, use_llm

SAMPLES = Path(__file__).resolve().parents[3] / "samples"


def source_for(*names: str) -> FileSource:
    from inferra import storage

    tables = []
    for name in names:
        key = f"workspaces/w1/{name}.parquet"
        rows, cols = convert_to_parquet(SAMPLES / f"{name}.csv", storage.local_path(key))
        tables.append((TableInfo(name, cols, rows), key))
    return FileSource("w1", tables)


@pytest.fixture
def client() -> TestClient:
    from inferra.main import create_app

    return TestClient(create_app())


def test_analysis_finds_grain_entities_and_unstable_attributes() -> None:
    a = analyze_table(source_for("retail_order_lines"), "retail_order_lines")
    assert a["grain"] == ["order_id", "line_number"]
    roles = {e["column"]: e for e in a["entities"]}
    assert roles["order_id"]["role"] == "transaction" and "customer_id" in roles["order_id"]["references"]
    assert roles["customer_id"]["role"] == "dimension"
    assert set(roles["product_sku"]["attributes"]) == {
        "product_name",
        "product_category",
        "product_brand",
        "unit_list_price",
    }
    assert "order_date" in roles["order_id"]["attributes"]  # order-level, so it stays on the fact
    assert [(u["column"], u["violations"]) for u in a["unstable_attributes"]] == [("customer_region", 4)]


def test_composite_grain_is_minimal_and_duplicates_are_counted() -> None:
    funnel = analyze_table(source_for("daily_funnel"), "daily_funnel")
    # app_version determines platform, so platform is not needed to identify a row.
    assert funnel["grain"] == ["date", "app_version", "channel", "country"]
    orders = analyze_table(source_for("ecommerce_orders"), "ecommerce_orders")
    assert orders["duplicate_rows"] == 12 and orders["grain"] == ["order_id"]


def test_a_unique_measure_is_not_the_grain(tmp_path: Path) -> None:
    """Walmart-style weekly sales: every Weekly_Sales value happens to be unique, but the grain is
    store and week."""
    import numpy as np
    import pandas as pd

    from inferra import storage

    rng = np.random.default_rng(5)
    weeks = pd.date_range("2010-02-05", periods=30, freq="W-FRI")
    frame = pd.DataFrame(
        [
            (store, week.date(), float(rng.uniform(2e5, 3e6)), int(week.month == 11))
            for store in range(1, 46)
            for week in weeks
        ],
        columns=["Store", "Date", "Weekly_Sales", "Holiday_Flag"],
    )
    csv = tmp_path / "walmart.csv"
    frame.to_csv(csv, index=False)
    rows, cols = convert_to_parquet(csv, storage.local_path("workspaces/w1/walmart.parquet"))
    source = FileSource("w1", [(TableInfo("walmart", cols, rows), "workspaces/w1/walmart.parquet")])

    a = analyze_table(source, "walmart")
    assert sorted(a["grain"]) == ["Date", "Store"]
    assert ["Weekly_Sales"] not in a["candidate_keys"]
    assert "Weekly_Sales" in a["measures"] and "Store" not in a["measures"]


def test_suggested_star_schema_builds_and_its_tests_find_guest_orders() -> None:
    source = source_for("retail_order_lines")
    analysis = analyze_table(source, "retail_order_lines")
    nodes = suggest_star_schema(analysis, low_cardinality_values(source, analysis))
    assert [n["name"] for n in nodes] == [
        "stg_retail_order_lines",
        "dim_customer",
        "dim_product",
        "dim_date",
        "fct_retail_order_lines",
    ]
    result = build_project(source, parse_nodes(nodes)).result
    rows = {n["name"]: n["row_count"] for n in result["nodes"]}
    assert rows["fct_retail_order_lines"] == 6573 and rows["dim_product"] == 60
    failing = [t for t in result["tests"] if t["status"] != "pass"]
    assert [t["id"] for t in failing] == ["fct_retail_order_lines.customer_id.not_null"]
    assert failing[0]["failures"] == 90 and failing[0]["sample"][0]["customer_id"] is None
    assert {"from": "stg_retail_order_lines", "to": "dim_customer"} in result["edges"]
    assert {"from": "retail_order_lines", "to": "stg_retail_order_lines"} in result["edges"]


def test_compile_errors_are_specific() -> None:
    def nodes(*specs: tuple[str, str]) -> Any:
        return parse_nodes([{"name": n, "layer": "staging", "sql": sql} for n, sql in specs])

    with pytest.raises(ProjectError, match="unknown node"):
        compile_project(nodes(("a", "SELECT * FROM {{ ref('b') }}")), {"t"})
    with pytest.raises(ProjectError, match="unknown dataset"):
        compile_project(nodes(("a", "SELECT * FROM {{ source('nope') }}")), {"t"})
    with pytest.raises(ProjectError, match="Circular"):
        compile_project(
            nodes(("a", "SELECT * FROM {{ ref('b') }}"), ("b", "SELECT * FROM {{ ref('a') }}")), {"t"}
        )
    with pytest.raises(ProjectError, match="clash"):
        compile_project(nodes(("t", "SELECT 1")), {"t"})
    order = compile_project(
        nodes(("b", "SELECT * FROM {{ ref('a') }}"), ("a", "SELECT * FROM {{ source('t') }}")), {"t"}
    )
    assert [n.name for n in order.nodes] == ["a", "b"]


def test_failures_are_contained_and_raw_references_and_writes_are_rejected() -> None:
    source = source_for("ecommerce_orders")
    nodes = parse_nodes(
        [
            {"name": "bad", "layer": "staging", "sql": "SELECT nope FROM {{ source('ecommerce_orders') }}"},
            {"name": "child", "layer": "mart", "sql": "SELECT * FROM {{ ref('bad') }}"},
            {"name": "raw", "layer": "staging", "sql": "SELECT * FROM ecommerce_orders"},
            {"name": "evil", "layer": "staging", "sql": "COPY (SELECT 1) TO '/tmp/x.csv'"},
            {"name": "ok", "layer": "staging", "sql": "SELECT 1 AS one"},
        ]
    )
    result = build_project(source, nodes).result
    status = {n["name"]: n["status"] for n in result["nodes"]}
    assert status == {"bad": "error", "child": "skipped", "raw": "error", "evil": "error", "ok": "ok"}
    errors = {n["name"]: n.get("error", "") for n in result["nodes"]}
    assert "source()" in errors["raw"] and "SELECT" in errors["evil"]
    assert result["build_ok"] is False


def test_dbt_export_layout() -> None:
    from inferra.transform.dbt import export_project

    source = source_for("retail_order_lines")
    analysis = analyze_table(source, "retail_order_lines")
    nodes = parse_nodes(suggest_star_schema(analysis))
    project = compile_project(nodes, {"retail_order_lines"})
    blob = export_project("Retail model", "", project.nodes, [{"name": "retail_order_lines"}])
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        names = set(zf.namelist())
        fact = zf.read("retail_model/models/marts/fct_retail_order_lines.sql").decode()
        marts = zf.read("retail_model/models/marts/_marts.yml").decode()
    assert "retail_model/models/staging/stg_retail_order_lines.sql" in names
    assert "retail_model/macros/unique_combination_of_columns.sql" in names  # no package needed
    assert "{{ ref('stg_retail_order_lines') }}" in fact and "config(materialized='table')" in fact
    assert "unique_combination_of_columns" in marts and "ref('dim_customer')" in marts


def test_data_model_api_suggest_edit_build_publish_export(client: TestClient) -> None:
    ws = login(client)
    dataset = upload(client, ws, SAMPLES / "retail_order_lines.csv")
    res = client.post(f"/api/workspaces/{ws}/data-models/suggest", json={"dataset_id": dataset["id"]})
    assert res.status_code == 201, res.text
    model = res.json()
    assert model["summary"]["tests_failed"] == 1 and not model["stale"]

    # A person edits the fact to fall back to a guest customer; the test passes after a build.
    nodes = model["nodes"]
    fact = next(n for n in nodes if n["layer"] == "fact")
    fact["sql"] = fact["sql"].replace(
        '"customer_id",', "coalesce(\"customer_id\", 'GUEST') AS customer_id,", 1
    )
    fact["columns"] = [c for c in fact["columns"] if c["name"] != "customer_id"]
    res = client.put(f"/api/workspaces/{ws}/data-models/{model['id']}", json={**model, "nodes": nodes})
    assert res.status_code == 200 and res.json()["stale"]
    built = client.post(f"/api/workspaces/{ws}/data-models/{model['id']}/build").json()
    assert built["summary"]["tests_failed"] == 0 and not built["stale"]

    bad = client.put(
        f"/api/workspaces/{ws}/data-models/{model['id']}",
        json={
            **model,
            "nodes": [*nodes, {"name": "x", "layer": "mart", "sql": "SELECT * FROM {{ ref('y') }}"}],
        },
    )
    assert bad.status_code == 400 and "unknown node" in bad.json()["detail"]

    published = client.post(
        f"/api/workspaces/{ws}/data-models/{model['id']}/publish",
        json={"nodes": ["fct_retail_order_lines", "dim_customer"]},
    )
    assert published.status_code == 200, published.text
    fact_ds = next(d for d in published.json() if d["table_name"] == "fct_retail_order_lines")
    assert fact_ds["source_kind"] == "model" and fact_ds["row_count"] == 6573
    preview = client.get(f"/api/workspaces/{ws}/datasets/{fact_ds['id']}").json()["preview"]
    assert any(r["customer_id"] == "GUEST" for r in preview) or len(preview) == 20
    again = client.post(
        f"/api/workspaces/{ws}/data-models/{model['id']}/publish", json={"nodes": ["dim_customer"]}
    )
    assert again.json()[0]["id"] in {d["id"] for d in published.json()}  # replaced, not duplicated
    refreshed = client.post(f"/api/workspaces/{ws}/datasets/{fact_ds['id']}/refresh")
    assert refreshed.status_code == 200 and refreshed.json()["row_count"] == 6573
    detail = client.get(f"/api/workspaces/{ws}/data-models/{model['id']}").json()
    assert {p["node"] for p in detail["published"]} == {"fct_retail_order_lines", "dim_customer"}
    assert "fct_retail_order_lines" not in detail["sources"]

    export = client.get(f"/api/workspaces/{ws}/data-models/{model['id']}/dbt")
    assert export.status_code == 200 and export.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(export.content)) as zf:
        assert any(n.endswith("data/retail_order_lines.parquet") for n in zf.namelist())

    assert client.delete(f"/api/workspaces/{ws}/data-models/{model['id']}").status_code == 204
    still = client.get(f"/api/workspaces/{ws}/datasets/{fact_ds['id']}").json()
    assert still["data_model_id"] is None


def test_model_data_run_end_to_end(client: TestClient) -> None:
    ws = login(client)
    upload(client, ws, SAMPLES / "retail_order_lines.csv")
    use_llm(demo_script)
    client.patch(f"/api/workspaces/{ws}", json={"auto_approve_plans": True})
    run = client.post(
        f"/api/workspaces/{ws}/runs", json={"goal": "Turn the order lines export into a star schema"}
    ).json()
    drain()
    run = client.get(f"/api/workspaces/{ws}/runs/{run['id']}").json()
    assert run["status"] == "completed", run["error"]
    assert run["intent"] == "model_data"
    assert [s["agent"] for s in run["steps"]] == ["data", "modeler", "insight"]
    kinds = {a["kind"] for a in run["artifacts"]}
    assert {"keys", "data_model"} <= kinds
    failed = [(i["title"], c) for i in run["insights"] for c in i["checks"] if not c["passed"]]
    assert run["insights"] and not failed, failed
    assert any(c["name"] == "model_tests" for i in run["insights"] for c in i["checks"])
    models = client.get(f"/api/workspaces/{ws}/data-models").json()
    assert len(models) == 1 and models[0]["run_id"] == run["id"]
    art = next(a for a in run["artifacts"] if a["kind"] == "data_model")
    assert art["data"]["data_model_id"] == models[0]["id"]
