"""ContextBudget — enforces token limits on retrieved context."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Approximate: 1 token ≈ 4 characters
_CHARS_PER_TOKEN = 4


@dataclass
class BudgetResult:
    included_chunks: list[dict[str, Any]]
    excluded_count: int
    total_tokens: int


class ContextBudget:
    def __init__(self, max_tokens: int = 6000, max_chunks: int = 20) -> None:
        self._max_tokens = max_tokens
        self._max_chunks = max_chunks

    def apply(self, chunks: list[dict[str, Any]]) -> BudgetResult:
        included = []
        token_count = 0

        for chunk in chunks:
            content = chunk.get("content", "")
            chunk_tokens = max(1, len(content) // _CHARS_PER_TOKEN)

            if len(included) >= self._max_chunks:
                break
            if token_count + chunk_tokens > self._max_tokens and included:
                # Skip this oversized chunk but keep filling — a single large
                # chunk must not discard every later chunk that would still fit.
                continue

            included.append(chunk)
            token_count += chunk_tokens

        return BudgetResult(
            included_chunks=included,
            excluded_count=len(chunks) - len(included),
            total_tokens=token_count,
        )
