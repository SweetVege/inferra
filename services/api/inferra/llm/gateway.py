"""The one place Inferra talks to a language model.

Agents depend on the small `LLMClient` protocol, so tests and offline demos can swap in a scripted
client while production uses Claude through the Anthropic SDK."""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from inferra.config import get_settings

Role = Literal["planning", "specialist", "insight"]


class LLMUnavailable(Exception):
    """No model is configured, or the provider refused or failed the request."""


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    requests: int = 0

    def add(self, other: "Usage") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_read_input_tokens += other.cache_read_input_tokens
        self.cache_creation_input_tokens += other.cache_creation_input_tokens
        self.requests += other.requests

    def as_dict(self) -> dict[str, int]:
        return dict(self.__dict__)


@dataclass
class LLMResponse:
    # Content blocks exactly as returned (SDK objects or test doubles). Append them unchanged to the
    # conversation: thinking blocks must be passed back as-is.
    content: list[Any]
    stop_reason: str | None
    usage: Usage = field(default_factory=Usage)


class LLMClient(Protocol):
    def create(
        self, *, role: Role, system: str, tools: list[dict[str, Any]], messages: list[dict[str, Any]]
    ) -> LLMResponse: ...


def tool_uses(response: LLMResponse) -> list[Any]:
    return [b for b in response.content if getattr(b, "type", None) == "tool_use"]


def text_of(response: LLMResponse) -> str:
    return "\n".join(b.text for b in response.content if getattr(b, "type", None) == "text").strip()


class AnthropicLLM:
    def __init__(self, model: str):
        import anthropic

        self._anthropic = anthropic
        self._client = anthropic.Anthropic()
        self.model = model

    def create(
        self, *, role: Role, system: str, tools: list[dict[str, Any]], messages: list[dict[str, Any]]
    ) -> LLMResponse:
        s = get_settings()
        effort = {
            "planning": s.llm_effort_planning,
            "specialist": s.llm_effort_specialist,
            "insight": s.llm_effort_insight,
        }[role]
        try:
            # Streaming avoids HTTP timeouts on long agent turns.
            with self._client.beta.messages.stream(
                model=self.model,
                max_tokens=s.llm_max_tokens,
                system=system,
                tools=tools,
                messages=messages,
                output_config={"effort": effort},
                cache_control={"type": "ephemeral"},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            ) as stream:
                message = stream.get_final_message()
        except self._anthropic.APIStatusError as exc:
            raise LLMUnavailable(f"Model request failed ({exc.status_code}): {exc.message}") from exc
        except self._anthropic.APIConnectionError as exc:
            raise LLMUnavailable("Could not reach the model provider.") from exc
        if message.stop_reason == "refusal":
            raise LLMUnavailable("The model declined this request.")
        u = message.usage
        usage = Usage(
            input_tokens=u.input_tokens or 0,
            output_tokens=u.output_tokens or 0,
            cache_read_input_tokens=u.cache_read_input_tokens or 0,
            cache_creation_input_tokens=u.cache_creation_input_tokens or 0,
            requests=1,
        )
        return LLMResponse(content=list(message.content), stop_reason=message.stop_reason, usage=usage)


_override: Callable[[], LLMClient] | None = None


def set_llm_factory(factory: Callable[[], LLMClient] | None) -> None:
    """Install a different client factory (tests, offline demo seeding)."""
    global _override
    _override = factory


def llm_available() -> bool:
    return _override is not None or bool(get_settings().llm_model)


def get_llm() -> LLMClient:
    if _override is not None:
        return _override()
    model = get_settings().llm_model
    if not model:
        raise LLMUnavailable("No model configured. Set INFERRA_LLM_MODEL and ANTHROPIC_API_KEY.")
    return AnthropicLLM(model)
