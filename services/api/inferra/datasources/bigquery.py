"""BigQuery connections through a service account.

Every query is dry-run first: it must be a SELECT, and the bytes it would scan must fit the
connection's budget, which is also set as `maximum_bytes_billed` on the real job. Grant the service
account BigQuery Data Viewer and BigQuery Job User; nothing more is needed."""

import json
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from inferra.datasources.base import QueryError
from inferra.datasources.extracts import Columns, arrow_to_parquet, wrap_select

TITLE = "BigQuery"
PROJECT = re.compile(r"^(?:[a-z0-9.-]+:)?[a-z][a-z0-9-]{4,61}[a-z0-9]$")
IDENTIFIER = re.compile(r"^[A-Za-z0-9_$-]{1,1024}$")
GB = 1024**3
MAX_DATASETS = 50
MAX_TABLES = 1000


class BigQueryIn(BaseModel):
    kind: Literal["bigquery"]
    name: str = Field(min_length=1, max_length=200)
    project_id: str = Field(min_length=6, max_length=100)
    location: str | None = Field(default=None, max_length=50)
    dataset: str | None = Field(default=None, max_length=1024, description="List only this dataset.")
    credentials_json: str = Field(min_length=2, max_length=20000, description="A service account key (JSON).")
    maximum_gb_billed: float = Field(default=10, gt=0, le=10_000)

    @field_validator("project_id")
    @classmethod
    def _project(cls, value: str) -> str:
        if not PROJECT.match(value):
            raise ValueError("not a valid Google Cloud project id")
        return value

    @field_validator("dataset")
    @classmethod
    def _dataset(cls, value: str | None) -> str | None:
        if value and not IDENTIFIER.match(value):
            raise ValueError("not a valid dataset id")
        return value or None


def parse(body: BigQueryIn) -> tuple[dict[str, Any], str]:
    try:
        key = json.loads(body.credentials_json)
    except ValueError as exc:
        raise QueryError("The service account key is not valid JSON.") from exc
    if not isinstance(key, dict) or key.get("type") != "service_account" or "client_email" not in key:
        raise QueryError("Paste a service account key file (type 'service_account').")
    config = {
        "project_id": body.project_id,
        "location": body.location,
        "dataset": body.dataset,
        "maximum_bytes_billed": int(body.maximum_gb_billed * GB),
        "client_email": key["client_email"],
    }
    return config, body.credentials_json


def client(config: dict[str, Any], secret: str) -> Any:
    from google.cloud import bigquery
    from google.oauth2 import service_account

    credentials = service_account.Credentials.from_service_account_info(
        json.loads(secret), scopes=["https://www.googleapis.com/auth/bigquery"]
    )
    return bigquery.Client(
        project=config["project_id"], credentials=credentials, location=config.get("location")
    )


def _error(exc: Exception) -> QueryError:
    message = getattr(exc, "message", None) or str(exc)
    return QueryError(f"BigQuery: {str(message).strip().splitlines()[0][:300]}")


def verify(config: dict[str, Any], secret: str) -> str:
    try:
        client(config, secret).query("SELECT 1").result()  # waits for the job to finish
    except QueryError:
        raise
    except Exception as exc:  # google.api_core errors share no useful base with auth errors
        raise _error(exc) from exc
    return f"BigQuery project {config['project_id']}"


def tables(config: dict[str, Any], secret: str) -> list[dict[str, Any]]:
    bq = client(config, secret)
    try:
        datasets = (
            [config["dataset"]]
            if config.get("dataset")
            else [d.dataset_id for d in bq.list_datasets(max_results=MAX_DATASETS)]
        )
        out: list[dict[str, Any]] = []
        for dataset in datasets:
            for t in bq.list_tables(f"{config['project_id']}.{dataset}", max_results=MAX_TABLES):
                out.append(
                    {"schema": dataset, "table": t.table_id, "type": t.table_type, "estimated_rows": None}
                )
    except Exception as exc:
        raise _error(exc) from exc
    return out


def source_sql(config: dict[str, Any], source: dict[str, Any]) -> str:
    if source.get("sql"):
        return wrap_select(source["sql"])
    schema, table = source.get("schema") or config.get("dataset"), source.get("table")
    if not table or not schema:
        raise QueryError("Choose a table (dataset and table) or provide a query.")
    if not IDENTIFIER.match(schema) or not IDENTIFIER.match(table):
        raise QueryError("Dataset and table names may contain letters, digits, '_', '-', and '$'.")
    return f"SELECT * FROM `{config['project_id']}.{schema}.{table}`"


def extract(
    config: dict[str, Any], secret: str, source: dict[str, Any], dest: Path, max_rows: int
) -> tuple[int, Columns, bool]:
    from google.cloud import bigquery

    sql = source_sql(config, source)
    budget = int(config["maximum_bytes_billed"])
    bq = client(config, secret)
    try:
        plan = bq.query(sql, job_config=bigquery.QueryJobConfig(dry_run=True, use_query_cache=False))
        if (plan.statement_type or "SELECT") != "SELECT":
            raise QueryError("Only SELECT queries can be extracted.")
        scanned = int(plan.total_bytes_processed or 0)
        if scanned > budget:
            raise QueryError(
                f"This query would scan {scanned / GB:.1f} GB, over this connection's "
                f"{budget / GB:.1f} GB limit. Filter or aggregate it first."
            )
        job = bq.query(sql, job_config=bigquery.QueryJobConfig(maximum_bytes_billed=budget))
        table = job.result(max_results=max_rows + 1).to_arrow(create_bqstorage_client=False)
    except QueryError:
        raise
    except Exception as exc:
        raise _error(exc) from exc
    truncated = table.num_rows > max_rows
    if truncated:
        table = table.slice(0, max_rows)
    rows, columns = arrow_to_parquet(table, dest)
    return rows, columns, truncated
