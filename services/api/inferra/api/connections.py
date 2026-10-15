import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from inferra import storage
from inferra.api.datasets import _out as dataset_out
from inferra.api.datasets import _unique_table_name
from inferra.api.schemas import ORM, DatasetOut
from inferra.auth.session import WorkspaceAccess, workspace_access
from inferra.config import get_settings
from inferra.datasources import postgres
from inferra.datasources.base import QueryError
from inferra.datasources.files import to_table_name
from inferra.db.models import Connection, Dataset, new_id
from inferra.db.session import get_db

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["connections"])


class ConnectionIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=5432, ge=1, le=65535)
    database: str = Field(min_length=1, max_length=200)
    user: str = Field(min_length=1, max_length=200)
    password: str = Field(max_length=500)
    sslmode: Literal["disable", "prefer", "require", "verify-ca", "verify-full"] = "prefer"


class ConnectionOut(ORM):
    id: str
    name: str
    kind: str
    config: dict[str, Any]
    created_at: datetime


class ConnectionCreated(ConnectionOut):
    server_version: str
    tables: list[dict[str, Any]]


class ExtractIn(BaseModel):
    schema_name: str | None = Field(default=None, alias="schema")
    table: str | None = None
    sql: str | None = Field(default=None, max_length=20000)
    name: str | None = Field(default=None, max_length=100)

    model_config = {"populate_by_name": True}


def _get(db: Session, access: WorkspaceAccess, connection_id: str) -> Connection:
    conn = db.get(Connection, connection_id)
    if conn is None or conn.workspace_id != access.workspace.id:
        raise HTTPException(404, "Connection not found.")
    return conn


def _config(conn: Connection) -> postgres.PgConfig:
    return postgres.PgConfig.from_dict(conn.config)


@router.get("/connections", response_model=list[ConnectionOut])
def list_connections(
    access: WorkspaceAccess = Depends(workspace_access), db: Session = Depends(get_db)
) -> list[Connection]:
    return list(db.scalars(select(Connection).where(Connection.workspace_id == access.workspace.id)))


@router.post("/connections", response_model=ConnectionCreated, status_code=201)
def create_connection(
    body: ConnectionIn, access: WorkspaceAccess = Depends(workspace_access), db: Session = Depends(get_db)
) -> ConnectionCreated:
    """Test the credentials, then store them (password encrypted)."""
    access.require("editor")
    config = postgres.PgConfig(body.host, body.port, body.database, body.user, body.sslmode)
    try:
        version = postgres.server_version(config, body.password)
        tables = postgres.list_tables(config, body.password)
    except QueryError as exc:
        raise HTTPException(400, str(exc)) from exc
    conn = Connection(
        workspace_id=access.workspace.id,
        name=body.name,
        kind="postgres",
        config=config.__dict__,
        secret=postgres.encrypt(body.password),
        created_by=access.user.id,
    )
    db.add(conn)
    db.commit()
    return ConnectionCreated(
        **ConnectionOut.model_validate(conn).model_dump(), server_version=version, tables=tables
    )


@router.get("/connections/{connection_id}/tables")
def connection_tables(
    connection_id: str, access: WorkspaceAccess = Depends(workspace_access), db: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    conn = _get(db, access, connection_id)
    try:
        return postgres.list_tables(_config(conn), postgres.decrypt(conn.secret))
    except QueryError as exc:
        raise HTTPException(400, str(exc)) from exc


def _run_extract(
    db: Session, conn: Connection, source: dict[str, Any], key: str
) -> tuple[int, list[dict[str, str]]]:
    query = postgres.source_sql(source.get("schema"), source.get("table"), source.get("sql"))
    dest = storage.local_path(key)
    with tempfile.TemporaryDirectory() as tmp:
        staging = Path(tmp) / "extract.parquet"
        rows, columns, truncated = postgres.extract(
            _config(conn), postgres.decrypt(conn.secret), query, staging, get_settings().extract_max_rows
        )
        if truncated:
            raise QueryError(
                f"The source has more than {get_settings().extract_max_rows:,} rows. "
                "Extract an aggregated or filtered query instead."
            )
        dest.parent.mkdir(parents=True, exist_ok=True)
        staging.replace(dest)
    storage.put_file(key, dest)
    return rows, columns


@router.post("/connections/{connection_id}/extracts", response_model=DatasetOut, status_code=201)
def create_extract(
    connection_id: str,
    body: ExtractIn,
    access: WorkspaceAccess = Depends(workspace_access),
    db: Session = Depends(get_db),
) -> DatasetOut:
    """Pull a table or a read-only query into the workspace as a dataset."""
    access.require("editor")
    conn = _get(db, access, connection_id)
    source = {"schema": body.schema_name, "table": body.table, "sql": body.sql}
    dataset_id = new_id()
    key = f"workspaces/{access.workspace.id}/{dataset_id}.parquet"
    try:
        rows, columns = _run_extract(db, conn, source, key)
    except QueryError as exc:
        raise HTTPException(400, str(exc)) from exc
    base = body.name or body.table or "query"
    table = _unique_table_name(db, access.workspace.id, to_table_name(base))
    dataset = Dataset(
        id=dataset_id,
        workspace_id=access.workspace.id,
        source_kind="postgres",
        table_name=table,
        display_name=base.replace("_", " "),
        description=f"Extract from {conn.name}"
        + (f" ({body.schema_name or 'public'}.{body.table})" if body.table else ""),
        storage_key=key,
        row_count=rows,
        columns=columns,
        created_by=access.user.id,
        connection_id=conn.id,
        source_query=json.dumps(source),
        synced_at=datetime.now(UTC),
    )
    db.add(dataset)
    db.commit()
    return dataset_out(dataset)


@router.post("/datasets/{dataset_id}/refresh", response_model=DatasetOut)
def refresh_extract(
    dataset_id: str, access: WorkspaceAccess = Depends(workspace_access), db: Session = Depends(get_db)
) -> DatasetOut:
    access.require("editor")
    dataset = db.get(Dataset, dataset_id)
    if dataset is None or dataset.workspace_id != access.workspace.id:
        raise HTTPException(404, "Dataset not found.")
    if not dataset.connection_id or not dataset.source_query:
        raise HTTPException(400, "Only extracts from a connection can be refreshed.")
    conn = _get(db, access, dataset.connection_id)
    try:
        rows, columns = _run_extract(db, conn, json.loads(dataset.source_query), dataset.storage_key)
    except QueryError as exc:
        raise HTTPException(400, str(exc)) from exc
    dataset.row_count, dataset.columns = rows, columns
    dataset.profile = None
    dataset.synced_at = datetime.now(UTC)
    db.commit()
    return dataset_out(dataset)


@router.delete("/connections/{connection_id}", status_code=204)
def delete_connection(
    connection_id: str, access: WorkspaceAccess = Depends(workspace_access), db: Session = Depends(get_db)
) -> None:
    """Remove the credentials; existing extracts stay as static datasets."""
    access.require("editor")
    db.delete(_get(db, access, connection_id))
    db.commit()
