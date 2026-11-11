"""Test doubles for the LLM layer."""

from collections.abc import Callable
from typing import Any, TypeVar

from pydantic import BaseModel

from tracker.llm.client import LLMUnavailableError

T = TypeVar("T", bound=BaseModel)


class FakeLLM:
    """Returns whatever `respond(prompt, schema)` builds; records every call."""

    model = "fake-model"

    def __init__(
        self, respond: Callable[[str, type[Any]], Any] | None = None, fail: bool = False
    ) -> None:
        self.respond = respond
        self.fail = fail
        self.calls: list[str] = []

    def parse(self, system: str, prompt: str, schema: type[T], max_tokens: int = 1024) -> T:
        self.calls.append(prompt)
        if self.fail or self.respond is None:
            raise LLMUnavailableError("fake outage")
        result: T = self.respond(prompt, schema)
        return result
