"""The contract the rest of the app expects from a language-model client.

Services depend on this protocol rather than on ``GeminiClient``, so tests (and
a future second provider) can swap in anything with the same three methods.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel


@runtime_checkable
class LLMClient(Protocol):
    model: str

    def generate(
        self,
        contents: Any,
        *,
        system_instruction: str | None = None,
        temperature: float = 0.7,
        max_output_tokens: int = 800,
        response_schema: type[BaseModel] | None = None,
    ) -> str | None:
        """Return the model's text, or ``None`` if anything went wrong.

        ``contents`` may be a string or provider-specific parts (images), which
        is how vision requests are made.
        """
        ...

    def embed(
        self, texts: list[str], *, task_type: str = "RETRIEVAL_DOCUMENT"
    ) -> list[list[float]] | None:
        """Return one vector per input text, or ``None`` if the call failed."""
        ...
