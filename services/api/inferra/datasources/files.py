"""Uploaded files, stored as Parquet and queried with an in-process, sandboxed DuckDB."""

import datetime as dt
import decimal
import math
import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from inferra import storage
from inferra.datasources.base import QueryError, QueryResult, TableInfo

SUPPORTED_EXTENSIONS = {".csv", ".tsv", ".txt", ".parquet", ".xlsx", ".xls"}


def json_safe(value: Any) -> Any:
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        return None if math.isnan(value) or math.isinf(value) else value
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, dt.datetime | dt.date | dt.time):
        return value.isoformat()
    if isinstance(value, dt.timedelta):
        return value.total_seconds()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, list | tuple):
        return [json_safe(v) for v in value]
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    return str(value)


def to_table_name(raw: str) -> str:
    name = re.sub(r"[^0-9a-zA-Z_]+", "_", Path(raw).stem).strip("_").lower()
    if not name or name[0].isdigit():
        name = f"t_{name}"
    return name[:60]


def convert_to_parquet(source: Path, dest: Path) -> tuple[int, list[dict[str, str]]]:
    """Normalize an uploaded file to Parquet. Returns (row_count, columns)."""
    ext = source.suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise QueryError(f"Unsupported file type '{ext}'. Upload CSV, TSV, Parquet, or Excel.")
    dest.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    try:
        if ext in {".xlsx", ".xls"}:
            import pandas as pd

            frame = pd.read_excel(source)
            frame.columns = [str(c) for c in frame.columns]
            con.register("upload", frame)
            relation = "upload"
        elif ext == ".parquet":
            relation = f"read_parquet('{_sql_path(source)}')"
        else:
            relation = f"read_csv_auto('{_sql_path(source)}', sample_size=-1)"
        con.execute(f"COPY (SELECT * FROM {relation}) TO '{_sql_path(dest)}' (FORMAT parquet)")
        described = con.execute(f"DESCRIBE SELECT * FROM read_parquet('{_sql_path(dest)}')")
        columns = [{"name": r[0], "type": r[1]} for r in described.fetchall()]
        row_count = con.execute(f"SELECT count(*) FROM read_parquet('{_sql_path(dest)}')").fetchone()[0]  # type: ignore[index]
    except duckdb.Error as exc:
        raise QueryError(f"Could not read the file: {exc}") from exc
    finally:
        con.close()
    return int(row_count), columns


def _sql_path(path: Path) -> str:
    return str(path).replace("'", "''")


class FileSource:
    """A workspace's uploaded datasets exposed as DuckDB views.

    Each query gets a fresh in-memory connection that can read only this workspace's directory,
    cannot change its own configuration, and accepts a single SELECT statement."""

    def __init__(self, workspace_id: str, tables: list[tuple[TableInfo, str]]):
        self.workspace_id = workspace_id
        self._tables = tables  # (info, storage_key)

    def list_tables(self) -> list[TableInfo]:
        return [info for info, _ in self._tables]

    def quote(self, identifier: str) -> str:
        return '"' + identifier.replace('"', '""') + '"'

    @contextmanager
    def session(self) -> Iterator[duckdb.DuckDBPyConnection]:
        """A sandboxed connection over the views, for multi-statement work such as building a
        data model. Callers must validate any SQL they run (see `validated`)."""
        con = self._connect()
        try:
            yield con
        finally:
            con.close()

    def validated(self, con: duckdb.DuckDBPyConnection, sql: str) -> str:
        return self._validated(con, sql)

    def _connect(self) -> duckdb.DuckDBPyConnection:
        con = duckdb.connect()
        for info, key in self._tables:
            path = storage.ensure_local(key)
            con.execute(
                f"CREATE VIEW {self.quote(info.name)} AS SELECT * FROM read_parquet('{_sql_path(path)}')"
            )
        allowed = _sql_path(storage.workspace_dir(self.workspace_id).resolve())
        con.execute(f"SET allowed_directories=['{allowed}']")
        con.execute("SET enable_external_access=false")
        con.execute("SET lock_configuration=true")
        return con

    def _validated(self, con: duckdb.DuckDBPyConnection, sql: str) -> str:
        sql = sql.strip().rstrip(";").strip()
        try:
            statements = con.extract_statements(sql)
        except duckdb.Error as exc:
            raise QueryError(f"SQL could not be parsed: {exc}") from exc
        if len(statements) != 1:
            raise QueryError("Send exactly one SQL statement.")
        if statements[0].type != duckdb.StatementType.SELECT:
            raise QueryError("Only read-only SELECT queries are allowed.")
        return sql

    def query(self, sql: str, limit: int | None = None) -> QueryResult:
        con = self._connect()
        try:
            sql = self._validated(con, sql)
            try:
                cursor = con.execute(sql)
                columns = [d[0] for d in cursor.description or []]
                types = {d[0]: str(d[1]) for d in cursor.description or []}
                fetched = cursor.fetchmany(limit + 1) if limit is not None else cursor.fetchall()
            except duckdb.Error as exc:
                raise QueryError(str(exc).split("\n")[0]) from exc
        finally:
            con.close()
        truncated = limit is not None and len(fetched) > limit
        if truncated:
            fetched = fetched[:limit]
        rows = [{col: json_safe(val) for col, val in zip(columns, row, strict=True)} for row in fetched]
        return QueryResult(columns=columns, rows=rows, truncated=truncated, sql=sql, types=types)

    def query_frame(self, sql: str) -> "pd.DataFrame":
        """Run a read-only SELECT and return a DataFrame (for modeling tools that need rows)."""
        con = self._connect()
        try:
            sql = self._validated(con, sql)
            try:
                return con.execute(sql).df()
            except duckdb.Error as exc:
                raise QueryError(str(exc).split("\n")[0]) from exc
        finally:
            con.close()
