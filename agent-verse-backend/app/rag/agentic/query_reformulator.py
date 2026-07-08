"""QueryReformulator — generates alternative query phrasings on empty results."""
from __future__ import annotations
import re
from typing import Any


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
    ) -> list[str]:
        """LLM-driven reformulation. Falls back to rule-based."""
        if provider is None:
            return self.reformulate(query)
        try:
            from app.providers.base import CompletionRequest, Message
            resp = await provider.complete(CompletionRequest(
                messages=[
                    Message(role="system", content=(
                        f"Rewrite this search query in {self._max} different ways. "
                        "Each rewrite should retrieve different but relevant documents. "
                        "Output one rewrite per line, no numbering."
                    )),
                    Message(role="user", content=query),
                ],
                model="",
                max_tokens=150,
                temperature=0.6,
            ))
            raw = (resp.content or "").strip()
            rewrites = [
                line.strip() for line in raw.split("\n")
                if line.strip() and line.strip() != query
            ]
            return rewrites[:self._max] if rewrites else self.reformulate(query)
        except Exception:
            return self.reformulate(query)

