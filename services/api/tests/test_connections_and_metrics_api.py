"""Postgres connections, extracts, the metric layer API, and the diagnose workflow end to end."""

import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pytest
from fastapi.testclient import TestClient

from inferra.db.models import Connection
from inferra.db.session import session_scope
from inferra.llm.demo_script import demo_script
from inferra.runtime.queue import drain
from tests.test_runs_e2e import login, upload, use_llm

SAMPLES = Path(__file__).resolve().parents[3] / "samples"
PG_URL = os.environ.get("INFERRA_TEST_DATABASE_URL", "")
needs_pg = pytest.mark.skipif(
    not PG_URL.startswith("postgres"), reason="set INFERRA_TEST_DATABASE_URL to a Postgres URL"
)


@pytest.fixture
def client() -> TestClient:
    from inferra.main import create_app

    return TestClient(create_app())


@pytest.fixture
def pg() -> dict[str, Any]:
    import psycopg

    u = urlparse(PG_URL)
    params = {
        "host": u.hostname,
        "port": u.port or 5432,
        "database": u.path.lstrip("/"),
        "user": u.username,
        "password": u.password,
    }
    with psycopg.connect(
        host=u.hostname,
        port=u.port or 5432,
        dbname=params["database"],
        user=u.username,
        password=u.password,
        autocommit=True,
    ) as conn:
        conn.execute("DROP TABLE IF EXISTS src_orders")
        conn.execute("CREATE TABLE src_orders (id int, region text, amount numeric(10,2), created_at date)")
        conn.execute(
            "INSERT INTO src_orders SELECT g, (ARRAY['N','S'])[1 + g % 2], g * 1.5, DATE '2026-01-01' + g "
            "FROM generate_series(1, 300) g"
        )
    yield params
    with psycopg.connect(
        host=u.hostname,
        port=u.port or 5432,
        dbname=params["database"],
        user=u.username,
        password=u.password,
        autocommit=True,
    ) as conn:
        conn.execute("DROP TABLE IF EXISTS src_orders")


def connect(client: TestClient, ws: str, pg: dict[str, Any], **overrides: Any) -> Any:
    body = {"name": "Warehouse", "sslmode": "disable", **pg, **overrides}
    return client.post(f"/api/workspaces/{ws}/connections", json=body)


@needs_pg
def test_connection_extract_and_refresh(client: TestClient, pg: dict[str, Any]) -> None:
    import psycopg

    ws = login(client)
    res = connect(client, ws, pg)
    assert res.status_code == 201, res.text
    conn = res.json()
    assert any(t["table"] == "src_orders" for t in conn["tables"])
    assert "password" not in conn["config"]
    with session_scope() as db:
        stored = db.get(Connection, conn["id"])
        assert stored is not None and pg["password"] not in stored.secret

    ds = client.post(f"/api/workspaces/{ws}/connections/{conn['id']}/extracts", json={"table": "src_orders"})
    assert ds.status_code == 201, ds.text
    ds = ds.json()
    assert ds["row_count"] == 300 and ds["source_kind"] == "postgres" and ds["synced_at"]
    types = {c["name"]: c["type"] for c in ds["columns"]}
    assert types["amount"] == "DOUBLE"
    profile = client.post(f"/api/workspaces/{ws}/datasets/{ds['id']}/profile").json()["profile"]
    assert profile["columns"]["amount"]["max"] == pytest.approx(450.0)

    query = client.post(
        f"/api/workspaces/{ws}/connections/{conn['id']}/extracts",
        json={
            "sql": "SELECT region, sum(amount) AS revenue FROM src_orders GROUP BY 1",
            "name": "revenue_by_region",
        },
    ).json()
    assert query["row_count"] == 2 and query["table_name"] == "revenue_by_region"

    u = urlparse(PG_URL)
    with psycopg.connect(
        host=u.hostname,
        port=u.port or 5432,
        dbname=pg["database"],
        user=u.username,
        password=u.password,
        autocommit=True,
    ) as c:
        c.execute("INSERT INTO src_orders VALUES (999, 'N', 1, '2026-12-01')")
    refreshed = client.post(f"/api/workspaces/{ws}/datasets/{ds['id']}/refresh").json()
    assert refreshed["row_count"] == 301


@needs_pg
def test_extracts_are_read_only_and_single_statement(client: TestClient, pg: dict[str, Any]) -> None:
    ws = login(client)
    conn = connect(client, ws, pg).json()
    url = f"/api/workspaces/{ws}/connections/{conn['id']}/extracts"
    for sql in [
        "DELETE FROM src_orders",
        "SELECT 1; DROP TABLE src_orders",
        "WITH x AS (DELETE FROM src_orders RETURNING *) SELECT * FROM x",
    ]:
        res = client.post(url, json={"sql": sql})
        assert res.status_code == 400, sql
    rows = client.post(url, json={"table": "src_orders"}).json()["row_count"]
    assert rows == 300  # nothing was deleted


@needs_pg
def test_bad_credentials_and_private_hosts(
    client: TestClient, pg: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = login(client)
    res = connect(client, ws, pg, password="wrong")
    assert res.status_code == 400 and "Could not connect" in res.json()["detail"]
    from inferra.config import get_settings

    monkeypatch.setattr(get_settings(), "allow_private_db_hosts", False)
    res = connect(client, ws, pg)
    assert res.status_code == 400 and "Private" in res.json()["detail"]


def test_metric_definitions_api(client: TestClient) -> None:
    ws = login(client)
    dataset = upload(client, ws, SAMPLES / "daily_funnel.csv")
    url = f"/api/workspaces/{ws}/metrics"
    body = {
        "dataset_id": dataset["id"],
        "name": "conversion_rate",
        "label": "Conversion rate",
        "kind": "ratio",
        "numerator": {"kind": "sum", "column": "signups"},
        "denominator": {"kind": "sum", "column": "sessions"},
        "time_column": "date",
        "dimensions": ["platform"],
        "format": "percent",
    }
    created = client.post(url, json=body)
    assert created.status_code == 201, created.text
    assert 0.04 < created.json()["current_value"] < 0.07
    assert client.post(url, json=body).status_code == 409
    bad = client.post(url, json={**body, "name": "broken", "numerator": {"kind": "sum", "column": "nope"}})
    assert bad.status_code == 400 and "nope" in bad.json()["detail"]
    missing = client.post(url, json={**body, "name": "broken2", "kind": "ratio", "numerator": None})
    assert missing.status_code == 400
    values = client.get(f"{url}/{created.json()['id']}/values?grain=week").json()
    assert values["series"] and values["series"][-1]["period"] >= "2026-10-01"
    assert [m["name"] for m in client.get(url).json()] == ["conversion_rate"]


def test_diagnose_run_and_insight_feed(client: TestClient) -> None:
    ws = login(client)
    dataset = upload(client, ws, SAMPLES / "daily_funnel.csv")
    client.post(
        f"/api/workspaces/{ws}/metrics",
        json={
            "dataset_id": dataset["id"],
            "name": "conversion_rate",
            "label": "Signup conversion rate",
            "kind": "ratio",
            "numerator": {"kind": "sum", "column": "signups"},
            "denominator": {"kind": "sum", "column": "sessions"},
            "time_column": "date",
            "format": "percent",
        },
    ).raise_for_status()
    llm = use_llm(demo_script)
    client.patch(f"/api/workspaces/{ws}", json={"auto_approve_plans": True})
    run = client.post(f"/api/workspaces/{ws}/runs", json={"goal": "Why did signup conversion drop?"}).json()
    drain()
    run = client.get(f"/api/workspaces/{ws}/runs/{run['id']}").json()
    assert run["status"] == "completed", run["error"]
    assert run["intent"] == "diagnose"
    assert {a["kind"] for a in run["artifacts"]} >= {"anomalies", "decomposition", "metric"}
    assert all(i["verified"] for i in run["insights"]), [i["checks"] for i in run["insights"]]
    planning = next(r for r in llm.requests if r.role == "planning")
    assert "conversion_rate: Signup conversion rate" in planning.first_user_text

    feed = client.get(f"/api/workspaces/{ws}/insights").json()
    assert len(feed) == len(run["insights"]) and feed[0]["run_goal"].startswith("Why did")
    client.patch(f"/api/workspaces/{ws}/insights/{feed[0]['id']}", json={"status": "accepted"})
    assert len(client.get(f"/api/workspaces/{ws}/insights?status=accepted").json()) == 1
