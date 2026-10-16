"""Snowflake connections, with a password or a key pair.

Snowflake has no read-only session switch, so use a role that can only read. On top of that,
custom SQL runs wrapped as a subquery (which rejects DDL and DML), sessions allow one statement per
call, and queries time out after two minutes."""

import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from inferra.datasources.base import QueryError
from inferra.datasources.extracts import Columns, fetch_batches, frames_to_parquet, wrap_select

TITLE = "Snowflake"
ACCOUNT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{1,254}$")
MAX_TABLES = 1000


class SnowflakeIn(BaseModel):
    kind: Literal["snowflake"]
    name: str = Field(min_length=1, max_length=200)
    account: str = Field(description="Account identifier, e.g. myorg-myaccount.")
    user: str = Field(min_length=1, max_length=200)
    warehouse: str = Field(min_length=1, max_length=255)
    database: str = Field(min_length=1, max_length=255)
    schema_name: str = Field(default="PUBLIC", alias="schema", max_length=255)
    role: str | None = Field(default=None, max_length=255)
    password: str | None = Field(default=None, max_length=500)
    private_key: str | None = Field(default=None, max_length=10000, description="PEM, unencrypted.")

    model_config = {"populate_by_name": True}

    @field_validator("account")
    @classmethod
    def _account(cls, value: str) -> str:
        # The account becomes part of a hostname, so only identifier characters are allowed.
        value = value.strip().removesuffix(".snowflakecomputing.com")
        if not ACCOUNT.match(value):
            raise ValueError("use the account identifier, e.g. myorg-myaccount")
        return value


def parse(body: SnowflakeIn) -> tuple[dict[str, Any], str]:
    if bool(body.password) == bool(body.private_key):
        raise QueryError("Provide either a password or a private key.")
    config = {
        "account": body.account,
        "user": body.user,
        "warehouse": body.warehouse,
        "database": body.database,
        "schema": body.schema_name,
        "role": body.role,
        "auth": "key_pair" if body.private_key else "password",
    }
    return config, body.private_key or body.password or ""


def _private_key(pem: str) -> bytes:
    from cryptography.hazmat.primitives import serialization

    try:
        key = serialization.load_pem_private_key(pem.encode(), password=None)
    except (ValueError, TypeError) as exc:
        raise QueryError("The private key must be an unencrypted PEM key.") from exc
    return key.private_bytes(
        serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )


def connect(config: dict[str, Any], secret: str) -> Any:
    import snowflake.connector

    auth: dict[str, Any] = (
        {"private_key": _private_key(secret)} if config.get("auth") == "key_pair" else {"password": secret}
    )
    try:
        return snowflake.connector.connect(
            account=config["account"],
            user=config["user"],
            warehouse=config["warehouse"],
            database=config["database"],
            schema=config["schema"],
            role=config.get("role") or None,
            login_timeout=15,
            network_timeout=120,
            session_parameters={
                "QUERY_TAG": "inferra",
                "STATEMENT_TIMEOUT_IN_SECONDS": 120,
                "MULTI_STATEMENT_COUNT": 1,
            },
            **auth,
        )
    except snowflake.connector.Error as exc:
        raise QueryError(f"Could not connect: {str(exc).strip().splitlines()[0][:300]}") from exc


def _run(config: dict[str, Any], secret: str, sql: str, params: tuple[Any, ...] = ()) -> list[Any]:
    import snowflake.connector

    conn = connect(config, secret)
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        return list(cur.fetchall())
    except snowflake.connector.Error as exc:
        raise QueryError(str(exc).strip().splitlines()[0][:300]) from exc
    finally:
        conn.close()


def verify(config: dict[str, Any], secret: str) -> str:
    return f"Snowflake {_run(config, secret, 'SELECT CURRENT_VERSION()')[0][0]}"


def tables(config: dict[str, Any], secret: str) -> list[dict[str, Any]]:
    rows = _run(
        config,
        secret,
        "SELECT table_schema, table_name, table_type, row_count FROM information_schema.tables "
        f"WHERE table_schema <> 'INFORMATION_SCHEMA' ORDER BY 1, 2 LIMIT {MAX_TABLES}",
    )
    return [
        {"schema": r[0], "table": r[1], "type": r[2], "estimated_rows": None if r[3] is None else int(r[3])}
        for r in rows
    ]


def _ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def source_sql(config: dict[str, Any], source: dict[str, Any]) -> str:
    if source.get("sql"):
        return wrap_select(source["sql"])
    if not source.get("table"):
        raise QueryError("Choose a table or provide a query.")
    return f"SELECT * FROM {_ident(source.get('schema') or config['schema'])}.{_ident(source['table'])}"


def extract(
    config: dict[str, Any], secret: str, source: dict[str, Any], dest: Path, max_rows: int
) -> tuple[int, Columns, bool]:
    import snowflake.connector

    sql = source_sql(config, source)
    conn = connect(config, secret)
    try:
        cur = conn.cursor()
        cur.execute(sql)
        frames, truncated = fetch_batches(cur, max_rows)
    except snowflake.connector.Error as exc:
        raise QueryError(str(exc).strip().splitlines()[0][:300]) from exc
    finally:
        conn.close()
    rows, columns = frames_to_parquet(frames, dest)
    return rows, columns, truncated
