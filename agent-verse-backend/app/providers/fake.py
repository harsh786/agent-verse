"""FakeProvider — deterministic LLM provider for tests.

Returns scripted responses from a list (cycling when exhausted).
Records every call in ``call_history`` for assertion in tests.
"""

from __future__ import annotations

import math

from app.providers.base import (
    CompletionRequest,
    CompletionResponse,
    EmbedRequest,
    EmbedResponse,
)


class FakeProvider:
    """Deterministic test double for any LLMProvider.

    Args:
        responses: Scripted text replies, cycled in order.
        embed_dim: Dimensionality of fake embedding vectors.
        vision: Whether the fake claims vision support.
        tool_use: Whether the fake claims tool-use support.
    """

    def __init__(
        self,
        *,
        responses: list[str] | None = None,
        embed_dim: int = 1024,
        vision: bool = False,
        tool_use: bool = True,
    ) -> None:
        self._responses = responses or ["I am a fake LLM response."]
        self._embed_dim = embed_dim
        self._vision = vision
        self._tool_use = tool_use
        self._call_index = 0
        self.call_history: list[CompletionRequest] = []

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        self.call_history.append(request)
        text = self._responses[self._call_index % len(self._responses)]
        self._call_index += 1
        return CompletionResponse(
            content=text,
            model=request.model,
            input_tokens=10,
            output_tokens=len(text.split()),
        )

    async def embed(self, request: EmbedRequest) -> EmbedResponse:
        embeddings = [
            # Simple deterministic vector: each dim is sin(i + text_index)
            [math.sin(i + idx) for i in range(self._embed_dim)]
            for idx, _ in enumerate(request.texts)
        ]
        return EmbedResponse(
            embeddings=embeddings,
            model=request.model,
            total_tokens=sum(len(t.split()) for t in request.texts),
        )

    def supports_vision(self) -> bool:
        return self._vision

    def supports_tool_use(self) -> bool:
        return self._tool_use
