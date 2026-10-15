from dataclasses import dataclass, field
from typing import Any, Protocol


class QueryError(Exception):
    """Raised for SQL that is rejected or fails; the message is safe to show an agent."""


@dataclass
class TableInfo:
    name: str
    columns: list[dict[str, str]]
    row_count: int | None = None
    description: str | None = None


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[dict[str, Any]]
    truncated: bool = False
    sql: str = ""
    types: dict[str, str] = field(default_factory=dict)


class DataSource(Protocol):
    """Every analysis tool talks to data through this interface, so the same tool code works on
    uploaded files today and on warehouse connections later."""

    def list_tables(self) -> list[TableInfo]: ...

    def query(self, sql: str, limit: int | None = None) -> QueryResult:
        """Run a single read-only SELECT statement."""
        ...

    def quote(self, identifier: str) -> str: ...
