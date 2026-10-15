"""Typed tools. Agents call these; tools compute and never call an LLM.

A tool is a Pydantic input model plus a function. The model's JSON schema is what the LLM sees,
and the same model validates whatever the LLM sends back."""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from inferra.datasources.base import DataSource


class ToolError(Exception):
    """An expected failure (bad column, rejected SQL). The message is returned to the agent."""


@dataclass
class ArtifactDraft:
    kind: str
    title: str
    data: dict[str, Any]


@dataclass
class ToolOutput:
    result: Any  # compact JSON-able payload the agent reads
    artifact: ArtifactDraft | None = None
    code: str | None = None  # SQL (or equivalent) that produced the result


@dataclass
class ToolContext:
    source: DataSource
    row_limit: int = 200


@dataclass
class Tool:
    name: str
    description: str
    input_model: type[BaseModel]
    fn: Callable[[ToolContext, Any], ToolOutput]
    tags: set[str] = field(default_factory=set)

    def definition(self) -> dict[str, Any]:
        schema = self.input_model.model_json_schema()
        schema.pop("title", None)
        return {"name": self.name, "description": self.description, "input_schema": schema}

    def run(self, ctx: ToolContext, raw_input: dict[str, Any]) -> ToolOutput:
        try:
            parsed = self.input_model.model_validate(raw_input)
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(str(p) for p in e['loc']) or 'input'}: {e['msg']}" for e in exc.errors()
            )
            raise ToolError(f"Invalid input for {self.name}: {problems}") from exc
        return self.fn(ctx, parsed)


_REGISTRY: dict[str, Tool] = {}


def tool(name: str, description: str, input_model: type[BaseModel]):  # type: ignore[no-untyped-def]
    def register(fn: Callable[[ToolContext, Any], ToolOutput]) -> Callable[[ToolContext, Any], ToolOutput]:
        _REGISTRY[name] = Tool(name=name, description=description, input_model=input_model, fn=fn)
        return fn

    return register


def get_tool(name: str) -> Tool:
    _load_all()
    return _REGISTRY[name]


def get_tools(names: list[str]) -> list[Tool]:
    _load_all()
    return [_REGISTRY[n] for n in names]


def _load_all() -> None:
    # Importing the modules registers their tools.
    from inferra.tools import analysis, profiling  # noqa: F401


def round_floats(value: Any, digits: int = 6) -> Any:
    if isinstance(value, float):
        return round(value, digits)
    if isinstance(value, list):
        return [round_floats(v, digits) for v in value]
    if isinstance(value, dict):
        return {k: round_floats(v, digits) for k, v in value.items()}
    return value
