from __future__ import annotations

_CHARS = 4


class PromptOptimizer:
    def __init__(self, max_tokens: int = 4000) -> None:
        self._max_chars = max_tokens * _CHARS

    def optimize(self, prompt: str) -> str:
        if len(prompt) <= self._max_chars:
            return prompt
        return prompt[: self._max_chars - 20] + "\n...[truncated]"
