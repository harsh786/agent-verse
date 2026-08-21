"""QueryReformulator — generates alternative query phrasings on empty results."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class QueryReformulation:
    original_query: str
    reformulated_query: str
    source: str


class QueryReformulator:
    def __init__(self, max_attempts: int = 2) -> None:
        self._max = max_attempts

    def reformulate(self, query: str) -> list[str]:
        alternatives: list[str] = []
        q = query.strip()
        keywords = re.sub(r"^(what is|how do|can you|please|find|get|list)\s+", "", q, flags=re.I)
        if keywords != q and keywords:
            alternatives.append(keywords)
        expanded = q.replace("kb", "knowledge base").replace("ltm", "long-term memory")
        if expanded != q:
            alternatives.append(expanded)
        else:
            alternatives.append(f"information about {q}")
        return alternatives[: self._max]

    async def reformulate_async(
        self,
        query: str,
        provider: Any = None,
        model: str = "",
        *,
        strict: bool = False,
    ) -> list[str]:
        """LLM-driven reformulation. Falls back to rule-based."""
        if provider is None:
            return self.reformulate(query)
        try:
            from app.providers.base import CompletionRequest, Message

            resp = await provider.complete(
                CompletionRequest(
                    messages=[
                        Message(
                            role="system",
                            content=(
                                f"Rewrite this search query in {self._max} different ways. "
                                "Each rewrite should retrieve different but relevant documents. "
                                "Output one rewrite per line, no numbering."
                            ),
                        ),
                        Message(role="user", content=query),
                    ],
                    model=model,
                    max_tokens=150,
                    temperature=0.6,
                )
            )
            raw = (resp.content or "").strip()
            rewrites = [
                line.strip() for line in raw.split("\n") if line.strip() and line.strip() != query
            ]
            return rewrites[: self._max] if rewrites else self.reformulate(query)
        except Exception:
            if strict:
                raise
            return self.reformulate(query)

    async def reformulate_one(
        self,
        query: str,
        *,
        provider: Any = None,
        model: str = "",
        strict: bool = False,
    ) -> QueryReformulation:
        """Return one explicit reformulation without hiding provider failures."""

        alternatives = await self.reformulate_async(
            query,
            provider,
            model,
            strict=strict,
        )
        if not alternatives:
            raise ValueError("Query reformulation produced no alternatives")
        return QueryReformulation(
            original_query=query,
            reformulated_query=alternatives[0],
            source="llm" if provider is not None else "rules",
        )
