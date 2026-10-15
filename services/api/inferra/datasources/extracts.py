"""Write extracted rows to Parquet, the common landing format for every connector."""

import decimal
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from inferra.datasources.base import QueryError

Columns = list[dict[str, str]]


def _describe(dest: Path) -> Columns:
    con = duckdb.connect()
    try:
        path = str(dest).replace("'", "''")
        rows = con.execute(f"DESCRIBE SELECT * FROM read_parquet('{path}')").fetchall()
    finally:
        con.close()
    return [{"name": r[0], "type": r[1]} for r in rows]


def frames_to_parquet(frames: list[pd.DataFrame], dest: Path) -> tuple[int, Columns]:
    """Batches of rows (as DataFrames) to one Parquet file. Decimals become floats."""
    if not frames:
        raise QueryError("The query returned no rows.")
    frame = pd.concat(frames, ignore_index=True)
    for col in frame.columns:
        if frame[col].map(lambda v: isinstance(v, decimal.Decimal)).any():
            frame[col] = pd.to_numeric(frame[col], errors="coerce").astype(float)
    dest.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    try:
        con.register("extract", frame)
        path = str(dest).replace("'", "''")
        con.execute(f"COPY (SELECT * FROM extract) TO '{path}' (FORMAT parquet)")
    finally:
        con.close()
    return len(frame), _describe(dest)


def arrow_to_parquet(table: Any, dest: Path) -> tuple[int, Columns]:
    if table.num_rows == 0:
        raise QueryError("The query returned no rows.")
    # Decimal columns become doubles, matching the other connectors.
    fields = [pa.field(f.name, pa.float64()) if pa.types.is_decimal(f.type) else f for f in table.schema]
    table = table.cast(pa.schema(fields))
    dest.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, dest)
    return table.num_rows, _describe(dest)


def fetch_batches(cursor: Any, max_rows: int, batch: int = 50_000) -> tuple[list[pd.DataFrame], bool]:
    """Read an executed DB-API cursor in batches, stopping at max_rows. Returns (frames, truncated).
    Column names are read after the first fetch: server-side cursors only know them then."""
    frames: list[pd.DataFrame] = []
    fetched = 0
    while True:
        rows = cursor.fetchmany(batch)
        if not rows:
            return frames, False
        names = [d[0] for d in cursor.description or []]
        if fetched + len(rows) > max_rows:
            frames.append(pd.DataFrame.from_records(rows[: max_rows - fetched], columns=names))
            return frames, True
        frames.append(pd.DataFrame.from_records(rows, columns=names))
        fetched += len(rows)


def wrap_select(sql: str) -> str:
    """Accept one SELECT (or WITH) query and wrap it, so the warehouse rejects anything that is not a
    query: DDL and DML are not allowed inside a subquery."""
    body = sql.strip().rstrip(";").strip()
    if not body.lower().startswith(("select", "with")):
        raise QueryError("Custom extracts must be a single SELECT query.")
    if ";" in body:
        raise QueryError("Send exactly one SQL statement.")
    return f"SELECT * FROM ({body}) AS inferra_extract"
