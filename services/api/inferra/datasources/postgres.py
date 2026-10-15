"""Postgres connections: test, list tables, and pull extracts into Parquet.

Every session is read-only (`default_transaction_read_only`) with a statement timeout. Custom SQL
runs as a subquery on a server-side cursor, which accepts exactly one SELECT. Extracts land in the
workspace's storage, so every analysis tool works on them exactly as on uploaded files."""

import ipaddress
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from inferra.config import get_settings
from inferra.datasources.base import QueryError
from inferra.datasources.extracts import Columns, fetch_batches, frames_to_parquet, wrap_select

TITLE = "PostgreSQL"


class PostgresIn(BaseModel):
    kind: Literal["postgres"] = "postgres"
    name: str = Field(min_length=1, max_length=200)
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=5432, ge=1, le=65535)
    database: str = Field(min_length=1, max_length=200)
    user: str = Field(min_length=1, max_length=200)
    password: str = Field(max_length=500)
    sslmode: Literal["disable", "prefer", "require", "verify-ca", "verify-full"] = "prefer"


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


def parse(body: PostgresIn) -> tuple[dict[str, Any], str]:
    return PgConfig(body.host, body.port, body.database, body.user, body.sslmode).__dict__, body.password


def verify(config: dict[str, Any], secret: str) -> str:
    return f"PostgreSQL {server_version(PgConfig.from_dict(config), secret)}"


def tables(config: dict[str, Any], secret: str) -> list[dict[str, Any]]:
    return list_tables(PgConfig.from_dict(config), secret)


def source_sql(schema: str | None, table: str | None, sql: str | None) -> Any:
    from psycopg import sql as pgsql

    if sql:
        return pgsql.SQL(wrap_select(sql))  # type: ignore[arg-type]
    if not table:
        raise QueryError("Choose a table or provide a query.")
    return pgsql.SQL("SELECT * FROM {}.{}").format(
        pgsql.Identifier(schema or "public"), pgsql.Identifier(table)
    )


def extract(
    config: dict[str, Any], secret: str, source: dict[str, Any], dest: Path, max_rows: int
) -> tuple[int, Columns, bool]:
    """Stream a table or query through a server-side cursor into Parquet. Returns (rows, columns,
    truncated). A named cursor accepts exactly one statement, and the session is read-only."""
    import psycopg

    query = source_sql(source.get("schema"), source.get("table"), source.get("sql"))
    try:
        with connect(PgConfig.from_dict(config), secret) as conn, conn.cursor(name="inferra_extract") as cur:
            cur.execute(query)
            frames, truncated = fetch_batches(cur, max_rows)
    except psycopg.Error as exc:
        raise QueryError(str(exc).strip().splitlines()[0]) from exc
    rows, columns = frames_to_parquet(frames, dest)
    return rows, columns, truncated
