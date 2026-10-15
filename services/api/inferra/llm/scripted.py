"""A deterministic LLM double for tests and the offline demo seed.

A script is a function that sees the request and returns the next assistant turn, so a test can
describe agent behavior ("on the planning call, submit this plan") without network access."""

import itertools
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from inferra.llm.gateway import LLMResponse, Role, Usage

_ids = itertools.count(1)


@dataclass
class TextBlock:
    text: str
    type: str = "text"


@dataclass
class ToolUseBlock:
    name: str
    input: dict[str, Any]
    id: str = ""
    type: str = "tool_use"

    def __post_init__(self) -> None:
        if not self.id:
            self.id = f"toolu_{next(_ids):04d}"


@dataclass
class Request:
    role: Role
    system: str
    tools: list[dict[str, Any]]
    messages: list[dict[str, Any]]

    @property
    def tool_names(self) -> set[str]:
        return {t["name"] for t in self.tools}

    def last_tool_results(self) -> list[dict[str, Any]]:
        last = self.messages[-1]
        if last["role"] != "user" or isinstance(last["content"], str):
            return []
        return [b for b in last["content"] if b.get("type") == "tool_result"]

    @property
    def first_user_text(self) -> str:
        content = self.messages[0]["content"]
        return content if isinstance(content, str) else str(content)


Script = Callable[[Request], list[Any]]


class ScriptedLLM:
    def __init__(self, script: Script):
        self.script = script
        self.requests: list[Request] = []

    def create(
        self, *, role: Role, system: str, tools: list[dict[str, Any]], messages: list[dict[str, Any]]
    ) -> LLMResponse:
        request = Request(role=role, system=system, tools=tools, messages=list(messages))
        self.requests.append(request)
        blocks = self.script(request)
        stop = "tool_use" if any(getattr(b, "type", "") == "tool_use" for b in blocks) else "end_turn"
        # A scripted turn costs no tokens; it still counts as one request.
        return LLMResponse(content=blocks, stop_reason=stop, usage=Usage(requests=1))
