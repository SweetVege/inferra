"""Typed tools. Agents call these; tools compute and never call an LLM.

A tool is a Pydantic input model plus a function. The model's JSON schema is what the LLM sees,
and the same model validates whatever the LLM sends back."""

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np
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
class ModelDraft:
    """A trained model for the registry: metadata plus a serialized, self-contained payload."""

    name: str
    task: str
    target: str
    table: str
    features: list[str]
    metrics: dict[str, Any]
    card: dict[str, Any]
    payload: str


@dataclass
class ProjectDraft:
    """A data model built in a run: saved as a draft for people to review, edit, and publish."""

    name: str
    description: str
    nodes: list[dict[str, Any]]
    build: dict[str, Any]


@dataclass
class ToolOutput:
    result: Any  # compact JSON-able payload the agent reads
    artifact: ArtifactDraft | None = None
    code: str | None = None  # SQL (or equivalent) that produced the result
    model: ModelDraft | None = None
    project: ProjectDraft | None = None


@dataclass
class ToolContext:
    source: DataSource
    row_limit: int = 200
    metrics: dict[str, dict[str, Any]] = field(default_factory=dict)  # defined metrics by name


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
    from inferra.tools import (  # noqa: F401
        analysis,
        datamodel,
        experiment,
        metrics,
        ml,
        modeling,
        profiling,
    )


def round_floats(value: Any, digits: int = 8) -> Any:
    """Round to significant digits, so tiny p-values keep their magnitude. Also turns numpy
    scalars into plain Python values so artifacts serialize as JSON."""
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float):
        return float(f"{value:.{digits}g}") if math.isfinite(value) else value
    if isinstance(value, list):
        return [round_floats(v, digits) for v in value]
    if isinstance(value, dict):
        return {k: round_floats(v, digits) for k, v in value.items()}
    return value
