"""BigQuery and Snowflake connectors against in-process fakes of their client libraries: settings
validation, the safety checks around every query, extraction to Parquet, and the API flow."""

import decimal
import json
from pathlib import Path
from typing import Any

import pyarrow as pa
import pytest
from fastapi.testclient import TestClient

from inferra.datasources import bigquery, snowflake
from inferra.datasources.base import QueryError
from tests.test_runs_e2e import login

KEY = json.dumps({"type": "service_account", "client_email": "inferra@proj.iam.gserviceaccount.com"})


@pytest.fixture
def client() -> TestClient:
    from inferra.main import create_app

    return TestClient(create_app())


class FakeJob:
    def __init__(self, sql: str, dry_run: bool, bytes_: int, table: pa.Table):
        self.sql, self.dry_run, self.total_bytes_processed = sql, dry_run, bytes_
        self.statement_type = "SELECT"
        self._table = table

    def result(self, max_results: int | None = None) -> Any:
        table = self._table if max_results is None else self._table.slice(0, max_results)
        return type("Rows", (), {"to_arrow": lambda _self, create_bqstorage_client=False: table})()


class FakeBigQuery:
    def __init__(self, table: pa.Table, bytes_: int = 10_000):
        self.table, self.bytes, self.jobs = table, bytes_, []

    def query(self, sql: str, job_config: Any = None) -> FakeJob:
        job = FakeJob(sql, bool(job_config and job_config.dry_run), self.bytes, self.table)
        self.jobs.append((sql, job_config))
        return job

    def list_datasets(self, max_results: int) -> list[Any]:
        return [type("D", (), {"dataset_id": "sales"})()]

    def list_tables(self, dataset: str, max_results: int) -> list[Any]:
        return [type("T", (), {"table_id": "orders", "table_type": "TABLE"})()]


ORDERS = pa.table(
    {
        "id": [1, 2, 3],
        "amount": pa.array([decimal.Decimal("1.50"), decimal.Decimal("2.25"), None], pa.decimal128(10, 2)),
        "region": ["N", "S", "N"],
    }
)


def test_bigquery_settings_are_validated() -> None:
    with pytest.raises(ValueError):
        bigquery.BigQueryIn(kind="bigquery", name="x", project_id="Bad Project!", credentials_json=KEY)
    body = bigquery.BigQueryIn(kind="bigquery", name="x", project_id="acme-analytics", credentials_json="{}")
    with pytest.raises(QueryError, match="service account"):
        bigquery.parse(body)
    ok = bigquery.BigQueryIn(
        kind="bigquery", name="x", project_id="acme-analytics", credentials_json=KEY, maximum_gb_billed=2
    )
    config, secret = bigquery.parse(ok)
    assert config["maximum_bytes_billed"] == 2 * 1024**3 and secret == KEY
    assert config["client_email"].startswith("inferra@")


def test_bigquery_extract_checks_budget_and_writes_parquet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeBigQuery(ORDERS)
    monkeypatch.setattr(bigquery, "client", lambda config, secret: fake)
    config = {"project_id": "acme-analytics", "maximum_bytes_billed": 1_000_000, "dataset": "sales"}
    rows, columns, truncated = bigquery.extract(
        config, KEY, {"schema": "sales", "table": "orders"}, tmp_path / "a.parquet", 10
    )
    assert rows == 3 and not truncated
    assert {c["name"]: c["type"] for c in columns}["amount"] == "DOUBLE"
    (dry_sql, dry_cfg), (real_sql, real_cfg) = fake.jobs
    assert dry_cfg.dry_run and real_cfg.maximum_bytes_billed == 1_000_000
    assert real_sql == "SELECT * FROM `acme-analytics.sales.orders`"

    _rows, _cols, truncated = bigquery.extract(
        config, KEY, {"sql": "SELECT * FROM t"}, tmp_path / "b.parquet", 2
    )
    assert truncated
    assert fake.jobs[-1][0] == "SELECT * FROM (SELECT * FROM t) AS inferra_extract"

    fake.bytes = 5_000_000
    with pytest.raises(QueryError, match="over this connection's"):
        bigquery.extract(config, KEY, {"sql": "SELECT 1"}, tmp_path / "c.parquet", 10)
    with pytest.raises(QueryError, match="single SELECT"):
        bigquery.extract(config, KEY, {"sql": "DELETE FROM t WHERE true"}, tmp_path / "d.parquet", 10)
    with pytest.raises(QueryError, match="Dataset and table"):
        bigquery.extract(config, KEY, {"schema": "sales", "table": "x` ; DROP"}, tmp_path / "e.parquet", 10)


class FakeCursor:
    def __init__(self, conn: "FakeSnowflake"):
        self.conn = conn
        self.description: list[tuple[str, ...]] | None = None
        self._rows: list[tuple[Any, ...]] = []

    def execute(self, sql: str, params: Any = ()) -> None:
        self.conn.statements.append(sql)
        if "CURRENT_VERSION" in sql:
            self._rows = [("9.30.1",)]
        elif "information_schema.tables" in sql:
            self._rows = [("PUBLIC", "ORDERS", "BASE TABLE", 3)]
        else:
            self._rows = [(1, decimal.Decimal("1.50"), "N"), (2, decimal.Decimal("2.25"), "S")]
        self.description = None

    def fetchall(self) -> list[tuple[Any, ...]]:
        rows, self._rows = self._rows, []
        return rows

    def fetchmany(self, n: int) -> list[tuple[Any, ...]]:
        self.description = [("ID",), ("AMOUNT",), ("REGION",)]
        rows, self._rows = self._rows[:n], self._rows[n:]
        return rows


class FakeSnowflake:
    def __init__(self) -> None:
        self.statements: list[str] = []
        self.kwargs: dict[str, Any] = {}
        self.closed = False

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_snowflake(monkeypatch: pytest.MonkeyPatch) -> FakeSnowflake:
    import snowflake.connector

    fake = FakeSnowflake()

    def connect(**kwargs: Any) -> FakeSnowflake:
        fake.kwargs = kwargs
        return fake

    monkeypatch.setattr(snowflake.connector, "connect", connect)
    return fake


def test_snowflake_settings_and_key_pair(fake_snowflake: FakeSnowflake) -> None:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    with pytest.raises(ValueError):
        snowflake.SnowflakeIn(
            kind="snowflake", name="x", account="evil.example.com/x", user="u", warehouse="w", database="d"
        )
    both = snowflake.SnowflakeIn(
        kind="snowflake", name="x", account="acme-prod", user="u", warehouse="w", database="d"
    )
    with pytest.raises(QueryError, match="either a password or a private key"):
        snowflake.parse(both)
    pem = (
        rsa.generate_private_key(public_exponent=65537, key_size=2048)
        .private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
        .decode()
    )
    body = snowflake.SnowflakeIn(
        kind="snowflake",
        name="x",
        account="acme-prod.snowflakecomputing.com",
        user="u",
        warehouse="w",
        database="d",
        private_key=pem,
    )
    config, secret = snowflake.parse(body)
    assert config["account"] == "acme-prod" and config["auth"] == "key_pair"
    assert snowflake.verify(config, secret) == "Snowflake 9.30.1"
    assert isinstance(fake_snowflake.kwargs["private_key"], bytes) and "password" not in fake_snowflake.kwargs
    assert fake_snowflake.kwargs["session_parameters"]["STATEMENT_TIMEOUT_IN_SECONDS"] == 120
    with pytest.raises(QueryError, match="unencrypted PEM"):
        snowflake.verify(config, "not a key")


def test_snowflake_extract(fake_snowflake: FakeSnowflake, tmp_path: Path) -> None:
    config = {"account": "acme", "user": "u", "warehouse": "w", "database": "d", "schema": "PUBLIC"}
    rows, columns, truncated = snowflake.extract(
        config, "pw", {"table": 'ORD"ERS'}, tmp_path / "s.parquet", 10
    )
    assert rows == 2 and not truncated and fake_snowflake.closed
    assert fake_snowflake.statements[-1] == 'SELECT * FROM "PUBLIC"."ORD""ERS"'
    assert {c["name"]: c["type"] for c in columns} == {
        "ID": "BIGINT",
        "AMOUNT": "DOUBLE",
        "REGION": "VARCHAR",
    }
    with pytest.raises(QueryError):
        snowflake.extract(config, "pw", {"sql": "SELECT 1; DROP TABLE x"}, tmp_path / "t.parquet", 10)


def test_warehouse_connections_through_the_api(
    client: TestClient, fake_snowflake: FakeSnowflake, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = login(client)
    res = client.post(
        f"/api/workspaces/{ws}/connections",
        json={
            "kind": "snowflake",
            "name": "Snowflake prod",
            "account": "acme-prod",
            "user": "analyst",
            "warehouse": "XS",
            "database": "ANALYTICS",
            "schema": "PUBLIC",
            "password": "s3cret",
        },
    )
    assert res.status_code == 201, res.text
    conn = res.json()
    assert conn["kind"] == "snowflake" and conn["server_version"] == "Snowflake 9.30.1"
    assert conn["tables"][0]["table"] == "ORDERS" and "s3cret" not in json.dumps(conn)
    ds = client.post(
        f"/api/workspaces/{ws}/connections/{conn['id']}/extracts",
        json={"schema": "PUBLIC", "table": "ORDERS"},
    )
    assert ds.status_code == 201, ds.text
    assert ds.json()["source_kind"] == "snowflake" and ds.json()["row_count"] == 2
    refreshed = client.post(f"/api/workspaces/{ws}/datasets/{ds.json()['id']}/refresh")
    assert refreshed.status_code == 200

    fake = FakeBigQuery(ORDERS)
    monkeypatch.setattr(bigquery, "client", lambda config, secret: fake)
    res = client.post(
        f"/api/workspaces/{ws}/connections",
        json={"kind": "bigquery", "name": "BQ", "project_id": "acme-analytics", "credentials_json": KEY},
    )
    assert res.status_code == 201, res.text
    assert res.json()["config"]["client_email"].startswith("inferra@")
    assert res.json()["tables"] == [
        {"schema": "sales", "table": "orders", "type": "TABLE", "estimated_rows": None}
    ]

    bad = client.post(f"/api/workspaces/{ws}/connections", json={"kind": "oracle", "name": "x"})
    assert bad.status_code == 422
    missing = client.post(f"/api/workspaces/{ws}/connections", json={"kind": "bigquery", "name": "x"})
    assert missing.status_code == 422 and "project_id" in missing.json()["detail"]
