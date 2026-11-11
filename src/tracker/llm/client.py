"""Thin wrapper around the Anthropic SDK: structured outputs, one error type, usage logs."""

from typing import Any, Protocol, TypeVar

import anthropic
from pydantic import BaseModel

from tracker.log import get_logger
from tracker.settings import get_settings

log = get_logger(__name__)
T = TypeVar("T", bound=BaseModel)


class LLMUnavailableError(RuntimeError):
    """No key, API failure, refusal or unusable output: callers should use their fallback."""


class StructuredLLM(Protocol):
    """Anything that can turn a prompt into a validated Pydantic object."""

    model: str

    def parse(self, system: str, prompt: str, schema: type[T], max_tokens: int = 1024) -> T: ...


class ClaudeLLM:
    """Claude via `messages.parse` (structured outputs validated against a Pydantic model)."""

    def __init__(self, client: Any, model: str) -> None:
        self._client = client
        self.model = model

    def parse(self, system: str, prompt: str, schema: type[T], max_tokens: int = 1024) -> T:
        """One structured request. Raises LLMUnavailableError instead of SDK errors."""
        try:
            response = self._client.messages.parse(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
                output_format=schema,
            )
        except anthropic.AuthenticationError as exc:
            raise LLMUnavailableError("invalid ANTHROPIC_API_KEY") from exc
        except anthropic.RateLimitError as exc:  # SDK already retried with backoff
            raise LLMUnavailableError("rate limited") from exc
        except anthropic.APIStatusError as exc:
            raise LLMUnavailableError(f"API error {exc.status_code}") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMUnavailableError("could not reach the API") from exc

        usage = getattr(response, "usage", None)
        log.info(
            "llm_call",
            model=self.model,
            schema=schema.__name__,
            stop_reason=response.stop_reason,
            input_tokens=getattr(usage, "input_tokens", None),
            output_tokens=getattr(usage, "output_tokens", None),
        )
        if response.stop_reason == "refusal":
            raise LLMUnavailableError("model declined the request")
        if response.stop_reason == "max_tokens":
            raise LLMUnavailableError("output truncated at max_tokens")
        parsed = response.parsed_output
        if parsed is None:
            raise LLMUnavailableError("no structured output returned")
        result: T = parsed
        return result


def default_llm() -> StructuredLLM | None:
    """Claude if ANTHROPIC_API_KEY is configured, else None (use fallbacks)."""
    settings = get_settings()
    if settings.anthropic_api_key is None or not settings.anthropic_api_key.get_secret_value():
        return None
    client = anthropic.Anthropic(
        api_key=settings.anthropic_api_key.get_secret_value(), max_retries=3
    )
    return ClaudeLLM(client, settings.claude_model)
