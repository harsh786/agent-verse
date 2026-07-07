from __future__ import annotations

_CHARS_PER_TOKEN = 4


class TokenOptimizer:
    def __init__(self, max_tokens: int = 4000) -> None:
        self._max_chars = max_tokens * _CHARS_PER_TOKEN

    def compress(self, text: str) -> str:
        if len(text) <= self._max_chars:
            return text
        return text[: self._max_chars - 20] + "\n...[truncated for token budget]"
