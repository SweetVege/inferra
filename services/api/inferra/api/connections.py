import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from inferra import storage
from inferra.api.datasets import _out as dataset_out
from inferra.api.datasets import _unique_table_name
from inferra.api.schemas import ORM, DatasetOut
from inferra.auth.session import WorkspaceAccess, workspace_access
from inferra.config import get_settings
from inferra.datasources.base import QueryError
from inferra.datasources.connectors import connector, parse_input
from inferra.datasources.files import to_table_name
from inferra.datasources.secrets import decrypt, encrypt
from inferra.db.models import Connection, Dataset, new_id
from inferra.db.session import get_db

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["connections"])


class ConnectionOut(ORM):
    id: str
    name: str
    kind: str
    config: dict[str, Any]
    created_at: datetime


class ConnectionCreated(ConnectionOut):
    server_version: str  # what the server reported when the credentials were checked
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


@router.get("/connections", response_model=list[ConnectionOut])
def list_connections(
    access: WorkspaceAccess = Depends(workspace_access), db: Session = Depends(get_db)
) -> list[Connection]:
    return list(db.scalars(select(Connection).where(Connection.workspace_id == access.workspace.id)))


@router.post("/connections", response_model=ConnectionCreated, status_code=201)
def create_connection(
    body: dict[str, Any], access: WorkspaceAccess = Depends(workspace_access), db: Session = Depends(get_db)
) -> ConnectionCreated:
    """Test the credentials, then store them encrypted. The body's `kind` picks the connector
    (postgres, bigquery, or snowflake); its other fields are that connector's settings."""
    access.require("editor")
    try:
        kind, name, config, secret = parse_input(body)
        impl = connector(kind)
        version = impl.verify(config, secret)
        tables = impl.tables(config, secret)
    except ValidationError as exc:
        first = exc.errors()[0]
        field = ".".join(str(p) for p in first["loc"][1:]) or "body"
        raise HTTPException(422, f"{field}: {first['msg']}") from exc
    except QueryError as exc:
        raise HTTPException(400, str(exc)) from exc
    conn = Connection(
        workspace_id=access.workspace.id,
        name=name,
        kind=kind,
        config=config,
        secret=encrypt(secret),
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
        return connector(conn.kind).tables(conn.config, decrypt(conn.secret))
    except QueryError as exc:
        raise HTTPException(400, str(exc)) from exc


def _run_extract(conn: Connection, source: dict[str, Any], key: str) -> tuple[int, list[dict[str, str]]]:
    dest = storage.local_path(key)
    limit = get_settings().extract_max_rows
    with tempfile.TemporaryDirectory() as tmp:
        staging = Path(tmp) / "extract.parquet"
        rows, columns, truncated = connector(conn.kind).extract(
            conn.config, decrypt(conn.secret), source, staging, limit
        )
        if truncated:
            raise QueryError(
                f"The source has more than {limit:,} rows. Extract an aggregated or filtered query instead."
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
        rows, columns = _run_extract(conn, source, key)
    except QueryError as exc:
        raise HTTPException(400, str(exc)) from exc
    base = body.name or body.table or "query"
    table = _unique_table_name(db, access.workspace.id, to_table_name(base))
    dataset = Dataset(
        id=dataset_id,
        workspace_id=access.workspace.id,
        source_kind=conn.kind,
        table_name=table,
        display_name=base.replace("_", " "),
        description=f"Extract from {conn.name}"
        + (f" ({body.schema_name}.{body.table})" if body.table and body.schema_name else "")
        + (f" ({body.table})" if body.table and not body.schema_name else ""),
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


def refresh_dataset(db: Session, dataset: Dataset, user_id: str | None) -> None:
    """Re-pull an extract from its connection, or rebuild a published data model node.
    Raises QueryError when the source cannot be read."""
    if dataset.data_model_id and dataset.source_query:
        from inferra.api.data_models import publish_nodes
        from inferra.db.models import DataModel

        model = db.get(DataModel, dataset.data_model_id)
        if model is None:
            raise QueryError("The data model behind this dataset was deleted.")
        publish_nodes(db, model, [dataset.source_query], user_id)
        db.refresh(dataset)
        return
    conn = db.get(Connection, dataset.connection_id) if dataset.connection_id else None
    if conn is None or not dataset.source_query:
        raise QueryError("Only extracts and published data model nodes can be refreshed.")
    rows, columns = _run_extract(conn, json.loads(dataset.source_query), dataset.storage_key)
    dataset.row_count, dataset.columns = rows, columns
    dataset.profile = None
    dataset.synced_at = datetime.now(UTC)
    db.commit()


def refreshable(dataset: Dataset) -> bool:
    return bool(dataset.source_query and (dataset.data_model_id or dataset.connection_id))


@router.post("/datasets/{dataset_id}/refresh", response_model=DatasetOut)
def refresh_extract(
    dataset_id: str, access: WorkspaceAccess = Depends(workspace_access), db: Session = Depends(get_db)
) -> DatasetOut:
    access.require("editor")
    dataset = db.get(Dataset, dataset_id)
    if dataset is None or dataset.workspace_id != access.workspace.id:
        raise HTTPException(404, "Dataset not found.")
    try:
        refresh_dataset(db, dataset, access.user.id)
    except QueryError as exc:
        raise HTTPException(400, str(exc)) from exc
    return dataset_out(dataset)


@router.delete("/connections/{connection_id}", status_code=204)
def delete_connection(
    connection_id: str, access: WorkspaceAccess = Depends(workspace_access), db: Session = Depends(get_db)
) -> None:
    """Remove the credentials; existing extracts stay as static datasets."""
    access.require("editor")
    db.delete(_get(db, access, connection_id))
    db.commit()
