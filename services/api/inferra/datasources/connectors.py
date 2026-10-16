"""Database connectors. Each one validates its settings, checks credentials, lists tables, and
extracts a table or a read-only query into Parquet. Everything downstream (profiling, agents, data
models) works on the extract, so adding a warehouse only means adding a module here."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Annotated, Any

from pydantic import BaseModel, Field, TypeAdapter

from inferra.datasources import bigquery, postgres, snowflake
from inferra.datasources.base import QueryError
from inferra.datasources.extracts import Columns


@dataclass(frozen=True)
class Connector:
    kind: str
    title: str
    parse: Callable[[Any], tuple[dict[str, Any], str]]
    verify: Callable[[dict[str, Any], str], str]
    tables: Callable[[dict[str, Any], str], list[dict[str, Any]]]
    extract: Callable[[dict[str, Any], str, dict[str, Any], Path, int], tuple[int, Columns, bool]]


def _from(module: ModuleType, kind: str) -> Connector:
    return Connector(kind, module.TITLE, module.parse, module.verify, module.tables, module.extract)


CONNECTORS: dict[str, Connector] = {
    "postgres": _from(postgres, "postgres"),
    "bigquery": _from(bigquery, "bigquery"),
    "snowflake": _from(snowflake, "snowflake"),
}

ConnectionInput = Annotated[
    postgres.PostgresIn | bigquery.BigQueryIn | snowflake.SnowflakeIn, Field(discriminator="kind")
]
_ADAPTER: TypeAdapter[Any] = TypeAdapter(ConnectionInput)


def parse_input(body: dict[str, Any]) -> tuple[str, str, dict[str, Any], str]:
    """Validate a create-connection body. Returns (kind, name, config, secret)."""
    data = {"kind": "postgres", **body}  # earlier clients only knew Postgres
    parsed: BaseModel = _ADAPTER.validate_python(data)
    kind = str(data["kind"])
    config, secret = CONNECTORS[kind].parse(parsed)
    return kind, str(parsed.name), config, secret


def connector(kind: str) -> Connector:
    if kind not in CONNECTORS:
        raise QueryError(f"Unsupported connection type '{kind}'.")
    return CONNECTORS[kind]
