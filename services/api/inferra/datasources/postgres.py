"""Postgres connections: test, list tables, and pull extracts into Parquet.

Every session is read-only (`default_transaction_read_only`) with a statement timeout. Custom SQL
runs as a subquery on a server-side cursor, which accepts exactly one SELECT. Extracts land in the
workspace's storage, so every analysis tool works on them exactly as on uploaded files."""

import base64
import decimal
import hashlib
import ipaddress
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
from cryptography.fernet import Fernet, InvalidToken

from inferra.config import get_settings
from inferra.datasources.base import QueryError

BATCH = 50_000


def _fernet() -> Fernet:
    digest = hashlib.sha256((get_settings().secret_key + ":connections").encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(secret: str) -> str:
    return _fernet().encrypt(secret.encode()).decode()


def decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise QueryError("Stored credentials can no longer be decrypted; re-enter the password.") from exc


@dataclass
class PgConfig:
    host: str
    port: int
    database: str
    user: str
    sslmode: str = "prefer"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PgConfig":
        return cls(
            host=str(data["host"]),
            port=int(data.get("port") or 5432),
            database=str(data["database"]),
            user=str(data["user"]),
            sslmode=str(data.get("sslmode") or "prefer"),
        )


def check_host(host: str) -> None:
    """Outside development, refuse private and loopback addresses so a connection cannot be used
    to reach the service's own network."""
    if get_settings().allow_private_db_hosts:
        return
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise QueryError(f"Could not resolve host '{host}'.") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise QueryError("Private and loopback database hosts are not allowed.")


def connect(config: PgConfig, password: str) -> Any:
    import psycopg

    check_host(config.host)
    try:
        return psycopg.connect(
            host=config.host,
            port=config.port,
            dbname=config.database,
            user=config.user,
            password=password,
            sslmode=config.sslmode,
            connect_timeout=8,
            application_name="inferra",
            options="-c default_transaction_read_only=on -c statement_timeout=120000",
        )
    except psycopg.Error as exc:
        raise QueryError(f"Could not connect: {str(exc).strip().splitlines()[0]}") from exc


def list_tables(config: PgConfig, password: str) -> list[dict[str, Any]]:
    with connect(config, password) as conn:
        rows = conn.execute(
            """
            SELECT t.table_schema, t.table_name, t.table_type,
                   GREATEST(coalesce(c.reltuples, 0), 0)::bigint AS estimated_rows
            FROM information_schema.tables t
            LEFT JOIN pg_namespace n ON n.nspname = t.table_schema
            LEFT JOIN pg_class c ON c.relname = t.table_name AND c.relnamespace = n.oid
            WHERE t.table_schema NOT IN ('pg_catalog', 'information_schema')
            ORDER BY 1, 2
            """
        ).fetchall()
    return [{"schema": r[0], "table": r[1], "type": r[2], "estimated_rows": int(r[3])} for r in rows]


def server_version(config: PgConfig, password: str) -> str:
    with connect(config, password) as conn:
        return str(conn.execute("SHOW server_version").fetchone()[0])  # type: ignore[index]


def source_sql(schema: str | None, table: str | None, sql: str | None) -> Any:
    from psycopg import sql as pgsql

    if sql:
        body = sql.strip().rstrip(";")
        if not body.lower().startswith(("select", "with")):
            raise QueryError("Custom extracts must be a single SELECT query.")
        return pgsql.SQL("SELECT * FROM ({}) AS inferra_extract").format(pgsql.SQL(body))  # type: ignore[arg-type]
    if not table:
        raise QueryError("Choose a table or provide a query.")
    return pgsql.SQL("SELECT * FROM {}.{}").format(
        pgsql.Identifier(schema or "public"), pgsql.Identifier(table)
    )


def extract(
    config: PgConfig, password: str, query: Any, dest: Path, max_rows: int
) -> tuple[int, list[dict[str, str]], bool]:
    """Stream a query into a Parquet file. Returns (rows, columns, truncated)."""
    import psycopg

    frames: list[pd.DataFrame] = []
    fetched = 0
    truncated = False
    try:
        with connect(config, password) as conn, conn.cursor(name="inferra_extract") as cur:
            cur.execute(query)
            names: list[str] | None = None
            while True:
                rows = cur.fetchmany(BATCH)
                if names is None and cur.description:
                    names = [d.name for d in cur.description]
                if not rows:
                    break
                if fetched + len(rows) > max_rows:
                    rows = rows[: max_rows - fetched]
                    truncated = True
                frames.append(pd.DataFrame.from_records(rows, columns=names))
                fetched += len(rows)
                if truncated:
                    break
    except psycopg.Error as exc:
        raise QueryError(str(exc).strip().splitlines()[0]) from exc
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
        described = con.execute(f"DESCRIBE SELECT * FROM read_parquet('{path}')").fetchall()
    finally:
        con.close()
    return len(frame), [{"name": r[0], "type": r[1]} for r in described], truncated
